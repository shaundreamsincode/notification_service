---
name: openspec-adversary-author
description: "Authoring protocol: independently author the next OpenSpec
  artifact without reading the main agent's version — except proposal, which
  waits for the finished main proposal and is authored from it — trusting only
  reconciled dependencies, and record it as authored in the
  adversarial/state.json manifest. Stops before writing any comparison review."
---
Independently author an adversarial copy of the next OpenSpec artifact. For every artifact except `proposal` you do this **without ever looking at the main agent's version**; `proposal` is the single exception — it is authored from the finished main proposal (see Rule 2). This is the AUTHORING action of the adversarial flow: you write only the adversarial artifact and stop. The comparison review is written later, by the comparison protocol (`openspec-adversary-compare`), once the main artifact is done.

Your job is to break groupthink: start from the same inputs as the main agent and arrive at your own artifact independently. Where you diverge is where the risk lives. Because authoring and comparison are separate steps, you can author **before the main agent has finished** the artifact you are shadowing — the main version may not even exist yet, and that is fine. `proposal` is the exception: it is the first artifact and has no dependency artifacts, so nothing but the main proposal states what the change is actually about. For it you wait, and you read it (Rule 2).

**Input**: A change name. When invoked via `/run-adversary`, the dispatcher (`openspec-adversary-run`) has already resolved the change — use it and do not re-ask. If you somehow have no change, resolve it by inference per the dispatcher's ladder (explicit → session → single existing change → ask only as a last resort); do not reflexively ask.

**Status report**: Render the `openspec-adversary-status` report only once the adversarial artifact has been written (see "Hand back to the dispatcher"), or when the user explicitly asks for the status. Stops before that write — an unfinished main proposal, nothing eligible, or an unreconciled dependency — state the reason in prose with no status report. Building the report reads metadata only — status JSON, `state.json`, and file existence — so it never breaks Rule 2.

---

## 🛑 THE TWO IRON RULES 🛑

**Rule 1 — Trust only reconciled dependencies.** Before authoring an adversarial artifact, every dependency artifact that already has an adversarial entry MUST be marked `reconciled` in `adversarial/state.json`. If any dependency entry is `authored` or `pending_reconciliation`, STOP and report which dependency is not yet reconciled. You author against the reconciled, trustworthy state of the change — never against a dependency whose own divergences are unsettled. A dependency with no adversarial entry at all is acceptable — it means the adversary hasn't shadowed it; you trust the main version as-is.

**Rule 2 — Isolation while authoring, with one exception for `proposal`.** For every artifact **other than `proposal`**, you MUST NOT read the main agent's version of the artifact you are shadowing (its `outputPath` under the change root) — not now, not ever while authoring. The main artifact may not even exist yet when you author. Reading it would defeat the entire purpose: you would converge on the main agent's framing instead of deriving your own. The comparison against the main version happens in the comparison action (`openspec-adversary-compare`), never here.

**The `proposal` exception.** `proposal` is the first artifact and has no dependency artifacts, so without the main proposal your only signal about the change is its name — far too little to author anything useful, and enough to send you writing a proposal for a different change entirely. Therefore, when the target is `proposal`: its main `proposal.md` MUST be `done`, and you MUST read it, treating it as the authoritative statement of **what change is being made and why**. Take the intent and scope from it and nothing else — re-derive the problem framing, alternatives, risks, and success criteria yourself, and challenge the main proposal's framing rather than restating it. An adversarial proposal that paraphrases the main one is worthless. You still write only under `adversarial/`, and you still do not compare, critique, or review the main proposal here; that is the comparison action's job.

If you are ever about to violate either rule, STOP and re-read this section.

---

## Paths

All adversarial material lives under the change directory:

```
openspec/changes/<change>/adversarial/
  state.json
  artifacts/
    proposal.md
    design.md
    tasks.md
    specs/<capability>/spec.md     # mirrors the main specs/ tree
  reviews/
    <artifact-id>-review.md        # written by the comparison action, not here
```

