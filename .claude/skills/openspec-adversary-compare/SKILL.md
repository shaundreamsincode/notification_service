---
name: openspec-adversary-compare
description: "Comparison protocol: read an authored adversarial artifact plus
  the now-done main artifact, write the comparison review, and advance the
  adversarial/state.json entry to pending_reconciliation."
---
Write the comparison review for an already-authored adversarial artifact, now that the corresponding main artifact is `done`. This is the COMPARISON action of the adversarial flow: the authoring action derived the adversarial artifact independently; here you read the main artifact, compare the two, and write the review that the reconciliation action will later fold into the main artifact.

**Input**: A change name. When invoked via `/run-adversary`, the dispatcher (`openspec-adversary-run`) has already resolved the change and selected this action — use it and do not re-ask. You may be invoked **chained immediately after the authoring action** for the artifact just authored; in that case the target entry is that just-authored entry — use it directly (skip step 2's entry-picking), but still run the readiness check.

**Status report**: Load the `openspec-adversary-status` skill and follow its contract: render its report after the comparison review is written (step 6), or when the user explicitly asks for the status. Stops before the review is written and the multi-entry question in step 2 render no report — unless this invocation already authored an unreported adversarial artifact in the author→compare chain, in which case the stop renders the report for it. It names no next step.

---

## What this action does and does not touch

- **Reads**: the change status, `adversarial/state.json`, the already-authored adversarial artifact(s), and the main artifact at `outputPath` (for specs, the full main `specs/**` tree) — for the first time, except when the target is `proposal`, whose main version the authoring action already read as its intent input.
- **Writes**: the comparison review under `adversarial/reviews/`, and the `state.json` status field for the target entry.
- **Never** edits a main artifact or the authored adversarial artifact. Reconciliation is the `openspec-adversary-reconcile` action's job.

The isolation rule from the authoring action has already done its job: the adversarial artifact was derived independently, without seeing the main version — or, for `proposal`, taking only the change's intent and scope from it. Comparison is what was withheld until now, and passing judgment on the two versions here is exactly the point.

---

## Paths

```
openspec/changes/<change>/adversarial/
  state.json
  artifacts/...                    # the adversary's authored artifact(s)
  reviews/<artifact-id>-review.md  # written here
```

`state.json` paths are relative to the change directory.

---

## Steps

### 1. Resolve the change

- Use the change resolved by the dispatcher. If you somehow have no change, resolve it by inference (explicit → session → single existing change → ask only as a last resort); do not reflexively ask.
- Read `openspec/changes/<name>/adversarial/state.json`. If it is missing or has no entries, tell the user there is nothing to compare (nothing has been authored yet) and stop.

### 2. Pick the entry to compare

**If the dispatcher chained you here right after authoring**, the target is that just-authored entry — use it and skip straight to the readiness check below.

Otherwise, collect every entry with `status: "authored"`.

- **None** → tell the user there is nothing authored awaiting comparison. If there are `pending_reconciliation` entries, note that their reviews are awaiting reconciliation; otherwise say everything is already compared/reconciled. Stop.
- **One** → use it (subject to the readiness check below).
- **More than one** → present them via the `question` tool and ask which to compare. One entry per invocation.

**Readiness check.** Run `bun openspec status --change "<name>" --json` and find the target artifact's `status`. The main artifact MUST be `"done"`. If the chosen authored entry's main artifact is not yet `done`, STOP and report that the main artifact is not finished — the adversarial copy is authored and waiting, but there is nothing to compare against yet. If the user picked from a multi-entry list, offer only the authored entries whose main artifact is `done`.

### 3. Read both sides

For the chosen entry, read:
- the authored adversarial artifact(s) at `adversarialPaths`;
- the main artifact(s) at `mainPaths` (for specs, read the full main `specs/**` tree).

### 4. Write the comparison review

Write `adversarial/reviews/<target-id>-review.md` comparing **Main** (the main agent's version) against **Adversary** (the authored version). The review MUST include:

- A short framing line naming the two sources and their paths.
- The grading bar you used (value-centric / RFC 2119 / atomic + testable / correct artifact mechanics / coverage — scoped to the artifact type).
- A coverage comparison: what each version covers, and where they diverge.
- For each material divergence: which version is stronger and **why**.
- A concrete, actionable **merge recommendation** the main agent can act on — per-section where the artifact has sections (per-capability for specs, per-decision for design, per-task-block for tasks, per-section for proposal).
- Any **blocking** cross-cutting item that must be settled before the artifact is archived.

Be specific. The review is the product; vague observations are useless to the reconciling agent. Form your own judgment — name where the main version is actually stronger, not only where the adversary wins.

### 5. Advance the state manifest

Update the chosen entry: set `status: "pending_reconciliation"`, set `reviewPath` to `adversarial/reviews/<target-id>-review.md`, and set `comparedAt` to the current ISO 8601 timestamp. Leave `authoredAt`, `mainPaths`, `adversarialPaths`, and `dependencies` intact. Write `state.json` pretty-printed (2-space indent).

### 6. Stop

You have written exactly one comparison review. If you were chained here right after authoring, fold this into the single combined report for the invocation (name both the artifact you authored and the review you just wrote). Tell the user:
- which artifact you compared and where the review lives (and, when chained, that you also authored it this invocation);
- the `openspec-adversary-status` report, refreshed after the `state.json` write.

The report names no next step, inline or spawned — the user decides whether to reconcile.

Do NOT compare another artifact. One review per invocation.

---

## Anti-patterns — DO NOT

- **Do NOT compare an entry whose main artifact is not `done`.** The authored copy waits; comparison needs both sides.
- **Do NOT compare an entry that is not `authored`.** `pending_reconciliation` already has a review; `reconciled` is finished.
- **Do NOT edit the main artifact or the authored adversarial artifact.** The comparison action only writes the review and the `state.json` status field.
- **Do NOT reconcile.** Folding findings into the main artifacts is the reconciliation action's job.
- **Do NOT compare more than one entry per invocation.**
- **Do NOT skip the state manifest update** — without `pending_reconciliation` + `reviewPath`, the reconciliation action cannot find the review.
