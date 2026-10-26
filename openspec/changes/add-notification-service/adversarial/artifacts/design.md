# Design

## Context

Greenfield FastAPI + PostgreSQL service. See proposal.md for motivation and the at-most-once stance. Constraints that shape the approach:

- Postgres is the only stateful dependency. There is no broker, so the notifications table is the queue.
- No caller identity exists yet, so idempotency keys are global.
- Providers are behind an adapter interface; real integrations are out of scope, so the adapter contract must make the outcome classes (confirmed, transient, permanent, unknown) explicit rather than leaving classification to the worker.
- Templates ship with the deployment (files), not in the database.
- Rows may contain PII/PHI; logs and API responses must never carry recipient or option values.

## Goals / Non-Goals

**Goals:**
- Exactly one row per (idempotency key); a replay never creates a second row or second send.
- No double delivery: any ambiguity resolves to `unknown`, never to a retry.
- Every state transition is a single atomic, guarded SQL statement so concurrent workers, the reaper, and late workers have a deterministic winner.
- Template defects surface at process start, not at send time.

**Non-Goals:**
- Exactly-once delivery, a broker, a dead-letter admin view, rate limiting, auth, scheduling, listing/search.
- A pluggable template store or hot reload.
- Column-level encryption or option scrubbing.

## Decisions

### D1: Postgres table as outbox, claimed with `FOR UPDATE SKIP LOCKED`

One `notifications` table: `id` (bigint identity, internal), `uuid` (unique, public), `idempotency_key` (unique), `request_hash`, `channel`, `template_slug`, `recipient`, `template_options` (jsonb), `status`, `attempt_count`, `next_attempt_at`, `lease_expires_at`, `claimed_by`, `last_error` (category string), `created_at`, `updated_at`, `sent_at`. A partial index on `(next_attempt_at, id) WHERE status = 'pending'` serves claiming oldest-first.

Claim: one statement selecting due `pending` rows `ORDER BY next_attempt_at, id FOR UPDATE SKIP LOCKED LIMIT n` and updating them to `processing` with a lease and incremented `attempt_count`, committed before any provider call.

Alternatives: Redis/SQS queue (second system, and the dual-write problem returns); advisory locks (invisible state, no lease visibility). Integer PK keeps the claim index compact; the UUID is the only public identifier so enumeration of internal ids is impossible.

### D2: Idempotency via unique constraint, not check-then-insert

Intake does `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING`. If nothing is returned, select the existing row and compare `request_hash`; equal returns the original (202 with original UUID and current status), different returns 422 `idempotency_key_reused`. This is race-safe for concurrent identical requests, which a select-then-insert is not.

`request_hash` is SHA-256 of a canonical JSON form of the request body (sorted keys, numbers canonicalized by value, defaults not applied, template-independent). Keys are looked up before template validation so a replay still resolves after a template is removed or changed. Rejected requests (validation failure) never insert, so they do not consume the key.

Alternative: store the full original body and compare structurally; rejected as a larger PII footprint than needed, since the row already holds the effective fields but not the pre-normalization input. Hash is the compromise.

Key scoping: global column unique today; adding caller scope later is a migration to a composite unique index, which is compatible.

### D3: State machine enforced by guarded updates

Every transition is `UPDATE ... WHERE uuid = :u AND status = :expected [AND claimed_by/lease token = :mine]`, and zero rows affected means the transition lost. States: `pending -> processing -> {sent, failed, unknown, pending}`. `sent`, `failed`, `unknown` are terminal. This is the whole concurrency story: the first atomic commit wins, so a late worker finishing after the reaper marked `unknown` updates zero rows and its result is discarded (logged, not applied).

The worker must treat "zero rows" as normal, not as an error.

### D4: Outcome classification lives in the adapter contract

Adapters return one of `Confirmed`, `Transient(reason)`, `Permanent(reason)`; they raise or return `Unknown` for timeout, dropped connection, or any response they cannot classify. The worker maps: Confirmed -> `sent`; Transient -> `pending` with exponential backoff and jitter while `attempt_count < max_attempts`, else `failed`; Permanent -> `failed`; Unknown -> `unknown`. An unclassifiable result defaults to `unknown`, because the safe default under at-most-once is "might have been delivered".

The provider idempotency key is the notification UUID. This is defense in depth, not the primary guarantee, since providers' dedupe windows are finite.

