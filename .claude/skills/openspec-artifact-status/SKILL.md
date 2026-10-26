---
allowed-tools: Bash(openspec:*)
compatibility: Requires openspec CLI.
name: openspec-artifact-status
description: "Read-only status report for OpenSpec workflows: renders a table of
  every artifact's main status (plus adversarial columns once an adversarial
  pass has started) and the invocation's last action, collapsing to a one-line
  completion summary once every artifact is finished. Rendered by OpenSpec
  workflow skills only after the current command changes artifact content, or on
  explicit request."
---
Render the OpenSpec artifact status report: a table of every artifact's main status — with adversarial columns once an adversarial pass has started — followed by the invocation's last action. Once every artifact is finished, the report collapses to a one-line completion summary with no table, unless the user explicitly asks for the table. OpenSpec workflow skills load this skill and render the report only after the current command changes artifact content, or when the user explicitly asks for it — never merely because a command started, asked a question, or stopped.

This skill is **read-only and observational**. It never creates, repairs, or advances any artifact or state file, never selects what the calling workflow does next, and never authorizes continuing the workflow.

**Input from the caller**: the selected change (or the fact that none is selected yet), the status JSON the caller already holds or can refresh with the same CLI invocation and flags it uses for its own `status` calls (including any `--store`), and a one-line description of what this invocation did.

<!-- status-contract:start -->
## Status report contract

### Forms

The report takes one of three forms. Choose it with "Choosing the form" below.

**Main-only table** — no adversarial pass has started:

```markdown
**Change:** `<change-name>` · **Schema:** `<schemaName>`

| Artifact | Main |
|---|---|
| <artifact-id> | <main> |

**Last action:** <what this invocation did, or why it did nothing>
```

**Full table** — an adversarial pass has started, or adversarial state cannot be read:

```markdown
**Change:** `<change-name>` · **Schema:** `<schemaName>`

| Artifact | Main | Adversarial | Comparison | Reconciliation |
|---|---|---|---|---|
| <artifact-id> | <main> | <adversarial> | <comparison> | <reconciliation> |

**Last action:** <what this invocation did, or why it did nothing>
```

**Completion summary** — every artifact is finished (no table):

```markdown
**Artifacts complete:** `<change-name>`
**Last action:** <what this invocation did, or why it did nothing>
```

When a warning applies (see "Unavailable and inconsistent data"), add one line directly below the table, before **Last action**:

```markdown
**Status warnings:** <warning>; <warning>
```

Never add a next-command line, a recommendation, or a suggested follow-up to this report.

### Choosing the form

Evaluate these from metadata that is fresh after this invocation's last write:

- **Adversarial pass started**: `adversarial/state.json` exists and has at least one entry. A missing `state.json`, or a readable one with no entries, means no pass has started.
- **Main complete**: the status JSON reports `isComplete: true` and every `artifacts[]` entry's `status` is `done` or `skipped`.
- **Adversarial complete**: every `artifacts[]` entry has a `reconciled` entry in `state.json`. Reconciling only the entries that exist is not enough — an artifact with no entry, including a skipped one, leaves the pass unfinished.

Then pick the first form that applies:

1. **Completion summary** — when all of these hold: the user did not explicitly ask to see the status table; a change is selected; no warning or unavailable value applies; main is complete; and either no adversarial pass has started or the adversarial pass is complete.
2. **Main-only table** — when no adversarial pass has started.
3. **Full table** — otherwise, including whenever `state.json` is unreadable or malformed.

An explicit request for the status table always renders a table (main-only or full by the rules above), even when everything is complete. The "Change not selected" and "Status unavailable" single-row forms always use the full table's columns.

### When to render

Rendering is gated. Decide whether a report is due before choosing a form; when none is due, render nothing from this contract.

A report is due at a user-facing boundary — a question to the user, or the command's final response — when either of these holds:

- **Unreported content changes**: since the current command started, or since its last report, the command created or changed the content of a main artifact, an adversarial artifact, or a comparison review. Render once for all such changes, at the next boundary, above any question. Uninterrupted work across several steps (for example, several artifacts created in one run, or an author followed by its chained compare) produces one report, not one per step.
- **Explicit request**: the user asked to see the artifact status or the status table.

None of these is a content change on its own: loading this skill or a workflow skill; reading or refreshing status; selecting a change; creating an empty change scaffold; writing metadata only, such as `.openspec.yaml` or `adversarial/state.json`; or presenting a proposed edit that has not been written. Metadata written alongside a content change is reported with it.

The reporting obligation belongs to the command currently running. It starts when that command starts and ends with its final response. Changes made by an earlier command never make a report due in a later command, and this skill remaining in context from an earlier command never makes a later command render a report — not at its start, and not at its questions.

After a report, later boundaries in the same command render nothing until further content changes occur.

When the command stops partway — for clarification, a rejection, or a failure — after unreported content changes, render the report for what was actually written, and name the partial progress or failure in **Last action**. A stop before any content change renders nothing.

When no report is due, keep the calling command's own explanation, summary, or question, and add no table, no completion summary, and no **Last action** line.

"Render the report" means render whichever form "Choosing the form" selects; the completion summary satisfies the rendering obligation.

### Data sources

