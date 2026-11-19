"""The reporting adapter may authenticate evidence only on a supported installation."""

import hashlib
import json
import os
import shutil
import unittest

from tests.test_factory_observation_integration import FactoryCLI, JOB_ID


class EvidenceAdapterIntegration(FactoryCLI, unittest.TestCase):
    WEEKLY = ("--weekly", "2026-11-19", "--timezone", "UTC", "--as-of", "2099-11-27T10:00:00Z")

    def broken_job(self, name, **changes):
        path = self.job_dir.parent / name / "job.json"
        path.parent.mkdir()
        path.write_text(json.dumps({**json.loads(self.state.read_text()), "id": name, **changes}))
        return path

    def test_reports_keep_healthy_jobs_when_job_state_shapes_are_invalid(self):
        self.write_job(stage="merged")
        damaged = {"002-malformed-transition": {"history": ["unknown-private-content"]},
                   "003-malformed-reviewers": {"reviewers": ["unknown-private-content"]},
                   "004-malformed-surfaces": {"surfaces": [{"private": "raw"}]}}
        for name, changes in damaged.items():
            self.broken_job(name, **changes)
        report = json.loads(self.cli("scorecard", *self.WEEKLY, "--json").stdout)
        self.assertEqual(report["delivered"]["jobs"], [JOB_ID])
        self.assertEqual(set(report["diagnostics"]), set(damaged))
        self.assertNotIn("unknown-private-content", str(report))
        legacy = self.cli("scorecard", "--json")
        self.assertEqual(json.loads(legacy.stdout)["v1.2-two-phase"]["jobs"], 1)
        self.assertIn("002-malformed-transition", legacy.stderr)

    def test_reports_refuse_invalid_legacy_amounts_and_booleans(self):
        self.write_job(stage="merged", owner_minutes=0, cost_usd=0, escaped=False, owner_changes=None)
        damaged = {
            "002-negative-minutes": {"owner_minutes": -1},
            "003-negative-cost": {"cost_usd": -0.25},
            "004-nonfinite-cost": {"cost_usd": float("inf")},
            "005-string-escape": {"escaped": "false"},
            "006-numeric-owner-change": {"owner_changes": 0},
            "007-nonfinite-revision": {"revision": float("nan")},
        }
        for name, changes in damaged.items():
            self.broken_job(name, **changes)
        weekly = json.loads(self.cli("scorecard", *self.WEEKLY, "--json").stdout)
        self.assertEqual(weekly["delivered"]["jobs"], [JOB_ID])
        self.assertEqual(set(weekly["diagnostics"]), set(damaged))
        self.assertEqual(weekly["legacy"][JOB_ID], {"owner_minutes": 0, "cost_usd": 0, "escaped": False})
        legacy = self.cli("scorecard", "--json")
        card = json.loads(legacy.stdout)["v1.2-two-phase"]
        self.assertEqual(card["jobs"], 1)
        self.assertEqual((card["owner_minutes_median"], card["cost_usd_median"]), (0, 0))
        for name in damaged:
            self.assertIn(name, legacy.stderr)

    def test_reports_reject_duplicate_fields_at_every_level(self):
        self.write_job(stage="merged", owner_minutes=0)
        top = self.broken_job("002-duplicate-amount")
        nested = self.broken_job("003-duplicate-history")
        top.write_text(top.read_text().replace('"owner_minutes": 0', '"owner_minutes": 12, "owner_minutes": 0'))
        nested.write_text(nested.read_text().replace('"note": "created"', '"note": "duplicate", "note": "created"'))
        before = {path: path.read_bytes() for path in (top, nested, self.state)}
        weekly = json.loads(self.cli("scorecard", *self.WEEKLY, "--json").stdout)
        self.assertEqual(weekly["delivered"]["jobs"], [JOB_ID])
        self.assertEqual(set(weekly["diagnostics"]), {top.parent.name, nested.parent.name})
        legacy = self.cli("scorecard", "--json")
        self.assertEqual(json.loads(legacy.stdout)["v1.2-two-phase"]["jobs"], 1)
        for path, original in before.items():
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_folder_names_are_counted_without_echoing_them(self):
        self.write_job(stage="merged")
        invalid = "[private-name](arbitrary-target)"
        self.broken_job(invalid)
        self.broken_job("private second name")
        raw = self.cli("scorecard", *self.WEEKLY, "--json").stdout
        report = json.loads(raw)
        self.assertEqual(report["delivered"]["jobs"], [JOB_ID])
        self.assertEqual(report["invalid_job_directories"], 2)
        self.assertNotIn(invalid, raw)
        markdown = self.cli("scorecard", *self.WEEKLY).stdout
        self.assertIn("2 job directories have invalid IDs", markdown)
        self.assertNotIn(invalid, markdown)

    def test_card_and_weekly_use_local_references_without_repository_provenance(self):
        self.write_job(stage="merged", source_url="https://example.test/fake",
                       candidate_url="https://example.test/candidate",
                       metrics_url="https://example.test/metrics")
        self.cli("job", "observe", JOB_ID, "-", input=json.dumps(self.effort()))
        self.job_dir.joinpath("final-checks.md").write_text("GATE: PASS\n", encoding="utf-8")

        card = self.cli("job", "card", JOB_ID).stdout
        weekly = self.cli("scorecard", *self.WEEKLY).stdout
        self.assertIn("Tracker link unavailable", card)
        self.assertIn(f"`{JOB_ID}` (local job)", weekly)
        self.assertIn(f"Local observation ledger `factory/jobs/{JOB_ID}/observations/`", card)
        for markdown in (card, weekly):
            self.assertIn(f"`factory/jobs/{JOB_ID}/final-checks.md`", markdown)
            self.assertNotIn("](factory/jobs/", markdown)
            self.assertNotIn("example.test", markdown)

    def test_report_keeps_legacy_text_literal_in_markdown(self):
        stage = "awaiting `legacy` [review](https://stage.example.invalid)"
        first_title_line = "[preview](https://title.example.invalid)"
        title = first_title_line + "\n# fake heading"
        criterion = "Display <https://criterion.example.invalid> and www.example.invalid"
        self.write_job(stage=stage, title=title)
        self.broken_job("002-undated", stage=stage, history=[])
        (self.job_dir / "spec.md").write_text(
            f"## Acceptance criteria\n1. [display] {criterion}\n2. [nested] - Show the preview\n"
        )

        card = self.cli("job", "card", JOB_ID).stdout
        weekly = self.cli("scorecard", *self.WEEKLY).stdout
        self.assertIn(f"# Decision card · {JOB_ID} · `{first_title_line} # fake heading`", card)
        self.assertIn(f"- **Stage** — ``{stage}``", card)
        self.assertIn(f"- `{criterion}` (`display`) — missing (required)", card)
        self.assertIn("- `- Show the preview` (`nested`) — missing (required)", card)
        self.assertIn(f"Active job outcomes · ``{stage}`` 1.", weekly)
        self.assertIn(f"(``{stage}``).", weekly)
        self.assertNotIn("\n# fake heading", card)
        report = json.loads(self.cli("scorecard", *self.WEEKLY, "--json").stdout)
        self.assertEqual(report["outcomes"], {stage: 1})
        self.assertEqual(report["undated_statuses"], {"002-undated": stage})

    def test_legacy_scorecard_keeps_version_literal_in_a_markdown_table(self):
        version = "legacy | `v1` [preview](https://version.example.invalid)\n# fake heading"
        self.write_job(factory_version=version)

        markdown = self.cli("scorecard").stdout
        header = markdown.splitlines()[0]
        self.assertIn(r"``legacy \| `v1` [preview](https://version.example.invalid) # fake heading`` (n = 1)", header)
        self.assertNotIn("\n# fake heading", markdown)
        report = json.loads(self.cli("scorecard", "--json").stdout)
        self.assertEqual(report[version]["version"], version)

    def test_candidate_is_plain_after_origin_is_repointed(self):
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit",
                 "--allow-empty", "-qm", "Candidate")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("branch", f"factory/{JOB_ID}", head)
        self.git("update-ref", f"refs/remotes/origin/factory/{JOB_ID}", head)
        replacement = "https://github.com/fixture/replacement"
        self.write_job(source_url=f"{replacement}/pull/2", candidate_url=f"{replacement}/commit/{head}")
        proposal = {**self.effort(), "kind": "recovery_decision",
                    "evidence": [{"url": f"{replacement}/pull/2"}],
                    "data": {"failure_id": "example", "cause": "timeout"}}
        self.cli("job", "observe", JOB_ID, "-", input=json.dumps(proposal))
        self.git("remote", "set-url", "origin", replacement + ".git")

        card = self.cli("job", "card", JOB_ID).stdout
        self.assertIn(f"- **Candidate** — `{head}`\n", card)
        self.assertNotIn("/commit/", card)
        weekly = self.cli("scorecard", *self.WEEKLY).stdout
        self.assertIn("Tracker link unavailable", card)
        self.assertIn(f"`{JOB_ID}` (local job)", weekly)
        self.assertNotIn(replacement, card + weekly)
        report = json.loads(self.cli("scorecard", *self.WEEKLY, "--json").stdout)
        self.assertIsNone(report["links"][JOB_ID])
        self.write_job(pr=None, source_url=f"{replacement}/issues/1")
        self.assertNotIn(replacement, self.cli("job", "card", JOB_ID).stdout)

    def test_job_card_git_reads_disable_fetching_and_transport(self):
        self.write_job()
        tool = self.root / "stub"
        tool.mkdir()
        git = tool / "git"
        git.write_text(f"""#!/usr/bin/env python3
import os
import sys
from pathlib import Path

if sys.argv[1:2] == ['--no-optional-locks']:
    names = ['GIT_NO_LAZY_FETCH', 'GIT_ALLOW_PROTOCOL', 'GIT_PROTOCOL_FROM_USER']
    with Path('git-read-env').open('a') as log:
        log.write('|'.join(os.environ.get(name, 'absent') for name in names) + '\\n')
os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])
""")
        git.chmod(0o755)
        env = dict(os.environ, PATH=f"{tool}{os.pathsep}{os.environ.get('PATH', '')}", GIT_ALLOW_PROTOCOL="https:file")
        self.cli("job", "card", JOB_ID, env=env)
        self.assertEqual(set((self.root / "git-read-env").read_text().splitlines()), {"1||0"})

    def test_cli_leaves_incident_and_reported_recovery_safety_unverified(self):
        self.write_job()
        event = {"schema_version": 1, "event_id": "6d285466-8cbe-4fa9-9c00-3e264990f307", "job_id": JOB_ID,
                 "kind": "interruption", "origin": "real", "occurred_at": "2026-11-19T08:05:00Z",
                 "source": {"kind": "controller", "record_id": "interrupt-1"},
                 "data": {"interruption_id": "interrupt-1", "started_at": "2026-11-19T08:00:00Z",
                          "ended_at": "2026-11-19T08:05:00Z", "outcome": "resumed"}}
        self.cli("job", "observe", JOB_ID, "-", input=json.dumps(event))
        report = json.loads(self.cli("scorecard", *self.WEEKLY, "--json").stdout)
        self.assertIsNone(report["guardrails"]["effective_total"])
        self.assertEqual(report["interruptions"]["resumed"], 1)
        self.assertIsNone(report["interruptions"]["verified_safe"]["percent"])
        self.assertEqual(report["incident_and_interruption_source_verification"], "unavailable")
        markdown = self.cli("scorecard", *self.WEEKLY).stdout
        self.assertIn("Incident and interruption source verification is unavailable", markdown)

    def test_canonical_bound_looking_gate_note_does_not_verify(self):
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit",
                 "--allow-empty", "-qm", "Base")
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", base)
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit",
                 "--allow-empty", "-qm", "Candidate")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("worktree", "add", "-qb", f"factory/{JOB_ID}", f".worktrees/{JOB_ID}", head)
        self.write_job()
        spec = b"## Acceptance criteria\n1. A criterion\n"
        (self.job_dir / "spec.md").write_bytes(spec)
        round_dir = self.job_dir / "round-1"
        round_dir.mkdir()
        (round_dir / "checks.md").write_text(
            f"GATE: PASS\nCANDIDATE: {head}\nBASE: {base}\nSPEC-DIGEST: {hashlib.sha256(spec).hexdigest()}\n\n",
            encoding="utf-8",
        )
        weekly = json.loads(self.cli("scorecard", "--weekly", "2026-11-19", "--timezone", "UTC",
                                     "--as-of", "2099-11-27T10:00:00Z", "--json").stdout)
        self.assertEqual(weekly["delivered"]["first_gate"]["covered"], 0)
        self.assertTrue(any(gap["requirement"] == "checks" and gap["status"] == "unverified"
                            for gap in weekly["guardrails"]["gaps"]))
        unpublished = self.cli("job", "card", JOB_ID).stdout
        self.assertIn(head, unpublished)
        self.assertNotIn("/commit/", unpublished)

    def test_merge_requires_separate_final_check_evidence(self):
        self.write_job(stage="merged")
        options = ("--weekly", "2026-11-19", "--timezone", "UTC", "--as-of", "2099-11-27T10:00:00Z", "--json")
        report = json.loads(self.cli("scorecard", *options).stdout)
        rows = {gap["requirement"]: gap["status"] for gap in report["guardrails"]["gaps"]}
        self.assertEqual(rows["final_checks"], "missing")
        (self.job_dir / "final-checks.md").write_text("GATE: PASS\n", encoding="utf-8")
        report = json.loads(self.cli("scorecard", *options).stdout)
        rows = {gap["requirement"]: gap["status"] for gap in report["guardrails"]["gaps"]}
        self.assertEqual(rows["final_checks"], "unverified")
