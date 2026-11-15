# software-factory-mvp

A coding-agent software factory for Claude Code: GitHub issues in, specced, tested, reviewed and eval-gated pull requests out.

**Status:** v1 (15 Nov 2026). Design review by a second agent still to come.

## The idea

Code owns the loop; agents do the judging. Small deterministic scripts run job state, gates, routing and merging. Agents decide only what scripts can't: whether an issue is worth building, the spec, the code, and the review.

```
issue labelled `factory` → intake → route → spec → build (tests first)
  → gate (checks, scope, eval smoke) → parallel reviewers → ≤2 fix rounds
  → recovery → approver → auto-merge only if on the blast-radius allowlist
```

## What makes it trustworthy

- **Hidden holdout scenarios:** the builder never sees the scenarios it is graded on.
- **Protected paths:** agents can't edit checks, eval thresholds, gold labels, data splits or policies.
- **Auto-merge by blast radius, not confidence:** a written allowlist decides what merges without a human.
- **Release is never automated.**
- **The factory is evaluated too:** a weekly scorecard (first-pass gate rate, rounds per job, share auto-merged, escalation precision, cost per merged PR, escaped defects), and later a frozen benchmark of past issues.

## What's in v1

```
.claude/skills/factory/SKILL.md   the orchestrator: queue, pipeline, guardrails
.claude/agents/                   intake, spec-writer, builder, reviewer-code,
                                  reviewer-eval, reviewer-security, approver
.claude/settings.json             blocks destructive git, direct merges, releases, gists
factory/bin/job                   the only writer of job state; refuses illegal moves; merges
factory/bin/gate                  protected paths, spec scope, checks.sh -> PASS/FAIL
factory/bin/route                 risk + surfaces -> reviewers and models
factory/config.json               rounds, routing, protected paths, auto-merge allowlist
factory/checks.sh                 the target repo's real checks
INSTALL.md                        how an agent installs it into a project
tests/                            unit tests and an end-to-end gate test
```

Not in v1 yet: hidden holdout scenarios and their verifier, the recovery agent (v1 hands exhausted jobs to a human), the eval smoke gate, and the weekly factory scorecard.

## Use

Install it into a project ([INSTALL.md](INSTALL.md)), label issues `factory`, then in Claude Code run `/factory run`. Run the tests with `python3 -m unittest discover -s tests -t .`.

## Credits

Ideas drawn from ZazenCodes' software factory (GPLv3; no code copied), disler's Super Simple Software Factory, GitHub Spec Kit, obra/superpowers, Stripe's Minions, StrongDM's software factory, AgentField, and Anthropic's work on long-running agent harnesses.

## Licence

MIT. See [LICENSE](LICENSE).
