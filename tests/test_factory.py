"""Tests for the factory spine: state moves, parsing, path rules, and the gate end to end."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "factory" / "lib"))
import factory as F  # noqa: E402


class TestMoves(unittest.TestCase):
    def test_legal_and_illegal_moves(self):
        job = F.new_job("001-x", 1, "x")
        F.move(job, "spec")
        self.assertEqual(job["stage"], "spec")
        with self.assertRaises(F.FactoryError):
            F.move(job, "merged")

    def test_terminal_stages_have_no_exits(self):
        for stage in F.TERMINAL:
            self.assertEqual(F.TRANSITIONS[stage], set())

    def test_every_target_is_a_known_stage(self):
        for targets in F.TRANSITIONS.values():
            self.assertTrue(targets <= set(F.TRANSITIONS))

    def test_rounds_then_recovery(self):
        job = F.new_job("001-x", 1, "x")
        job.update(stage="gate", round=1)
        F.next_round_or_recovery(job, 2, "gate failed")
        self.assertEqual((job["stage"], job["round"]), ("build", 2))
        job["stage"] = "gate"
        F.next_round_or_recovery(job, 2, "gate failed")
        self.assertEqual((job["stage"], job["round"]), ("recovery", 2))


class TestParsing(unittest.TestCase):
    INTAKE = "DECISION: ACCEPT\nRISK: low\nSURFACES: eval, security\nRESCOPED: no\n\n## Summary\n- ok\n"

    def test_header(self):
        h = F.parse_header(self.INTAKE)
        self.assertEqual(h["DECISION"], "ACCEPT")
        self.assertEqual(F.parse_surfaces(h["SURFACES"]), ["eval", "security"])
        self.assertEqual(F.parse_surfaces("none"), [])

    def test_body_after_header(self):
        self.assertTrue(F.body_after_header(self.INTAKE).startswith("## Summary"))
        self.assertTrue(F.body_after_header("ESCALATE\n\n## Why\nx").startswith("## Why"))

    def test_allowed_files(self):
        spec = "## Files\n- `src/a.py`\n- tests/test_a.py\n* docs/\n\n## Assumptions\n- none\n"
        self.assertEqual(F.allowed_files(spec), ["src/a.py", "tests/test_a.py", "docs/"])
        self.assertEqual(F.allowed_files("## Behaviour\nx"), [])

    def test_job_id(self):
        self.assertEqual(F.make_job_id(7, "Load the Twitter dataset!"), "007-load-the-twitter-dataset")


class TestPathRules(unittest.TestCase):
    def test_protected(self):
        prot = ["factory/", ".claude/", "AGENTS.md"]
        self.assertEqual(F.protected_violations(["factory/checks.sh", "src/x.py", "AGENTS.md"], prot),
                         ["factory/checks.sh", "AGENTS.md"])

    def test_scope(self):
        self.assertEqual(F.scope_violations(["src/a.py", "src/b.py"], ["src/a.py", "tests/*"]), ["src/b.py"])

    def test_glob_crosses_folders(self):
        self.assertTrue(F.matches("tests/unit/test_a.py", ["tests/*"]))
        self.assertTrue(F.matches("docs/a/b.md", ["docs/"]))

    def test_automerge_blast_radius(self):
        allow = ["docs/*", "*.md", "tests/*"]
        self.assertTrue(F.automerge_ok(["docs/a.md", "tests/t.py"], allow, "low", [])[0])
        self.assertFalse(F.automerge_ok(["src/a.py"], allow, "low", [])[0])
        self.assertFalse(F.automerge_ok(["docs/a.md"], allow, "medium", [])[0])
        self.assertFalse(F.automerge_ok(["docs/a.md"], allow, "low", ["eval"])[0])
        self.assertFalse(F.automerge_ok([], allow, "low", [])[0])


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True, shell=True).stdout


class TestGateEndToEnd(unittest.TestCase):
    """A throwaway repo with a bare origin: the gate passes a clean change and fails bad ones."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        origin, self.root = self.tmp / "origin.git", self.tmp / "repo"
        env = "-c user.name=t -c user.email=t@t"
        sh(f"git init -q --bare -b main {origin}", self.tmp)
        sh(f"git clone -q {origin} repo", self.tmp)
        shutil.copytree(REPO / "factory", self.root / "factory", ignore=shutil.ignore_patterns("jobs"))
        (self.root / "factory" / "checks.sh").write_text("#!/usr/bin/env bash\nset -e\n! grep -rq BROKEN src\n")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n")
        sh(f"git add -A && git {env} commit -qm init && git push -q origin main", self.root)
        self.env = env

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_job(self, files_section: str):
        job_id = "001-test"
        d = self.root / "factory" / "jobs" / job_id
        d.mkdir(parents=True)
        job = F.new_job(job_id, 1, "test")
        job.update(stage="gate", round=1, risk="low")
        F.write_job(d, job)
        (d / "spec.md").write_text(f"## Files\n{files_section}\n")
        sh(f"git worktree add -q -b {job['branch']} .worktrees/{job_id} origin/main", self.root)
        return job_id, d, self.root / ".worktrees" / job_id

    def gate(self, job_id):
        out = subprocess.run([sys.executable, str(self.root / "factory" / "bin" / "gate"), job_id],
                             cwd=self.root, capture_output=True, text=True)
        return out.stdout.strip()

    def commit(self, wt, path, text):
        p = wt / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        sh(f"git add -A && git {self.env} commit -qm change", wt)

    def test_clean_change_passes(self):
        job_id, d, wt = self.make_job("- src/a.py")
        self.commit(wt, "src/a.py", "x = 2\n")
        self.assertTrue(self.gate(job_id).endswith("gate PASS"), self.gate)
        self.assertEqual(F.read_job(d)["stage"], "review")
        self.assertIn("GATE: PASS", (d / "round-1" / "checks.md").read_text())

    def test_out_of_scope_fails(self):
        job_id, d, wt = self.make_job("- src/a.py")
        self.commit(wt, "src/b.py", "y = 1\n")
        self.assertTrue(self.gate(job_id).endswith("gate FAIL"))
        job = F.read_job(d)
        self.assertEqual((job["stage"], job["round"]), ("build", 2))

    def test_protected_path_fails(self):
        job_id, d, wt = self.make_job("- factory/checks.sh")
        self.commit(wt, "factory/checks.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.gate(job_id)
        self.assertIn("Protected paths changed", (d / "round-1" / "checks.md").read_text())

    def test_stacked_job_counts_only_its_own_changes(self):
        job_id, d, wt = self.make_job("- src/c.py")
        sh(f"git branch factory/000-upstream origin/main", self.root)
        up_wt = self.root / ".worktrees" / "up"
        sh(f"git worktree add -q {up_wt} factory/000-upstream", self.root)
        self.commit(up_wt, "src/b.py", "y = 1\n")
        sh(f"git -C {wt} reset -q --hard factory/000-upstream", self.root)
        job = F.read_job(d)
        job["stacked_on_branch"] = "factory/000-upstream"
        F.write_job(d, job)
        self.commit(wt, "src/c.py", "z = 1\n")
        self.assertEqual(F.changed_files(wt, "main", job), ["src/c.py"])
        self.assertTrue(self.gate(job_id).endswith("gate PASS"))

    def test_failing_checks_fail(self):
        job_id, d, wt = self.make_job("- src/a.py")
        self.commit(wt, "src/a.py", "BROKEN\n")
        self.assertTrue(self.gate(job_id).endswith("gate FAIL"))
        self.assertIn("checks.sh exited", (d / "round-1" / "checks.md").read_text())


if __name__ == "__main__":
    unittest.main()
