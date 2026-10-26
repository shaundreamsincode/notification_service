---
name: openspec-adversary-reconcile
description: Protocol for reconciling a pending adversarial review into the main
  OpenSpec artifact(s) with a summary-then-approve gate, marking the state entry
  reconciled and retaining all adversarial artifacts and reviews.
---
Reconcile a `pending_reconciliation` adversarial review into the **main** OpenSpec artifact(s), using a summary-then-approve gate, then mark the review `reconciled` while retaining all adversarial files.

This action edits the main artifacts and runs only as the main agent, via `/reconcile-adversary`. It is NOT on the run path: neither `/run-adversary` nor `/run-inline-adversary` ever reconciles. It is the third and final step of the adversarial flow: the artifact was authored independently, the comparison review was written, and now you fold the worthwhile findings back into the main artifact(s).

This action only operates on entries that already have a comparison review (`status: "pending_reconciliation"`). An entry that is still `authored` has no review yet — the comparison has not been written. If you find only `authored` entries, report that they have no comparison review yet, then stop.

**Input**: A change name. This action is reached only via `/reconcile-adversary`. Without an argument, resolve the change by inference — explicit argument, then the change in active use this session (continuity is the default), then the single existing change if there is only one; ask only when there are multiple changes, no argument, and no session signal.

**Status report**: Load the `openspec-adversary-status` skill and follow its contract: render its report only after approved edits are written to the main artifact(s) — a completed apply, or a failed apply that wrote some edits — or when the user explicitly asks for the status. Change selection, entry selection, the approval gate, every re-gate after Request changes, a Reject, and a failure before any edit render no report. Until the approved edits and the `state.json` write both succeed, the entry's row stays **Pending**. The report names no next step.

---

## Paths

```
openspec/changes/<change>/adversarial/
  state.json
  artifacts/...          # the adversary's independently authored artifact
  reviews/<artifact-id>-review.md
```

`state.json` paths are relative to the change directory.

---

## Steps

### 1. Resolve the change and the pending entry

- Use the resolved change (see Input). If you still have no change, resolve it by inference — explicit → session (continuity) → single existing change — and ask only as a last resort.
- Read `openspec/changes/<name>/adversarial/state.json`. If it is missing or has no entries, tell the user there is nothing to reconcile (nothing has been authored yet) and stop.
- Collect every entry with `status: "pending_reconciliation"`.
  - None → check for `authored` entries. If any exist, report that they have no comparison review yet, so nothing is ready to reconcile. Otherwise report that everything is already reconciled. Stop.
  - One → use it.
  - More than one → present them via the `question` tool and ask which to reconcile. Reconcile one entry per invocation. If any `authored` entries also exist, mention them separately — they are not reconcilable yet (they need comparison first) and MUST NOT appear as reconciliation options.

### 2. Read all three sources

For the chosen entry, read:
- the comparison review at `reviewPath`;
- the adversarial artifact(s) at `adversarialPaths`;
- the main artifact(s) at `mainPaths`.

Read them fully. The review is the adversary's argument; the two artifacts are the evidence. Form your own judgment — the review is a strong input, not a command. You may disagree with a recommendation if the main artifact is actually stronger.

### 3. Summary-then-approve: present the plan

In your message (NOT in the `question` tool), present:

1. A concise **findings summary** — the material divergences the review raised and your verdict on each (adopt / partially adopt / reject, with a one-line reason).
2. The **exact proposed edits** to the main artifact(s): for each file, show the precise change (old → new, or the added/removed block). Be concrete enough that the user can approve without guessing what will be written.
3. Any **blocking cross-cutting item** the review flagged, and how your edits resolve it (or why it is deferred).

Only after presenting all of the above, invoke the `question` tool with a single short gate:

- **Approve** — apply the proposed edits.
- **Reject** — apply nothing; leave the entry `pending_reconciliation`.
- **Request changes** — the user describes adjustments; revise the proposed edits and re-present + re-gate.

Put NO substance inside the `question` options — the message above the gate carries everything.

### 4. Apply on approval

On **Approve**:
- Apply the edits to the **main** artifact(s) only — `proposal.md`, `design.md`, `specs/**`, or `tasks.md` as applicable. Never edit the adversarial files.
- Update the `state.json` entry: set `status: "reconciled"`, set `reconciledAt` to the current ISO 8601 timestamp, and record a short `notes` summary of what was adopted vs. rejected. Leave `authoredAt`, `comparedAt`, and `reviewPath` intact. Write `state.json` pretty-printed (2-space indent).
- **Retain all adversarial files.** Do NOT delete `adversarial/artifacts/**` or `adversarial/reviews/**`. They are kept for history.

On **Request changes**: revise and re-gate, presenting the revised plan. On **Reject**: write nothing — not the artifacts, not `state.json` — then tell the user the reconciliation was rejected and no changes were applied, and stop.

### 5. Report

Tell the user:
- which artifact was reconciled and what was adopted vs. rejected;
- that the adversarial artifact and review were retained;
- the `openspec-adversary-status` report, refreshed after the `state.json` write. If any edit or the `state.json` write failed, say which; when some edits were written, render the report with the entry's row as recorded — never render it Reconciled on a partial apply. If the apply failed before any edit was written, render no report.

---

## Anti-patterns — DO NOT

- **Do NOT edit the main artifacts before the user approves.** Summary-then-approve means the gate is the wall.
- **Do NOT treat the adversary's review as binding.** Adopt what is genuinely stronger; reject what isn't, and say why.
- **Do NOT delete or modify any file under `adversarial/`** except `state.json`'s status fields. Artifacts and reviews are retained.
- **Do NOT reconcile more than one pending entry per invocation.** One entry, one gate, one apply.
- **Do NOT mark an entry `reconciled` without applying (or deliberately rejecting) its edits.** The status must reflect reality.
- **Do NOT reconcile an `authored` entry.** It has no review yet — it must be compared first.
- **Do NOT confuse the actions.** The author and compare actions produce the artifact and review; this is the reconciliation action — the one that mutates main artifacts, behind the approval gate.
