# Standing rules

The owner's rulings that hold for every job, so the same kind of product question is never asked twice. The spec-writer and the builder read this file before raising a product question. A question one of these rules settles is not asked: the spec records it under **Owner decisions** as `Applied rule R<n>: <how it applies here>`, so the choice is visible, never silent.

Only the owner adds, changes or removes a rule. An agent that sees an answer which could become a rule says so in its reply ("rule candidate: ..."), and the orchestrator puts it to the owner.

## How to write a rule

- One rule per line, numbered `R1`, `R2`, ... Numbers are never reused.
- Say what decides, not why: "When X, do Y."
- Add where the owner gave it (an issue comment or thread message) and the date.

## Rules

None yet. An example of the shape:

```
- R1: When the old code crashes on, or silently accepts, NaN, Infinity or broken text, the new code refuses it with one error line. Error wording may differ only inside brackets. (Owner, support-triage-auto#71, 2026-11-19)
```
