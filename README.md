# software-factory-mvp

A coding-agent software factory for Claude Code: GitHub issues in, specced, tested, reviewed and eval-gated pull requests out.

**Status:** design v0.2, build on hold pending a design review. No factory code yet.

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

## Credits

Ideas drawn from ZazenCodes' software factory (GPLv3; no code copied), disler's Super Simple Software Factory, GitHub Spec Kit, obra/superpowers, Stripe's Minions, StrongDM's software factory, AgentField, and Anthropic's work on long-running agent harnesses.

## Licence

MIT. See [LICENSE](LICENSE).
