# Comparison Review: proposal

**Main**: `proposal.md` (main agent's version)
**Adversary**: `adversarial/artifacts/proposal.md` (authored from the main proposal's intent and scope, framing re-derived independently)

## Grading bar

A proposal is graded on: a value-centric **Why** that states the problem and the trade being made; a **What Changes** list that is specific enough to drive specs without leaking design; **Capabilities** that are durable, cohesive boundaries a spec can own as the system grows; and an **Impact** section that is honest about operational, caller, and data consequences and names what is out of scope. Mechanics: template sections present, no implementation detail that belongs in design, length under two pages.

## Coverage comparison

| Area | Main | Adversary |
|---|---|---|
| Problem framing | Callers need one reliable way; API fast, no duplicates | Two unreliable edges (retrying callers, silent providers); one durable record with an explicit outcome |
| Delivery guarantee | At-most-once, unknown outcome marked `failed` | At-most-once, unknown outcome gets its own `unknown` status; loss must be visible |
| Statuses | `pending`, `processing`, `sent`, `failed` | Adds `unknown` |
| Intake validation | Contract check plus dry-run render | Contract check only; startup validation guarantees contract-valid options render |
| Idempotency key semantics | Header required; replay returns UUID and status | Also: global scope with no auth, rejection on body mismatch, compatibility path to per-caller scoping |
| Lease expiry and late workers | Not mentioned | Lease reaper, late worker cannot overwrite outcome |
| Capabilities | 3: intake (incl. GET), templates, delivery (incl. state machine) | 4: intake (POST only), templates, lifecycle (state machine + GET), delivery (provider send + response classification) |
| Impact: dependencies | Concrete list incl. SQLAlchemy and Alembic | Generic list; ORM deferred to design |
| Impact: operations | DB plus worker | Also: provider timeout must be shorter than lease; no admin view of failed/unknown |
| Impact: callers | Not covered | How to treat `failed` vs `unknown`; store the key you sent |
| Impact: template deploys | Not covered | Pending rows render against the deployed template; contracts must stay backward compatible |
| Out of scope | Real providers only | Providers, auth, key scoping, rate limiting, scheduling, listing |

## Material divergences

### 1. Why: naming the trade

Adversary is stronger. Main's Why promises that "a retried request or a crashed worker never produces a second message" without saying what is given up. At-most-once is a product decision that costs lost messages, and the proposal is the place to say so. Main's phrasing also slightly overclaims: a provider that ignores idempotency keys can still duplicate on its side, and the system cannot prevent that. Adversary states the trade and the condition that makes it acceptable (loss is visible).

### 2. `unknown` as a terminal status

Adversary is stronger, and this is the most consequential divergence. Under at-most-once, a caller who sees `failed` needs to know whether resubmitting with a new key is safe. Main encodes "outcome unknown" as free text in the error field, which is not machine-readable and forces callers to parse messages. A distinct `unknown` status makes the two caller actions ("resubmit" vs "do not resubmit, investigate") first-class. Cost: one more status in the state machine and in every spec that enumerates statuses. Worth it.

This ripples into the main specs already written: the delivery spec's timeout and lease-expiry scenarios currently mark the row `failed` with an outcome-unknown error, and the intake GET requirement enumerates statuses implicitly. Those must change together if this is adopted.

### 3. Dry-run render at intake

Mixed. Adversary's startup validation (every variable a body references must be declared in the manifest) is the real guarantee and belongs in the proposal; Main does not mention it at the proposal level. But Adversary's conclusion that intake therefore need not render is weaker than it looks: a declared, correctly typed option can still fail a filter at render time, and rendering is near-free. Keep Main's dry-run render as a second line of defence, and add Adversary's startup validation as the primary one.

### 4. Capability boundaries

Adversary's diagnosis is right; its remedy is too much. Main splits the state machine across two owners: statuses and transitions live in delivery, while the status view callers read lives in intake. Nobody owns the lifecycle as a whole. Adversary fixes this with a fourth capability, but its `delivery` (provider response classification) and `lifecycle` (what each class does to the row) are so coupled that every delivery scenario's THEN would live in lifecycle. That is a split along an implementation seam, not a behavioral one.

Recommended shape, three capabilities:

- `notification-intake`: `POST /notifications` only. Validation, recipient normalization, idempotency key semantics.
- `notification-templates`: unchanged.
- `notification-lifecycle` (rename of `notification-delivery`): the state machine including statuses and transitions, exclusive claiming, leases and the reaper, retry policy, provider send with the UUID as idempotency key, provider response classification, and the `GET /notifications/{uuid}` status view.

Moving GET out of intake is the substantive change. The rename is optional but makes the boundary honest; "delivery" undersells a capability that also owns the read model.

### 5. Idempotency key semantics at proposal level

Adversary is stronger. Key scope and body-mismatch rejection are externally visible contract decisions and belong in What Changes, not only in specs. The compatibility note (global today, per-caller once auth exists) is useful because it pre-empts a later breaking change.

### 6. Lease reaper and late-worker guard

Adversary is stronger by mention; Main's specs cover this but the proposal does not. One bullet in What Changes suffices.

### 7. Impact section

Adversary is stronger on operations, caller contract, template deploy compatibility, and explicit out-of-scope. Main is stronger on naming concrete dependencies, which helps the tasks artifact. Adversary's note that the ORM is a design choice is correct but does not justify dropping the list.

The "provider timeout shorter than lease" constraint deserves to be promoted: it is the one operational misconfiguration that silently converts successful sends into `unknown`.

### 8. Last error in the GET response

Adversary includes last error in the status view; Main's proposal is silent and its specs omit it. Including it is useful for callers, but raw provider error text can echo the recipient. Recommend the GET return an error category or code, with any free-text message sanitized of recipient data. This is a spec-level detail to settle during reconciliation of specs, flagged here so it is not lost.

## Merge recommendation

**Why**: replace with Adversary's framing. Keep Main's sentence about the API answering fast while delivery is asynchronous.

**What Changes**: start from Main's list and
- add the `unknown` status and define the four outcome classes (confirmed, transient rejection, permanent rejection, unknown) in one bullet;
- add startup template validation and keep dry-run render at intake;
- add idempotency key scope, body-mismatch rejection, and the per-caller compatibility note;
- add the lease reaper and late-worker guard bullet;
- move `GET /notifications/{uuid}` into the lifecycle bullet rather than listing it beside POST.

**Capabilities**: three, as laid out in divergence 4. Rename `notification-delivery` to `notification-lifecycle` and move the status view into it. Update capability descriptions accordingly.

**Impact**: keep Main's dependency list. Add Adversary's operations, callers, template deploy, and out-of-scope items. Promote the timeout-shorter-than-lease constraint.

## Blocking items

1. **Adopt or reject the `unknown` status before specs are treated as final.** The main delivery spec already encodes the opposite choice. Reconciling the proposal without updating the specs leaves the artifacts inconsistent.
2. **Settle the capability boundary before design.** The main specs exist on the three-way split with GET in intake. If the lifecycle shape is adopted, the GET requirement moves files and the delivery spec gains an explicit state machine requirement. Design and tasks should not be written against the old partition.
