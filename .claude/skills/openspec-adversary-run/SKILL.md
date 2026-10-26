---
name: openspec-adversary-run
description: "Dispatcher protocol for the run path (/run-adversary and
  /run-inline-adversary): resolve the change, read adversarial/state.json and
  OpenSpec status, then select and perform an action per the priority order
  (compare, or author) — and, when an authored artifact's main version is
  already done, chain the author straight into its compare (automatically when
  spawned, after asking when inline). If a review is pending reconciliation it
  reports that and stops; reconcile is not on this path."
---
Advance the adversarial OpenSpec workflow by one dispatched action — plus, in one specific case, the sanctioned **author→compare chain**. This is the dispatcher for the run path — `/run-adversary` (a main-agent wrapper that spawns you as a subagent) and `/run-inline-adversary` (inline in the current session): resolve which change to work on, read `adversarial/state.json` and the OpenSpec status, then select and perform an action — **compare** or **author** — by delegating to that action's detailed protocol. When you select **author** and the shadowed artifact's main version is already `done`, you then chain directly into the **compare** of that same artifact (automatically when spawned; after asking the user when inline). No other chaining exists: a selected **compare** never chains into an author, and you never perform more than the one author→compare pair.

Reconcile is NOT on this path. Reconciliation edits the main artifacts and is performed only by the `/reconcile-adversary` main-agent command. If a review is pending reconciliation, this dispatcher reports it and stops (see "Selecting the action").

You run as the `openspec-adversary` agent — either as an isolated subagent (spawned by the `/run-adversary` main-agent wrapper) or inline in the current session (invoked by `/run-inline-adversary`). The protocol is identical for both; only the final reporting differs (see "Reporting"). The two run-path actions, each with exactly one protocol skill, are:

- **Compare** → `openspec-adversary-compare`.
- **Author** → `openspec-adversary-author`.

## 🛑 Load only the protocol skill(s) the action needs 🛑

You MUST resolve the change and select the action using ONLY this dispatcher skill. The state read below needs no protocol skill.

Once you have selected the action, load the protocol skill for that action and follow it to completion:

- If you selected **compare**, you MUST load `openspec-adversary-compare` and you MUST NOT load `openspec-adversary-author`.
- If you selected **author**, you MUST load `openspec-adversary-author`. You MUST NOT load `openspec-adversary-compare` up front — the ONLY case where you may load it is the sanctioned author→compare chain, and only **after** the author protocol has finished (its adversarial artifact written and its `state.json` entry marked `authored`) and the chain gate has passed (see "Selecting the action").
- You MUST NOT load `openspec-adversary-reconcile` from this dispatcher at all — reconcile is not a run-path action.

**The status reporter is not a protocol skill.** You MUST load `openspec-adversary-status` before rendering a status report, which happens only when one is due (see "Reporting"). It is read-only, may be loaded at any point, and grants no permission to perform any action; it does not relax any rule above.

You MUST NOT load a protocol skill "for reference," "to compare," or "to be safe." Loading a protocol skill you are not about to act on is a protocol violation: it wastes context and risks bleeding one action's rules into another. Load `openspec-adversary-compare` mid-chain ONLY when the author step is complete and the gate says to proceed — never before.

**Input**: Optionally a change name. If omitted, resolve the change by inference (see "Resolving the change") — do NOT reflexively ask the user.

---

## Resolving the change

Resolve the target change using this ladder. **Stop at the first rule that resolves**, and do NOT ask the user unless you reach rule 4 (or the escape hatch fires).

1. **Explicit argument.** If a change name was passed to the command, use it.
2. **Session inference.** Scan the conversation for the change already in active use this session — an artifact you authored, compared, or reconciled earlier; a change the user has been discussing; an OpenSpec change worked on so far. The default assumption is **continuity**: the user is continuing the same change they have been working on. Never assume they switched to a different change without an explicit signal in the conversation. If one change is clearly the session's active subject, use it.
3. **Single change in the system.** Run `bun openspec list --json`. If exactly one active (non-archived) change exists, use it — there is nothing to disambiguate. (If the user wanted a different change they would have created or named one.)
4. **Ask.** Only if there are multiple changes, no argument, and no session signal: present the changes via the `question` tool and ask which to work on.

