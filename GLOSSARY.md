# Glossary

- **Job**: one issue's trip through the factory, in `factory/jobs/<id>/`. Its `job.json` is written only by `factory/bin/job`.
- **Stage**: where a job is: intake, spec, build, gate, review, approve, merging, recovery, needs-info, needs-input, needs-human, merged, rejected.
- **Phase**: one GitHub milestone. Its builds start only when every issue in it is specced and its questions are answered (the phase gate).
- **Standing rule**: an owner ruling in `docs/agents/standing-rules.md` that settles a kind of product question for every job, so it isn't asked again.
- **Product gap**: a product question the builder meets that neither the spec nor a standing rule settles. It stops the job and goes to the owner.
- **Clarify phase**: intake and spec only. It ends when the spec has no open product question (`job clarify`); open questions go on the issue with the `needs-input` label.
- **Build phase**: build, gate, review, approve and PR. It never starts while a product question is open.
- **Lane**: the clarify and build phases run as two independent lanes, side by side, so specs and owner questions move while other issues build.
- **Round**: one build plus its gate and reviews. A job gets `max_rounds` rounds before recovery.
- **Gate**: the scripted check after each build: protected paths, spec scope, `factory/checks.sh`.
- **Protected path**: a file agents may never change (checks, factory config, eval thresholds, gold labels, data splits, policies).
- **Seam**: the public boundary a test runs against. The spec names the seams; the builder tests only there.
- **Blast radius**: what a change can break. `job merge` merges unseen only what is on the auto-merge allowlist.
- **Stacked job**: a job whose branch starts from another job's unmerged branch.
- **Owner**: the person who adds the `factory` label, merges what the factory hands over, and makes the product calls.
