"""Interrupted canonical gate publications leave a receipt for reconciliation."""

from contextlib import redirect_stdout
import fcntl
from io import StringIO
import json
import os
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.test_factory import F, GateFixture, REPO


class InterruptedPublication(Exception):
    pass


class GatePublication(GateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.job_id, self.d, self.wt = self.make_job("- src/a.py")
        self.commit(self.wt, "src/a.py", "x = 2\n")
        self.state = self.d / "job.json"
        self.marker = self.d / ".gate-publication.pending"
        self.receipt = self.d / "round-1" / "checks.md"

    def invoke(self, command, *args):
        main = runpy.run_path(str(REPO / "factory" / "bin" / command))["main"]
        out = StringIO()
        with patch.object(F, "repo_root", return_value=self.root), redirect_stdout(out):
            status = main(list(args))
        return status, out.getvalue()

    def assert_pending_blocks_gates(self):
        before = {p: p.read_bytes() for p in (self.state, self.marker, self.receipt) if p.is_file()}
        for args in ((), ("--final",), ()):
            status, output = self.invoke("gate", self.job_id, *args)
            self.assertEqual(status, 1, output)
            self.assertIn("publication interrupted", output)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertFalse((self.d / "final-checks.md").exists())

    def test_interrupted_state_write_leaves_blocking_marker(self):
        before = self.state.read_bytes()
        with patch.object(F, "write_job", side_effect=InterruptedPublication):
            with self.assertRaises(InterruptedPublication):
                self.invoke("gate", self.job_id)

        self.assertTrue(self.marker.is_file())
        self.assertIn(b"GATE: PASS", self.receipt.read_bytes())
        self.assertEqual(self.state.read_bytes(), before)
        self.assert_pending_blocks_gates()

        # The controller remains in gate. The owner can discard the tentative
        # receipt and clear the stop after inspecting both files.
        self.receipt.unlink()
        self.marker.unlink()
        status, output = self.invoke("gate", self.job_id)
        self.assertEqual((status, output), (0, f"{self.job_id} -> review: gate PASS\n"))
        self.assertFalse(self.marker.exists())

    def test_marker_is_durable_before_a_receipt_is_attempted(self):
        before = self.state.read_bytes()
        atomic_write = getattr(F, "atomic_write", None)

        def interrupt_receipt(path, content):
            if path == self.receipt:
                self.assertTrue(self.marker.is_file())
                raise InterruptedPublication
            atomic_write(path, content)

        with patch.object(F, "atomic_write", side_effect=interrupt_receipt, create=True):
            with self.assertRaises(InterruptedPublication):
                self.invoke("gate", self.job_id)

        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(self.receipt.exists())
        self.assert_pending_blocks_gates()

    def test_marker_survives_committed_state_until_unlink_succeeds(self):
        with patch.object(F, "remove_marker", side_effect=InterruptedPublication, create=True):
            with self.assertRaises(InterruptedPublication):
                self.invoke("gate", self.job_id)

        self.assertEqual(F.read_job(self.d)["stage"], "review")
        self.assertIn(b"GATE: PASS", self.receipt.read_bytes())
        self.assert_pending_blocks_gates()

        report = subprocess.run([sys.executable, str(self.root / "factory" / "bin" / "scorecard"), "--json"],
                                cwd=self.root, capture_output=True, text=True, check=True)
        cards = [card for card in json.loads(report.stdout).values() if "first_pass_gate" in card]
        self.assertTrue(cards)
        self.assertTrue(all(card["first_pass_gate"] is None for card in cards))

    def test_pending_marker_refuses_route_and_job_before_side_effects(self):
        self.marker.write_text("round-1\n")
        before = self.state.read_bytes()
        self.receipt.parent.mkdir()
        self.receipt.write_bytes(b"Unreconciled receipt\n")
        operations = [("route",), ("job", "record", "owner_minutes", "30"),
                      ("job", "advance", "review"), ("job", "pr"), ("job", "reject")]
        with (patch.object(F, "run") as run, patch.object(F, "gh_api", return_value={"number": 3}) as gh,
              patch.object(sys, "stdin", StringIO("controlled reason"))):
            for command, *args in operations:
                with self.subTest(command=(command, *args)):
                    head, tail = (args[:1], args[1:]) if command == "job" else ([], [])
                    status, output = self.invoke(command, *head, self.job_id, *tail)
                    self.assertEqual(status, 1, output)
                    self.assertIn("publication interrupted", output)
        run.assert_not_called()
        gh.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.receipt.read_bytes(), b"Unreconciled receipt\n")
        self.assertFalse((self.d / "rejection.md").exists())

    def test_malformed_revision_refuses_job_before_push_or_publication(self):
        job = F.read_job(self.d)
        job["revision"] = False
        self.state.write_text(json.dumps(job) + "\n")
        before = self.state.read_bytes()
        with patch.object(F, "run") as git, patch.object(F, "gh_api", return_value={"number": 3}) as gh:
            status, output = self.invoke("job", "pr", self.job_id)
        self.assertEqual(status, 1, output)
        self.assertIn("invalid job revision", output)
        git.assert_not_called()
        gh.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)

    def test_dangling_pending_marker_refuses_publication(self):
        self.marker.symlink_to(self.d / "absent")
        status, output = self.invoke("gate", self.job_id)
        self.assertEqual(status, 1, output)
        self.assertIn("publication interrupted", output)
        self.assertTrue(self.marker.is_symlink())
        self.assertFalse(self.receipt.exists())

    def test_gate_refuses_round_directory_symlink(self):
        outside = self.root / "outside-round"
        outside.mkdir()
        self.receipt.parent.symlink_to(outside, target_is_directory=True)
        status, output = self.invoke("gate", self.job_id)
        self.assertEqual(status, 1, output)
        self.assertFalse((outside / "checks.md").exists())
        self.assertFalse(self.marker.exists())

    def test_gate_refuses_state_symlink(self):
        outside = self.root / "outside-job.json"
        self.state.rename(outside)
        self.state.symlink_to(outside)
        status, output = self.invoke("gate", self.job_id)
        self.assertEqual(status, 1, output)
        self.assertTrue(self.state.is_symlink())
        self.assertFalse(self.receipt.exists())

    def test_gate_refuses_to_replace_a_job_that_changed_during_checks(self):
        real_run = subprocess.run

        def record_while_checking(command, *args, **kwargs):
            if command[:2] == ["bash", str(self.root / "factory" / "checks.sh")]:
                status, output = self.invoke("job", "record", self.job_id, "owner_minutes", "17")
                self.assertEqual(status, 0, output)
            return real_run(command, *args, **kwargs)

        with patch.object(subprocess, "run", side_effect=record_while_checking):
            status, output = self.invoke("gate", self.job_id)
        self.assertEqual(status, 1, output)
        self.assertIn("job changed while the gate ran", output)
        self.assertEqual(F.read_job(self.d)["owner_minutes"], 17)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.receipt.exists())

    def test_final_gate_replaces_receipt_atomically_without_advancing_legacy_job(self):
        job = F.read_job(self.d)
        job.pop("revision", None)
        self.state.write_text(json.dumps(job) + "\n")
        before = self.state.read_bytes()
        outside = self.root / "outside-receipt"
        outside.write_bytes(b"Do not write here\n")
        final_receipt = self.d / "final-checks.md"
        final_receipt.symlink_to(outside)

        self.assertEqual(self.invoke("gate", self.job_id, "--final"),
                         (0, f"{self.job_id} final gate PASS\n"))
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(outside.read_bytes(), b"Do not write here\n")
        self.assertFalse(final_receipt.is_symlink())
        self.assertFalse(self.marker.exists())
        self.assertIn(b"GATE: PASS", final_receipt.read_bytes())
        self.assertEqual(self.invoke("gate", self.job_id), (0, f"{self.job_id} -> review: gate PASS\n"))
        self.assertEqual(F.read_job(self.d)["stage"], "review")

    def test_merge_refuses_before_git_when_another_gate_owns_its_worktree(self):
        job = F.read_job(self.d)
        job["stage"] = "merging"
        F.write_job(self.d, job)
        before = self.state.read_bytes()
        fd = os.open(self.wt, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(F, "fetch_base", side_effect=F.FactoryError("already reached Git")) as fetch:
                status, output = self.invoke("job", "merge", self.job_id)
            self.assertEqual(status, 1, output)
            self.assertIn("another gate is running", output)
            fetch.assert_not_called()
        finally:
            os.close(fd)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse((self.d / "final-checks.md").exists())

    def test_merge_still_runs_final_gate_and_applies_existing_allowlist(self):
        job = F.read_job(self.d)
        job["stage"] = "merging"
        F.write_job(self.d, job)
        with patch.object(F, "gh_api", return_value={}):
            status, output = self.invoke("job", "merge", self.job_id)
        self.assertEqual(status, 0, output)
        self.assertIn("needs-human", output)
        self.assertEqual(F.read_job(self.d)["stage"], "needs-human")
        self.assertIn("GATE: PASS", (self.d / "final-checks.md").read_text())

    def test_merge_excludes_another_job_on_same_worktree_during_sync_and_decision(self):
        job = F.read_job(self.d)
        job["stage"] = "merging"
        F.write_job(self.d, job)
        other = F.new_job("002-other", 2, "Other")
        other["worktree"] = job["worktree"]
        other_dir = self.d.parent / other["id"]
        other_dir.mkdir()
        F.write_job(other_dir, other)
        (other_dir / "spec.md").write_bytes((self.d / "spec.md").read_bytes())

        attempts = {}
        gate = self.root / "factory" / "bin" / "gate"

        def competing_gate(phase):
            if phase not in attempts:
                attempts[phase] = subprocess.run([sys.executable, str(gate), other["id"], "--final"],
                                                 cwd=self.root, capture_output=True, text=True)

        fetch_base = F.fetch_base

        def fetch_during_merge(cwd, base):
            competing_gate("sync")
            return fetch_base(cwd, base)

        def github_during_decision(*args, **kwargs):
            competing_gate("decision")
            return {}

        with (patch.object(F, "fetch_base", side_effect=fetch_during_merge),
              patch.object(F, "gh_api", side_effect=github_during_decision)):
            status, output = self.invoke("job", "merge", self.job_id)
        self.assertEqual(status, 0, output)
        self.assertEqual(set(attempts), {"sync", "decision"})
        for phase, result in attempts.items():
            with self.subTest(phase=phase):
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("another gate is running", result.stdout)
        self.assertFalse((other_dir / "final-checks.md").exists())
