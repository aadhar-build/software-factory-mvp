#!/usr/bin/env bash
# The repo's real checks. Runs with a job's worktree as the working directory.
# Rules: non-interactive, never rewrites files (no --fix), exits non-zero on any failure.
# The installer replaces the body below with the target repo's own commands.
set -euo pipefail

python3 -m unittest discover -s tests -t . -q
