# software-factory-mvp

A coding-agent software factory for Claude Code: GitHub issues in, specced, tested, reviewed and eval-gated pull requests out.

**Status:** v1 (15 Nov 2026). Design review by a second agent still to come.

## The idea

Code owns the loop; agents do the judging. Small deterministic scripts run job state, gates, routing and merging. Agents decide only what scripts can't: whether an issue is worth building, the spec, the code, and the review.

```
phase check:   milestone's issues mapped to the work it must cover; gaps → owner
clarify lane:  issue labelled `factory` → intake → spec (standing rules first) → product questions?
                 yes → posted on the issue, label `needs-input`, wait for the owner
                 no  → ready
phase gate:    builds start only when every issue in the milestone is ready (one question sheet)
build lane:    ready → pickup (re-spec if main moved) → build (tests first; an unsettled product gap stops and asks)
                 → gate (checks, scope, eval smoke) → parallel reviewers → ≤2 fix rounds
                 → recovery → approver → auto-merge only if on the blast-radius allowlist
```

The two lanes run side by side across phases. Within a phase (one milestone), every issue is specced and every question is answered, in one sheet, before any of its builds start, so the owner answers once instead of being interrupted mid-build. Rulings that hold for every job live in `docs/agents/standing-rules.md`, so a kind of question is asked once. The build lane refuses to start while a spec has an open product question, so nothing is built on a guess about policy, metrics, thresholds or copy. Technical questions never stop a job: the agents ask a second model (Astra, with Fable as tiebreaker) and log the answer.

## What makes it trustworthy

- **Hidden holdout scenarios:** the builder never sees the scenarios it is graded on.
- **Product questions go to the owner before any code:** the spec lists them, each with a recommended answer, and the build is blocked until they're answered on the issue.
- **Protected paths:** agents can't edit checks, eval thresholds, gold labels, data splits or policies.
- **Every fix round is kept:** sending a job back for changes always starts a fresh round, and old reviews are never overwritten or counted twice.
- **Auto-merge by blast radius, not confidence:** a written allowlist decides what merges without a human.
- **Release is never automated.**
- **The factory is evaluated too:** a weekly scorecard (first-pass gate rate, rounds per job, share auto-merged, escalation precision, cost per merged PR, escaped defects), and later a frozen benchmark of past issues.

## Skills it borrows

Planning to build uses [Matt Pocock's skills](https://github.com/mattpocock/skills): `grill-me`, `to-spec` and `to-tickets` for the owner's planning, `triage` in intake, `to-spec` in the spec, `tdd` in the builder, and his two-axis review in `reviewer-code`. Build to ship uses [tstack](https://github.com/t3dotgg/tstack): `file-pr` for PR descriptions, `leaving-pr-comments` for every comment, `babysit-pr` for CI, and `full-stop` as the factory's default stopping point. Both are MIT and copied unchanged; `docs/agents/skills.md` says where each one sits and where the factory's rules override it.

## Is it getting better?

`factory/bin/scorecard` groups every job by the factory version that ran it and sets it beside a frozen baseline. `factory/baseline-v1.json` holds v1's numbers from its first 4 jobs (15 Nov 2026), taken before the planning and shipping skills were added; jobs since then are tagged `v1.1-skills`, from 18 Nov `v1.2-two-phase` (clarify before build), and from v1.3 `v1.3-phase-gate`. From v1.3 it also counts questions to the owner per job, jobs that asked after the clarify gate (phase 1 misses), and hours a job waited on an answer. Under 10 jobs a column, read the comparison as directional only.

## What's in it

```
.claude/skills/factory/SKILL.md   the orchestrator: queue, pipeline, guardrails
.claude/agents/                   intake, spec-writer, builder, reviewer-code,
                                  reviewer-eval, reviewer-security, approver
.claude/settings.json             blocks destructive git, direct merges, releases, gists
factory/bin/job                   the only writer of job state; refuses illegal moves; clarify,
                                  answers, pickup, lane; merges
factory/bin/gate                  protected paths, spec scope, checks.sh -> PASS/FAIL
factory/bin/route                 risk + surfaces -> reviewers and models
factory/config.json               lanes, rounds, routing, protected paths, auto-merge allowlist
factory/checks.sh                 the target repo's real checks
INSTALL.md                        how an agent installs it into a project
tests/                            unit tests and an end-to-end gate test
```

Not built yet: hidden holdout scenarios and their verifier, the recovery agent (exhausted jobs go to a human), the eval smoke gate, and CI checks on every PR.

## Use

Install it into a project ([INSTALL.md](INSTALL.md)), label issues `factory`, then in Claude Code run `/factory run` (both lanes), or `/factory clarify` / `/factory build` for one. Answer the questions on any issue labelled `needs-input`, then remove the label. Run the tests with `python3 -m unittest discover -s tests -t .`.

## Credits

Ideas drawn from ZazenCodes' software factory (GPLv3; no code copied), disler's Super Simple Software Factory, GitHub Spec Kit, obra/superpowers, Stripe's Minions, StrongDM's software factory, AgentField, and Anthropic's work on long-running agent harnesses.

## Licence

MIT. See [LICENSE](LICENSE).