`last_error` stores a sanitized category (for example `provider_rejected`, `timeout`, `lease_expired`), never the provider's raw message, which could echo recipient data.

Alternative: retry unknowns once using the provider idempotency key. Rejected: dedupe semantics vary by provider and the proposal chose visible loss over duplication.

### D5: Lease, timeout ordering, and reaper

Lease duration L (default 60s) must exceed provider timeout T (default 15s) plus commit slack; configuration validation refuses to start if `T >= L`. The reaper is a periodic loop (inside the worker process, run by every worker, safe because the transition is guarded) that sets `processing` rows with `lease_expires_at < now()` to `unknown` with `last_error = 'lease_expired'`. Use database time (`now()`) for all lease comparisons so worker clock skew is irrelevant.

A crashed worker therefore yields `unknown`, not a re-send, since we cannot know whether the provider call happened.

### D6: Templates: load once, validate eagerly, render strictly

Layout: `templates/<slug>/<channel>.yaml` manifest (options contract with types `string|integer|number|boolean`, required flag, email `subject`) plus sibling body files. Loader runs at startup (FastAPI lifespan) and builds an immutable in-memory registry keyed `(slug, channel)`; any failure aborts startup.

Startup validation: manifest well-formed; (slug, channel) unique; every referenced body exists and is non-empty; each body and subject parsed with Jinja2 `StrictUndefined` in a sandboxed environment with autoescape on for HTML; the set of variables referenced (via `jinja2.meta.find_undeclared_variables`) must be a subset of declared options; optional options used must be guarded. Unsupported option types fail startup. Subject must be single-line.

Intake validates options against the contract (required, no extras, type-checked with no coercion of strings to numbers) then dry-run renders to catch runtime-only failures, and discards the output. Nothing rendered is persisted; the worker renders again at send time so the row never holds a rendered body.

Alternative: render at intake and store. Rejected: stores PII twice and freezes content, but see Risks for the template-drift cost of the chosen approach.

### D7: Recipient validation

Email via `email-validator` with deliverability checks disabled (no DNS at intake). SMS via `phonenumbers`, requiring explicit country code (`+`), no default region, and storing E.164. Error responses never echo the recipient.

### D8: Process model

One codebase, two entry points: the API (uvicorn) and the worker (`python -m app.worker`), both reading the same config. The worker loop polls with a short sleep when idle (and `LISTEN/NOTIFY` is deliberately not used; polling is simpler and the latency target is seconds). Graceful shutdown stops claiming and lets in-flight sends finish within T.

SQLAlchemy 2.x sync engine for the worker (simple, blocking provider calls) and for the API via a threadpool-run dependency; Alembic for migrations. Adopting async everywhere adds complexity without measurable benefit at this scale.

### D9: Error and logging policy

A single exception handler shapes all error bodies as `{code, message}`; validation errors are mapped so Pydantic's default output (which echoes input values) is stripped of `input` fields. Structured logs carry notification UUID, status transitions, and error category, never recipient or options.

## Risks / Trade-offs

- [Lost notifications on ambiguity] -> Intended trade; `unknown` is first-class and documented for callers; no admin view in this change means operators must query SQL, noted as a gap.
- [Template drift for pending rows] -> Rendering at send time means a deploy between intake and send changes content or breaks rendering. Mitigate by requiring backward-compatible option contracts while rows are pending; a send-time render failure is a permanent `failed` with category `render_error`, never a retry.
- [Poll load / hot index] -> Partial index keeps the scan small; poll interval is configurable. Revisit with LISTEN/NOTIFY if latency matters.
- [Lease/timeout misconfiguration] -> Startup check enforces T < L; still a runtime hazard if the provider SDK ignores timeouts, so the adapter wrapper enforces a hard deadline.
- [Global idempotency keys] -> Cross-caller collisions are possible until auth exists; documented, compatible migration later.
- [Unbounded table growth] -> No retention in scope; flagged as follow-up.
- [PII at rest] -> Assumes encrypted storage; no column-level protection.
- [Reaper vs slow-but-successful send] -> Recorded `unknown` though actually sent; accepted under at-most-once and minimized by T < L.

## Migration Plan

Greenfield: Alembic baseline creates the table and indexes; deploy DB, run migrations, start API, then workers. Rollback is dropping the service; there is no prior data. Template changes deploy with the service and require a restart (startup validation is the gate).