Paths recorded in `state.json` are relative to the change directory (e.g. `adversarial/artifacts/design.md`).

---

## Steps

### 1. Resolve the change

- If a change name was given as input, use it.
- Otherwise run `bun openspec list --json`, present the changes via the `question` tool, and ask which one to work on.
- Confirm the change directory exists at `openspec/changes/<name>/`. If not, tell the user and stop.

### 2. Read the change status and pick the target artifact

Run:

```bash
bun openspec status --change "<name>" --json
```

This returns `artifacts[]` (each `{ id, outputPath, status }`, where `status` is one of `done`, `ready`, `blocked`, or not-started), `isComplete`, and `applyRequires`. A `blocked` artifact also carries `missingDeps[]`.

Load `adversarial/state.json` if it exists (see schema below); treat a missing file as `{ "change": "<name>", "schema": "<schemaName>", "entries": {} }`.

**Pick the target** (when not already selected by the dispatcher) = the FIRST artifact, in `artifacts[]` order, that satisfies ALL of:

1. its main `status` is eligible — for `proposal` that means `"done"` and nothing else (per Rule 2 you author it from the finished main proposal); for every other artifact it means `"ready"` or `"done"`. **Never `"blocked"`** (a blocked artifact has missing main dependencies, so there is nothing trustworthy to author against), and never not-started;
2. it has no entry in `state.json` (no `authored`, no `pending_reconciliation`, no `reconciled` entry for it).

Authoring a non-`proposal` artifact against a `"ready"` main version that does not yet exist is the intended parallelism — do it. You are not blocked by the main artifact being unfinished.

- If the only otherwise-eligible artifact is `proposal` and its main `status` is `"ready"`, report that the main `proposal.md` is not finished — until it exists there is nothing that says what this change is about — then stop.
- If every eligible artifact already has an entry and the next artifact is `blocked` or not-started, report that there is nothing new to shadow until more main dependencies are `done`, then stop.
- If `isComplete` is true and every artifact already has an entry, report that there is nothing left to author and stop.

### 3. Get the artifact instructions and apply the dependency gate

Run:

```bash
bun openspec instructions <target-id> --change "<name>" --json
```

Key fields:
- `outputPath` — the main artifact's path. Record it for later. Per Rule 2 you do not open it — unless the target is `proposal`, the one artifact whose main version you read.
- `template` — the structure to fill.
- `context` — project constitution (a constraint for you; never copy into the file).
- `rules` — artifact-specific rules (constraints for you; never copy into the file).
- `instruction` — schema-specific guidance.
- `dependencies[]` — each `{ id, done, path, description }`.

**Apply Rule 1 (dependency gate) now.** For every entry in `dependencies[]`: if `state.json` has an entry for that dependency id, it MUST be `reconciled`. If any is `authored` or `pending_reconciliation`, STOP and report exactly which dependency is not yet reconciled. A dependency with no adversarial entry at all is acceptable — you trust the main version as-is.

### 4. Author the adversarial artifact (Rule 2 in force)

