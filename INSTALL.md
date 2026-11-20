# Install the factory into a project

For a coding agent installing this factory into a target repo. Copy what's generic, adapt what's repo-specific, verify, report.

## Check first

- The target is a git repo with a GitHub `origin`, and `gh auth status` shows a user who can push and manage labels.
- `python3 --version` is 3.10 or later (the scripts use only the standard library).
- None of the files below already exist in the target. If one does, show the clash and ask before overwriting.

## Copy, unchanged

```
.claude/skills/*            the orchestrator and the vendored skills (keep THIRD_PARTY_NOTICES.md with them)
.claude/agents/*.md
THIRD_PARTY_NOTICES.md
docs/agents/*.md
.claude/settings.json
factory/bin/job  factory/bin/gate  factory/bin/route
factory/bin/scorecard  factory/bin/adversarial-report  factory/bin/evidence-bundle
factory/lib/factory.py  factory/lib/observations.py  factory/lib/guardrails.py
factory/lib/evidence_adapter.py  factory/lib/factory_recovery.py  factory/lib/merge_evidence.py
factory/lib/decision_card.py  factory/lib/scorecard_report.py  factory/lib/job_links.py
factory/lib/evidence_bundle.py
factory/baseline-v1.json
factory/config.json
factory/checks.sh
```

## Adapt

1. **`factory/checks.sh`:** the target's real install, lint, typecheck, test and build commands. Non-interactive, never rewrites files, exits non-zero on failure, runs from a job's worktree.
2. **`factory/config.json`:**
   - `protected_paths`: add the target's goalposts (eval thresholds, gold labels, data splits and seeds, policy rules, holdout scenarios).
   - `automerge_allowlist`: only what is safe to merge unseen (docs, tests, and similar). Owner approves this list.
   - `base_branch`, `merge_method`, and `owner_name` (shown in the 🤖 note on every comment the factory posts).
   - `lanes`: how many jobs the clarify lane (specs and owner questions, no code) and the build lane run at once. Default 3 and 1.
3. **`AGENTS.md`:** the target's rules; per-folder rule files where folders need their own (data, evals, agent).
4. **`.gitignore`:** add `factory/jobs/` and `.worktrees/`. Check that broad ignores (like `*.csv` to keep data out) don't also block test fixtures, and tell the spec writer in AGENTS.md how fixtures should be stored.
5. **Labels:** `factory/bin/job labels`. It creates `factory`, `needs-input` (the owner has questions to answer), `factory:needs-human` and `factory:escaped`.
6. **`CLAUDE.md` and `GLOSSARY.md`:** add the `## Agent skills` block from this repo's `CLAUDE.md`, and start a `GLOSSARY.md` with the target's own domain words.

## Verify

- `bash factory/checks.sh` passes on the base branch. With Python 3.12+, `unittest` exits non-zero when it finds no tests, so a new repo needs at least one smoke test before the first job.
- `factory/bin/job list` prints `no jobs`.
- Run `factory/bin/scorecard --json` in the empty install. It prints `{}`. In a disposable checkout, run `factory/bin/scorecard --weekly 2026-11-16 --timezone UTC --defect-window-days 14 --json` with those fixture settings and confirm zero delivered jobs. Compare the copied baseline bytes to the source and retain its four-job historical cohort.
- In a disposable checkout, exercise the [observation and correction example](docs/agents/factory-owner-workflow.md#submit-and-correct-an-observation) with fixture values. Replay it with the same ID and check that the recording time is preserved. Submit the correction, then confirm that the job card reads the corrected effort, the recovery summary still reads the job, and the job state is byte-identical.
- Confirm the reporting executables have their execute bits. Run `factory/bin/adversarial-report --candidate <full-commit-sha> --json` against the trusted local candidate. Keep the report with its candidate hash and observed, missed, or missing case results.
- Run `factory/bin/evidence-bundle --help` to confirm the offline exporter and its imports are available. Use the [owner workflow](docs/agents/factory-owner-workflow.md#open-selected-job-evidence-offline) to export a synthetic job to a new directory and verify that `index.html` links to its manifest and selected receipt projections.
- Report what was copied, what was adapted, and the allowlist for the owner to confirm.

## Observation and reporting data

Use the [factory owner workflow](docs/agents/factory-owner-workflow.md) to capture typed observations, preserve the local ledger during updates, review recovery and produce on-demand weekly reports. Legacy job fields without recording timestamps remain separately identified in the report. Canonical gate and review Markdown are historical evidence. A stabilized installation can verify bound receipts and worker audits locally when their candidate, base, spec, round, and publication state can all be established.

If an existing repository webhook has a closed, merged pull-request delivery, record a `check_evidence` observation with `source.kind` set to `controller`, `data.check_id` set to `merged_candidate`, `data.verdict` set to `pass`, and the candidate SHA and round. In `evidence` point to the same repository's `https://api.github.com/repos/OWNER/REPO/hooks/HOOK_ID/deliveries/DELIVERY_ID` record. The weekly `--verify-github-merges` option performs the read through the authenticated GitHub CLI using existing Webhooks read access. An expired, inaccessible, or mismatched delivery leaves the exact merged candidate unknown. The report records only the verified merge identifiers and timestamp. [GitHub webhook delivery endpoint](https://docs.github.com/en/rest/repos/webhooks#get-a-delivery-for-a-repository-webhook)