- **Rows**: one row per entry in the status JSON `artifacts[]`, in that order. Never hardcode artifact names; custom schemas render their own artifacts. A multi-file (glob) artifact is one row.
- **Main**: that entry's `status`, read from status JSON that is fresh after this invocation's last write. Map `done` → Done, `ready` → Ready, `blocked` → Blocked, `skipped` → Skipped; render any other value verbatim. Main status reflects file existence only — it says nothing about implementation or quality.
- **Adversarial, Comparison, Reconciliation**: the matching entry in `adversarial/state.json` under the selected change's root directory (the `changeRoot` from status JSON, or the change directory the caller resolved). The `<change-name>` and `<schemaName>` header values come from the same status JSON.

### Adversarial mapping

| Recorded state | Adversarial | Comparison | Reconciliation |
|---|---|---|---|
| No `state.json`, or no entry for the artifact | Not authored | — | — |
| `authored` | Authored | Not written | — |
| `pending_reconciliation` | Authored | Written | Pending |
| `reconciled` | Authored | Written | Reconciled |

- **Reconciled** means the reconciliation is recorded — not that later edits to the main artifact were reviewed.
- A reconciliation that is proposed or awaiting approval stays **Pending**. Render **Reconciled** only after the approved edits and the `state.json` write both succeeded.

### Unavailable and inconsistent data

Report what is known; never fill a gap with a guess or with a success. Any case below other than a missing `state.json` rules out the completion summary.

- **No change selected yet**: render a single row `| Change not selected | Unavailable | Unavailable | Unavailable | Unavailable |` and header values `—`. Never invent artifact rows and never pick a change just to render the table.
- **Status JSON unavailable**: if an earlier snapshot from this invocation exists, render it and add the warning `status refresh failed; snapshot may be stale`. Otherwise render the single row `| Status unavailable | Unavailable | Unavailable | Unavailable | Unavailable |`.
- **`state.json` missing**: not an error — no adversarial pass has started.
- **`state.json` unreadable or malformed**: render the full table with Unavailable in the three adversarial columns of every row and add the warning `adversarial state unreadable`. Missing and unreadable are different; never render unreadable state as Not authored.
- **Unrecognized entry status**: render `Unknown (<value>)` in the Adversarial column, `—` in the other two, and add a warning naming the artifact.
- **Entry for an artifact absent from `artifacts[]`**: add a warning naming it; do not add a row.
- **Recorded path missing on disk** (`adversarialPaths`, or a non-null `reviewPath`; a glob path counts as present when at least one file matches): keep the mapped values and add a warning naming the artifact and the missing path.

### Last action

Describe what **this invocation** did — never infer it from timestamps in `state.json`. Name rejections, partial completion, and failures plainly. Examples:

- `Created design.md.`
- `Revised design.md; rejected the proposed tasks.md revision.`
- `Authored the adversarial design and wrote its comparison review.`
- `Reconciled the design review into design.md.`
- `Created proposal.md and specs; stopped for clarification before design.`
- `Created proposal.md; failed while writing specs.`
- `No changes; status shown on request.`

### Boundaries

- Read metadata only: status JSON, `state.json`, and file existence. Never open the contents of main artifacts, adversarial artifacts, or reviews to build this report.
- Never write, create, repair, or advance any file, including `state.json`.
- Refreshing status for the report never changes an action the caller already selected.
- The report is information for the user. It grants no authority to continue the workflow.
<!-- status-contract:end -->

## Examples

Mid-planning, no adversarial work yet (main-only table):

```markdown
**Change:** `add-org-search` · **Schema:** `spec-driven`

| Artifact | Main |
|---|---|
| proposal | Done |
| design | Done |
| specs | Ready |
| tasks | Blocked |

**Last action:** Created design.md.
```

Every artifact done, no adversarial pass (completion summary):

```markdown
**Artifacts complete:** `add-org-search`
**Last action:** Created tasks.md.
```

Every main artifact done and every existing adversarial entry reconciled, but `tasks` never shadowed — the pass is unfinished, so the full table stays:

```markdown
**Change:** `add-org-search` · **Schema:** `spec-driven`

| Artifact | Main | Adversarial | Comparison | Reconciliation |
|---|---|---|---|---|
| proposal | Done | Authored | Written | Reconciled |
| design | Done | Authored | Written | Reconciled |
| specs | Done | Authored | Written | Reconciled |
| tasks | Done | Not authored | — | — |

**Last action:** Created tasks.md.
```

A skipped artifact, mixed adversarial progress, and a stale path:

```markdown
**Change:** `tune-cache-ttl` · **Schema:** `spec-driven`

| Artifact | Main | Adversarial | Comparison | Reconciliation |
|---|---|---|---|---|
| proposal | Done | Authored | Written | Reconciled |
| specs | Skipped | Not authored | — | — |
| design | Done | Authored | Not written | — |
| tasks | Ready | Not authored | — | — |

**Status warnings:** design adversarial path missing: adversarial/artifacts/design.md
**Last action:** Created design.md; stopped for clarification of the task breakdown.
```

The user explicitly asks for the status before a change is selected:

```markdown
**Change:** — · **Schema:** —

| Artifact | Main | Adversarial | Comparison | Reconciliation |
|---|---|---|---|---|
| Change not selected | Unavailable | Unavailable | Unavailable | Unavailable |

**Last action:** No changes; status shown on request.
```