Read ONLY:
- the `template`, `context`, `rules`, `instruction` from step 3;
- `openspec/specs/spec-governance/spec.md` and any permanent `openspec/specs/**` the instructions reference;
- the **reconciled main dependency artifacts** at their `dependencies[].path` (these are trustworthy per the gate);
- **only when the target is `proposal`**: the main `proposal.md` at `outputPath`, as the statement of change intent and scope (Rule 2's exception).

For every other target, do NOT read the target's `outputPath`. Derive your own artifact from first principles, applying `context` and `rules` as constraints. Write it to the adversarial location:

- `proposal` → `adversarial/artifacts/proposal.md`
- `design` → `adversarial/artifacts/design.md`
- `tasks` → `adversarial/artifacts/tasks.md`
- `specs` → one file per capability under `adversarial/artifacts/specs/<capability>/spec.md`, mirroring the main `specs/` tree.

Do not copy `context`, `rules`, or `<project_context>` blocks into the artifact — they guide what you write, never appear in output.

### 5. Update the state manifest

Upsert the `state.json` entry for the target artifact with `status: "authored"` and `reviewPath: null` (no review exists yet). Record `mainPaths` (the target `outputPath`, even if it does not exist yet) and `adversarialPaths` so the comparison action can locate both sides later. Write `state.json` (pretty-printed, 2-space indent). See the schema below.

### 6. Hand back to the dispatcher

You have authored exactly one adversarial artifact. The authoring action never writes a comparison review — that remains exclusively the comparison action's job — and never reads its target's main version, save for the `proposal` exception in Rule 2. Once the `state.json` entry is written (step 5), control returns to the `openspec-adversary-run` dispatcher, which decides whether to chain:

- If the shadowed artifact's main version is already `done`, the dispatcher chains into the comparison action for this same artifact (automatically when spawned via `/run-adversary`; after asking the user when inline via `/run-inline-adversary`). Rule 2 has done its job — the artifact was derived independently — so the comparison action may now read the main version and judge the two side by side.
- If the main version is not yet `done`, the dispatcher stops after authoring; the comparison review is written on a later invocation, once the main artifact is `done`.

Whichever happens, do NOT author a second artifact, and do NOT write the comparison review from within this authoring action. If you are not chaining, report to the user (or relay when spawned):
- which artifact you shadowed and where the adversarial artifact lives;
- whether the corresponding main artifact is already `done` or still in progress;
- the `openspec-adversary-status` report, refreshed after the `state.json` write.

The report names no next step, inline or spawned — the user decides what happens next. When the dispatcher chains into compare, the single combined report is rendered at the end of the compare instead.

---

## `state.json` schema

```json
{
  "change": "<change-name>",
  "schema": "<schemaName from status>",
  "entries": {
    "<artifact-id>": {
      "artifactId": "<artifact-id>",
      "mainPaths": ["<outputPath relative to change root>"],
      "adversarialPaths": ["adversarial/artifacts/<...>"],
      "reviewPath": null,
      "dependencies": ["<dependency artifact id>", "..."],
      "status": "authored",
      "authoredAt": "<ISO 8601 timestamp>",
      "comparedAt": null,
      "reconciledAt": null,
      "notes": ""
    }
  }
}
```

- `status` is exactly one of `"authored"`, `"pending_reconciliation"`, or `"reconciled"`.
- `reviewPath` is `null` until the comparison action writes the review.
- For `specs`, `mainPaths` and `adversarialPaths` list every per-capability file (or the glob root if the schema uses one); record enough to locate all spec files.
- `dependencies` is the list of `dependencies[].id` from the instructions output.
- The authoring action only ever writes `status: "authored"` (with `authoredAt`). The comparison action (`openspec-adversary-compare`) sets `pending_reconciliation` + `comparedAt` + `reviewPath`. The reconciliation action (`openspec-adversary-reconcile`) sets `reconciled` + `reconciledAt` + `notes`.

---

## Anti-patterns — DO NOT

- **Do NOT read the main artifact** — except the main `proposal.md` when the target is `proposal`. That is Rule 2. Authoring is otherwise blind to the main version; the comparison is the comparison action's job.
- **Do NOT paraphrase the main proposal** when authoring the adversarial `proposal`. Take intent and scope from it, then derive your own framing and challenge its assumptions.
- **Do NOT author against an unreconciled dependency.** That is Rule 1.
- **Do NOT author against a `blocked` or not-started artifact.** Only `ready` or `done` targets are eligible — and `proposal` only when `done`.
- **Do NOT write a comparison review here.** The authoring action authors only; the comparison action reviews.
- **Do NOT edit any main artifact** (`proposal.md`, `design.md`, `specs/**`, `tasks.md`) or anything outside `adversarial/`.
- **Do NOT produce more than one authored artifact per invocation.**
- **Do NOT copy `context`/`rules`/constitution text into the artifact.**
- **Do NOT skip the state manifest update** — without it the comparison and reconciliation actions cannot function.