**Escape hatch.** The only reason to ask despite a rule 2 or 3 inference is a genuine, articulable concern that acting on the inferred change would cause real harm — for example, the inferred change's state is inconsistent with the action in a way that strongly suggests a mismatch. Plain ambiguity is not enough; continuity wins. "I am truly concerned this is the wrong change," not "I would like to double-check."

Confirm the change directory exists at `openspec/changes/<name>/`. If not, tell the user and stop.

---

## Selecting the action

Load `adversarial/state.json` if it exists (treat a missing file as `{ "change": "<name>", "schema": "<schemaName>", "entries": {} }`). Run `bun openspec status --change "<name>" --json` to get `artifacts[]` (each `{ id, outputPath, status }`), `isComplete`, and `applyRequires`.

Choose the action by this priority order. Only AFTER the action is selected do you load its protocol skill (per the loading rule above):

1. **Compare** — if any entry has `status: "authored"` AND that artifact's main `status` is `"done"`. If more than one qualifies, prefer the earliest in `artifacts[]` order. Load and follow ONLY `openspec-adversary-compare` for that entry, then stop. A selected compare NEVER chains into an author.
2. **Author** — else pick the FIRST artifact, in `artifacts[]` order, that has NO entry in `state.json` and whose main `status` is eligible: for `proposal` that means `"done"` only — it is the one artifact authored from the finished main version, per the author protocol's `proposal` exception — and for every other artifact it means `"ready"` or `"done"`. Never `"blocked"`, never not-started. If the only otherwise-eligible artifact is `proposal` with main `status: "ready"`, stop and report that the main `proposal.md` is not finished. Load and follow `openspec-adversary-author` for the chosen artifact, including its dependency-reconciliation gate and authoring isolation rule. **After the author protocol completes** (adversarial artifact written, `state.json` entry marked `authored`), apply the **author→compare chain gate** using the dispatch-time status you already read for this same target:
   - If the target's main `status` is `"ready"` (the main version is not finished), **stop** — there is nothing to compare against yet. Report the authored artifact.
   - If the target's main `status` is `"done"`, chain into the compare of this just-authored entry:
     - **Spawned via `/run-adversary`** (isolated subagent): chain automatically — load `openspec-adversary-compare` and compare this entry, with no question (a subagent has no reliable channel to ask the user).
     - **Inline via `/run-inline-adversary`**: the author wrote adversarial content, so render the status report (refreshed after the author's `state.json` write), then ask the user via the `question` tool — the main artifact is already `done`; offer *Proceed to compare now (Recommended)* / *Stop here*. Chain into `openspec-adversary-compare` only on a proceed answer; otherwise stop and report the authored artifact without rendering the status report again.
   - The compare protocol's own readiness check (it re-runs `openspec status` and requires main `done`) is the final gate; if it fails, report and stop.
3. **Stop** — else nothing is eligible on the run path. Report the precise reason and stop:
   - a `pending_reconciliation` entry exists — reconcile is NOT a run-path action; report that the review is awaiting reconciliation, and do not touch the main artifacts yourself;
   - authored entries exist but their main artifacts are not yet `done` (waiting on the main agent to finish);
   - the next artifact is `blocked` or not-started (main agent must advance dependencies first);
   - `isComplete` is true and every artifact already has a `reconciled` entry (the adversarial pass is finished).

A `pending_reconciliation` entry does NOT block a compare or author that is otherwise eligible — only fall to Stop's reconcile branch when there is no eligible compare or author to perform. Perform the selected action **in full** by following its protocol skill; the author→compare chain above is the only permitted second action, and it runs only after the author step's `state.json` write is complete. Never reconcile.

---

## The protocol skills define the details

Each run-path action's protocol skill is authoritative for that action — follow it exactly:

- **`openspec-adversary-compare`** (compare): reads the authored adversarial artifact and the now-`done` main artifact; writes `adversarial/reviews/<id>-review.md`; advances the entry to `pending_reconciliation`. Never edits any artifact.
- **`openspec-adversary-author`** (author): authors one adversarial artifact under the dependency gate and the isolation rule — blind to the main version for every artifact except `proposal`, which is authored from the finished main proposal — and marks the entry `authored`. Never writes a review.

Reconcile (`openspec-adversary-reconcile`) is intentionally absent here — it is not a run-path action. It runs only via the `/reconcile-adversary` main-agent command.

When a protocol skill says "if omitted, ask which change to work on," that step is already satisfied — the change was resolved here. Do not re-ask.

---

## Reporting

Reporting depends on which command invoked you.

A single report covers everything you did this invocation — an author alone, or an author followed by its chained compare. When this invocation wrote an adversarial artifact or a comparison review that has not yet been reported, the report includes the `openspec-adversary-status` report, rendered per its contract from status refreshed after this invocation's last write; its **Last action** names what this invocation did. A stop or failure before any such write — including every stop in "Selecting the action" — states the reason in prose and renders no status report, unless the user explicitly asked for the status. The report names no next step.

When **spawned by `/run-adversary`** (isolated subagent), end your final message with a report stating only what you authored and/or reviewed (or why nothing was eligible), then the status report when one is due, then the **verbatim STOP relay directive** defined in the `openspec-adversary` agent prompt (its single source of truth) as the final element. The relayed report must contain no "next step", recommendation, or directive the main agent could execute — not even "run `/run-adversary` again" or "run `/reconcile-adversary`". Any next action is the user's to choose.

When invoked by **`/run-inline-adversary`** (inline in the current session) you are speaking directly to the user, not relaying to a separate main agent — do NOT emit the isolated-subagent STOP relay directive. Report directly to the user what you authored and/or reviewed (or why nothing was eligible), then the status report when one is due, then stop. Do not recommend next steps, and do not chain beyond the sanctioned author→compare pair.

In both cases, if you stopped because a review is pending reconciliation, state that plainly — but do not reconcile.

---

## Anti-patterns — DO NOT

- **Do NOT ask which change** when the argument, the session, or a single existing change already answers it. Asking when the answer is obvious is the primary failure to avoid.
- **Do NOT assume a different change** than the one the session has been working on without an explicit signal.
- **Do NOT perform any action beyond the sanctioned author→compare pair.** A selected compare stops after one review; an author may chain into the compare of that same artifact and nothing more.
- **Do NOT chain compare→author.** The chain runs in one direction only: author, then compare of that same artifact.
- **Do NOT begin the compare before the authored artifact and its `state.json` write are complete.** For every artifact except `proposal` that also means not reading the main version at all until then — reading it early defeats authoring isolation. (The `proposal` exception lets the author read the main proposal as its intent input; comparison work still waits.) The chain runs the exact sequence two separate invocations would.
- **Do NOT auto-chain when running inline.** Inline into compare requires the user's explicit proceed answer; only the spawned subagent chains automatically.
- **Do NOT reconcile, ever, on this path.** Reconcile edits the main artifacts and is the `/reconcile-adversary` command's job. If a review is pending, report it and stop.
- **Do NOT load `openspec-adversary-reconcile` here, and do NOT load `openspec-adversary-compare` up front for an author action.** Load the selected action's skill; load the compare skill mid-chain ONLY after the author step completes and the gate passes.
- **Do NOT change the priority order.** Compare clears the path for the next author. Draining pending compares first keeps dependencies trustworthy.
- **Do NOT skip a protocol skill's own gates** (dependency gate, isolation rule, summary-then-approve). The dispatcher selects the action; the protocol skill governs how it is done.
