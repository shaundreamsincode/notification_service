# Design review: Main vs Adversary

Main: `openspec/changes/add-notification-service/design.md` (12 decisions, concrete SQL, schema, layout).
Adversary: `openspec/changes/add-notification-service/adversarial/artifacts/design.md` (9 decisions, prose-level).
Dependency: proposal (reconciled). The adversary did not read the reconciled specs, only the proposal; the main version cites them.

Grading bar: design explains HOW and why (alternatives considered), decisions are traceable to the proposal/specs, concurrency and at-most-once claims actually hold under the stated mechanics, and the artifact is specific enough for tasks to be derived without guessing.

## Coverage comparison

Both converge on: Postgres outbox with `FOR UPDATE SKIP LOCKED` and partial index; lease plus reaper inside the worker; guarded (compare-and-set) transitions where the first atomic commit wins; UUID as provider idempotency key; unique key plus request hash for intake idempotency, with lookup before template validation; `unknown` as the safe default for unclassifiable results; category-only `last_error`; strict Jinja2 with startup validation via `find_undeclared_variables`; sync SQLAlchemy; DB-time `now()` for leases; T < L startup check; render failure at send time is permanent `failed`; error handler that strips echoed input; no-country-code SMS rejected.

Main only: full claim SQL, full column list, `max_attempts` copied onto the row, `provider_message_id`, the connect-timeout vs read-timeout classification table, fixed error-code set with API-side message mapping, per-variant Pydantic model with `extra='forbid'`, separate autoescape environments, omit-not-None rule for optional options, deterministic render, concrete default parameters, backoff formula, file layout, fake provider, discriminated union request model, `worker_id` semantics.

Adversary only: `ON CONFLICT DO NOTHING RETURNING` as the insert primitive; graceful worker shutdown; a hard-deadline wrapper around the adapter call; explicit alternatives-considered for each decision (queue, advisory locks, retry-unknown, render-at-intake, async); an explicit risk of unbounded poll load; Alembic ordering in the migration plan; sandboxed Jinja environment.

## Material divergences

1. **Outcome classification (D4 main vs adversary D4).** Main is stronger. The adversary treats any timeout as `unknown`. Main splits connect failure (cannot have delivered, safe to retry) from read timeout or dropped connection after the request was written (might have). That distinction is the real content of at-most-once at the adapter boundary and makes the retry path usable. Adversary's blanket rule is safe but would turn ordinary connection failures into lost messages.
2. **Finalize guard.** Main is stronger: `status='processing' AND locked_by=:worker_id`, with a stated zero-rows behavior (log the discarded outcome with UUID and provider message id). Adversary named the pattern but left the guard token vague.
3. **Insert primitive for idempotency (D6 main vs adversary D2).** Roughly equal. Main does lookup, then insert, then re-select on unique violation. Adversary does `INSERT ... ON CONFLICT DO NOTHING RETURNING` then select. Both are race-safe. The adversary form is one fewer round trip on the hot path and avoids a violation aborting the transaction; main's form puts the lookup before template resolution, which the reconciled specs require. Either is acceptable; a note on the abort-on-violation cost is the only gain.
4. **Data model and claim query.** Main is stronger; adversary omits `max_attempts`, `provider_message_id`, and the exact claim statement. Main's `max_attempts` snapshot is a good decision the adversary missed.
5. **Alternatives considered.** Adversary is stronger. Main records alternatives only for D1 and D9. The design instructions ask for alternatives per decision.
6. **Template/option semantics.** Main is stronger (omit-not-None, StrictUndefined environments, single-line subject, determinism). Adversary adds a sandboxed Jinja environment, worth adopting since templates are repo-controlled but cheap to harden.

## Blocking defects found in Main (not covered by either version's convergence)

**B1. Lease starts at claim but the batch is processed sequentially (D1, D3, D10).** The claim stamps `locked_at = now()` on up to ten rows at once; the worker then sends them one at a time with a thirty-second provider timeout and a sixty-second lease. The seventh row can wait three minutes before any send is attempted, and the reaper (run by any worker, before each poll) will mark it `unknown` with `lease_expired` even though nothing was ever sent. That silently converts healthy traffic into the lost outcome the product treats as rare, and contradicts the stated "two-to-one margin". Fix options for reconciliation: batch size 1 with concurrency by worker count; or stamp the lease per row immediately before the send (a short second claim step, with the row in a `processing` state whose lease starts at send); or bound batch_size x timeout under the lease and enforce it in settings. The settings check must cover this product, not only timeout < lease.

**B2. Provider timeout is not a total deadline (D1/D3, D4).** httpx timeouts are per phase (connect, read, write, pool), so a "thirty-second timeout" can run well beyond thirty seconds in total, and the lease margin then does not hold. The adapter wrapper should enforce a single overall deadline (the adversary's hard-deadline idea) and the T < L check should refer to that deadline.

## Smaller items

- Claim ordering is `ORDER BY available_at` with no tiebreaker; add `id` for deterministic oldest-first. `RETURNING` order is not guaranteed, so sort in the worker.
- Add graceful shutdown (stop claiming, finish in-flight within the deadline) to D10.
- Reaper should also set `updated_at`.
- Add short alternatives-considered lines for D2, D4, D6, D8, D10 (for example holding retries for unknown, render-at-intake, a table-per-attempt history).
- Open question on defaults is fine; B1 changes what the default batch size and lease should be, so it is not purely configuration.

## Merge recommendation (per decision)

- D1: keep main. Add `id` tiebreaker. Resolve B1 (recommend batch size 1 default, or per-row lease start) and state it in the decision.
- D2: keep main unchanged.
- D3: keep main; add `updated_at`; extend the settings check to cover B1.
- D4: keep main entirely; add the single overall deadline requirement from B2.
- D5: keep main.
- D6: keep main order; optionally note `ON CONFLICT DO NOTHING` as the insert primitive to avoid aborted transactions.
- D7: keep main.
- D8: keep main; adopt the sandboxed environment from the adversary.
- D9, D11, D12: keep main.
- D10: keep main; add graceful shutdown; revise the default batch/lease/timeout set per B1/B2.
- Risks: replace the "two-to-one margin" mitigation with the corrected one; add the sequential-batch risk and per-phase timeout risk.
- Add alternatives-considered lines as above.

Blocking before archive: B1 and B2. Tasks derived from the current design would implement a worker that loses rows in normal operation.
