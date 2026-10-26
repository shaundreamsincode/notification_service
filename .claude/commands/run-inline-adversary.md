---
name: run-inline-adversary
description: Advance the adversarial workflow using the openspec-adversary agent
  prompt inline in the current session (no subtask boundary). Inspects
  adversarial/state.json and either compares an authored artifact against the
  done main artifact, or authors the next artifact and — when its main version
  is already done — asks whether to chain straight into comparing that same
  artifact — then stops. Reconcile is handled separately by
  /reconcile-adversary.
---
Load the `openspec-adversary-run` skill for the adversarial dispatch protocol.

**Input**: The argument after `/run-inline-adversary` is an optional change name (e.g. `add-organizations`). If no argument is given, the skill resolves the change by inference — it does NOT reflexively ask.

## Goal

$ARGUMENTS

## Workflow

Follow the `openspec-adversary-run` skill exactly. This command runs the `openspec-adversary` agent **inline in the current session** (no subtask boundary) and is an entry point for advancing the adversarial workflow on the run path. It inspects `adversarial/state.json` together with `bun openspec status` and performs the dispatched action — and, when eligible and the user agrees, the one author→compare chain — chosen by this priority order:

1. **Compare** — if an `authored` entry's main artifact is now `done`, read both versions, write the comparison review, and advance the entry to `pending_reconciliation`. A compare selected here does not chain into an author.
2. **Author** — else if the next OpenSpec artifact has no adversarial entry, the dependency gate passes, and its main version is `ready` or `done` (`done` only, for `proposal`), independently author an adversarial copy — blind to the main version for every artifact except `proposal`, which is authored from the finished main proposal — and mark it `authored`. Then, **if that same artifact's main version is already `done`**, ask the user via the `question` tool whether to proceed into the comparison review now; on a proceed answer, chain directly into the compare of that just-authored artifact (read both versions, write the review, advance to `pending_reconciliation`). If the main version is not yet `done`, or the user declines, stop after authoring. This author→compare pair is the only chain — never go further.
3. **Stop** — else report that nothing is eligible, and why. If a review is pending reconciliation, report that — do NOT reconcile here.

The skill delegates each run-path action to its detailed protocol (`openspec-adversary-compare` for compare, `openspec-adversary-author` for author). It enforces the authoring isolation rule (never read the main version while authoring — except the main proposal, which is what an adversarial `proposal` is authored from) and the dependency-reconciliation gate.

Reconcile is NOT on this path. Reconciliation edits the **main** artifacts and is performed only by the `/reconcile-adversary` main-agent command. Even though this command runs inline in the main agent, it does not reconcile — keep the concerns separate.

Produce the dispatched action — and, when eligible and the user agrees, the one author→compare chain — then STOP.

### Reporting

Because this command runs inline — not in an isolated subagent — you are speaking directly to the user, not relaying a report to a separate main agent. Do NOT emit the isolated-subagent STOP directive used by `/run-adversary`.

Report directly to the user what you authored and/or reviewed this invocation (a single report covers an author, or an author plus its chained compare), then — when this invocation wrote an adversarial artifact or comparison review not yet reported — render the status report with the `openspec-adversary-status` skill, then stop. Render the status report above the compare-now question, since the author has just written content; if the user then stops, do not render it again. When nothing was written, state why in prose with no status report. Do NOT recommend or suggest next steps, and do NOT chain beyond the sanctioned author→compare pair. If you stopped because a review is pending reconciliation, say so.
