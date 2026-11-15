# Install the factory into a project

For a coding agent installing this factory into a target repo. Copy what's generic, adapt what's repo-specific, verify, report.

## Check first

- The target is a git repo with a GitHub `origin`, and `gh auth status` shows a user who can push and manage labels.
- `python3 --version` is 3.10 or later (the scripts use only the standard library).
- None of the files below already exist in the target. If one does, show the clash and ask before overwriting.

## Copy, unchanged

```
.claude/skills/factory/SKILL.md
.claude/agents/*.md
.claude/settings.json
factory/bin/job  factory/bin/gate  factory/bin/route
factory/lib/factory.py
factory/config.json
factory/checks.sh
```

## Adapt

1. **`factory/checks.sh`:** the target's real install, lint, typecheck, test and build commands. Non-interactive, never rewrites files, exits non-zero on failure, runs from a job's worktree.
2. **`factory/config.json`:**
   - `protected_paths`: add the target's goalposts (eval thresholds, gold labels, data splits and seeds, policy rules, holdout scenarios).
   - `automerge_allowlist`: only what is safe to merge unseen (docs, tests, and similar). Owner approves this list.
   - `base_branch`, `merge_method`.
3. **`AGENTS.md`:** the target's rules; per-folder rule files where folders need their own (data, evals, agent).
4. **`.gitignore`:** add `factory/jobs/` and `.worktrees/`.
5. **Labels:** `gh label create factory`, `factory:needs-info`, `factory:needs-human`, `factory:escaped`.

## Verify

- `bash factory/checks.sh` passes on the base branch.
- `factory/bin/job list` prints `no jobs`.
- Report what was copied, what was adapted, and the allowlist for the owner to confirm.
