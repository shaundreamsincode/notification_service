# Design

## Context

Greenfield FastAPI and PostgreSQL service. See proposal.md for motivation and the three capability specs for behavior. The constraints that shape the approach:

- At-most-once delivery with a visible `unknown` outcome. The design must never resend a notification whose provider outcome is ambiguous, and must distinguish ambiguity from definite rejection at the provider boundary.
- Multiple workers against one table, with no shared state other than PostgreSQL.
- Templates are files in the repository, loaded by both the API and the worker, and must be validated before either process serves traffic.
- Recipients and options are PII. They live only in the `notifications` table and must not appear in logs or API responses.

## Goals / Non-Goals

**Goals:**

- One table, one claim query, one state machine, all expressible in plain SQL so the behavior can be reasoned about without a queue library.
- Every provider outcome maps to exactly one of four classes before it touches the state machine.
- The API and worker share the same template registry and options validation code so intake and send cannot disagree.
- A fake provider makes the whole pipeline testable without network access.

**Non-Goals:**

- Throughput beyond a few hundred notifications per minute. Polling with a small batch is enough.
- LISTEN/NOTIFY or any push mechanism for wake-ups.
- Per-attempt history tables, metrics, or an admin UI.
- Scheduling, send windows, or priority ordering.

## Decisions

### D1. Lease-based claiming, not a transaction held across the send

Workers claim with a short transaction that moves rows to `processing` and records a lease, then release the lock before calling the provider. The alternative, holding `FOR UPDATE` across the provider call, is simpler and self-healing on crash, but a crash that rolls the row back to `pending` would cause a resend, which violates at-most-once. With a lease, a crashed worker's row expires into `unknown`, which is the semantics the specs require. The cost is a reaper and a compare-and-set on finalize, both covered below.

Claim query, one round trip:

```sql
UPDATE notifications
SET status = 'processing',
    attempt_count = attempt_count + 1,
    locked_by = :worker_id,
    locked_at = now(),
    updated_at = now()
WHERE id IN (
  SELECT id FROM notifications
  WHERE status = 'pending' AND available_at <= now()
  ORDER BY available_at, id
  LIMIT :batch_size
  FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

Partial index: `CREATE INDEX ON notifications (available_at, id) WHERE status = 'pending'`.

`RETURNING` order is not guaranteed, so the worker sorts the claimed rows by `(available_at, id)` before processing.

A lease is stamped at claim time, but a worker processes its claimed rows one at a time, so every row after the first ages against its lease while the rows ahead of it send. A batch of ten with a 15-second deadline would leave the tenth row waiting over two minutes against a 60-second lease, and the reaper would mark never-sent rows `unknown`. The default batch size is therefore 1, and concurrency comes from running more workers. Settings enforce `batch_size × provider_deadline < lease`, which reduces to the spec's timeout-below-lease rule at batch size 1. Alternatives rejected: stamping the lease per row just before the send needs a second write per notification, and a longer lease delays crash detection.

### D2. Finalize with compare-and-set on the lease holder

Every outcome write is `UPDATE ... WHERE id = :id AND status = 'processing' AND locked_by = :worker_id`. If the row count is zero, the lease expired and the reaper already marked the row `unknown`. The worker logs the discarded outcome with the UUID and provider message id and moves on. This is what makes the late-worker guard hold without any coordination beyond the row itself.

Alternative: a per-claim token column. Rejected: worker id plus `processing` status already identifies the one live claim, because a row cannot be claimed again without first returning to `pending`.

### D3. Reaper runs inside the worker loop

Before each poll, the worker runs `UPDATE notifications SET status = 'unknown', last_error = 'lease_expired', updated_at = now() WHERE status = 'processing' AND locked_at < now() - :lease`. The value is a category code, defined in D7. Any worker can reap any row; the statement is idempotent. A separate reaper process is not worth the deployment surface at this scale. The lease is a single config value, and the settings loader refuses to start unless `batch_size × provider_deadline < lease` (see D1 and D4).

### D4. Four provider outcome classes, decided in the adapter

The provider adapter interface returns a `SendResult` with `provider_message_id` on confirmed acceptance and raises one of three exceptions otherwise:

| Class | Raised when | State machine effect |
|---|---|---|
| `PermanentRejection` | 4xx that is not a rate limit, invalid recipient, provider says never retry | `failed` |
| `TransientRejection` | 429, 5xx, connect timeout, DNS failure, connection refused | back to `pending` with backoff, or `failed` once attempts are exhausted |
| `OutcomeUnknown` | read timeout, connection dropped after the request was written, unparseable 2xx | `unknown` |

The distinction between connect timeout and read timeout is the whole at-most-once story: a connection that never opened cannot have delivered anything, so it is safe to retry; a request that was written but never answered might have. HTTP clients that separate the two (httpx does) make this classification reliable. Anything the adapter cannot classify defaults to `OutcomeUnknown`, never to a retry.

Render failures in the worker are `failed` directly; they are not provider outcomes.

Each outcome exception carries a category code from a fixed set (`provider_rate_limited`, `provider_unavailable`, `provider_rejected`, `provider_timeout`, `provider_unclassified`, `lease_expired`, `render_failed`). Adapters never place provider response text in an exception. Raw provider detail goes to logs only, subject to the no-PHI rule, and never to the database or an API response.

The provider call runs under one overall deadline, not httpx's per-phase timeouts, which apply separately to connect, write, read and pool and can add up to far more than the configured number. The adapter base class derives per-phase timeouts whose sum does not exceed the deadline and wraps the call in a wall-clock guard. If the guard fires the outcome is `OutcomeUnknown` with category `provider_timeout`, because the guard cannot tell whether the request was written. This deadline is the "provider timeout" the settings check compares with the lease.

Alternatives: retry unknown outcomes once using the provider idempotency key, rejected because dedupe semantics vary by provider and the proposal chose visible loss over duplication. Classify only from library exceptions, rejected because it leaves total call time unbounded.

### D5. Provider idempotency key is the notification UUID

Every adapter receives the UUID and passes it to the provider in whatever form that provider accepts. Providers that honor it make retries after a transient rejection harmless even if our classification was wrong. Providers that ignore it get the same key anyway so the adapter interface stays uniform. The key is the UUID alone, not UUID plus attempt, because the goal is for the provider to collapse attempts, not distinguish them.

### D6. Request idempotency via unique key plus request hash

`idempotency_key` has a unique index. The API computes `request_hash` as SHA-256 of a canonical form of the request: sorted keys, normalized recipient, numbers canonicalized by numeric value so `1` and `1.0` match, and manifest defaults not applied. The canonical form depends on nothing in the deployed template. Intake runs in this order: check the key header, run schema validation (channel and recipient), look up the key, and on a hit compare hashes and return the original or `422` with code `idempotency_key_reused`. On a miss it resolves the template, validates options, dry-run renders, and inserts. It inserts with `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING`. If no row is returned a concurrent request won: re-select by key and compare hashes as above. This avoids a unique violation aborting the transaction. Nothing is written until every validation passes, so a rejected request never consumes its key. No application-level lock and no separate idempotency table. Keys are global because there is no caller identity; when authentication lands the unique index becomes `(caller_id, idempotency_key)`.

Alternative: store the original body and compare structurally, rejected as a larger PII footprint than a hash.

### D7. Data model

```
notifications
  id                   bigserial     primary key (internal only)
  uuid                 uuid          unique, default gen_random_uuid()
  idempotency_key      text          unique, not null
  request_hash         text          not null
  channel              text          not null, check in ('email','sms')
  template_slug        text          not null
  recipient            text          not null (normalized)
  template_options     jsonb         not null
  status               text          not null, check in (pending,processing,sent,failed,unknown)
  attempt_count        int           not null default 0
  max_attempts         int           not null
  available_at         timestamptz   not null default now()
  locked_by            text          null
  locked_at            timestamptz   null
  last_error           text          null
  provider_message_id  text          null
  created_at           timestamptz   not null default now()
  updated_at           timestamptz   not null default now()
  sent_at              timestamptz   null
```

`max_attempts` is copied from config onto the row at insert so a config change does not retroactively alter in-flight retries. `last_error` stores a category code from the fixed set in D4, never free text. The API maps each code to its fixed message. Setting the column is the only way an error reaches a client, so no provider text, recipient, or option value can leak through it.

### D8. Template registry shared by API and worker

A `TemplateRegistry` is built once per process at startup by walking `templates/<slug>/` and loading each `<channel>.yaml` manifest with its sibling bodies. Two Jinja2 environments are used: one with autoescape on for `.html` bodies and one with autoescape off for `.txt` bodies and subjects, both with `StrictUndefined`, so an undefined value fails the render instead of printing as empty. Both are immutable sandboxed environments, since template files are repository-controlled but hardening is cheap. Startup validation per variant:

1. Manifest parses and has `slug`, `channel`, `options`, and `subject` for email.
2. Every required body file exists.
3. `jinja2.meta.find_undeclared_variables` on each body and the subject is a subset of the declared option names.
4. No other manifest claims the same slug and channel.
5. Option types are in the supported set (`string`, `integer`, `number`, `boolean`).

Any failure raises at import time of the registry, so neither the API nor the worker serves traffic with a broken template set.

Options validation builds a Pydantic model per variant from the manifest at load time, with `extra='forbid'` and strict types, so undeclared options and type coercion both fail. Defaults from the manifest are applied by the same model. Intake validates with this model and then performs a dry-run render of every body; the worker validates and renders with the same registry. If the worker's registry has changed since intake (a deploy), a render failure is a permanent `failed` with category `render_failed`.

The render context contains only options that were supplied or have a default. An omitted optional option with no default is left out of the context, not set to `None`, so templates guard it with `is defined`, and an unguarded reference fails under `StrictUndefined`. Rendering also enforces a non-empty, single-line subject and non-empty body parts, and uses no clocks, randomness, or other non-deterministic globals, so the intake dry-run and the send-time render agree. Render errors raised at intake are reported by field or template name only and never repeat option values.

Alternative: render at intake and persist the output, rejected because it stores PII twice and freezes content.

### D9. Synchronous SQLAlchemy with psycopg, not async

The API's endpoints are sync functions run in FastAPI's threadpool, and the worker is a plain loop. Async buys nothing here: the hot path is one short transaction per request and one provider HTTP call per notification. Sync code keeps the claim query, the compare-and-set, and the tests straightforward. SQLAlchemy Core expressions are used for the claim and reaper statements; the ORM model exists for the rest.

### D10. Worker loop and backoff

Each worker process has a UUID-based `worker_id`. The loop is: reap, claim a batch, process each row sequentially, sleep the poll interval if the batch was empty. Backoff after a transient rejection is `base * 2^(attempt_count - 1)` with full jitter, capped. Defaults: five attempts, two-second base, five-minute cap, one-second poll, batch of one, sixty-second lease, fifteen-second provider deadline. All are settings. On SIGTERM or SIGINT the worker stops claiming and lets the in-flight send finish within the provider deadline. Alternatives (LISTEN/NOTIFY wake-ups, a per-attempt history table) are non-goals.

### D11. Recipient validation at the schema layer

The request body is a Pydantic discriminated union on `channel`. The email variant validates with `email-validator` and lowercases the domain; the SMS variant parses with `phonenumbers` in strict mode and stores E.164. FastAPI's default `422` body echoes the submitted input, so a custom handler for request validation errors keeps only the field path and message type and drops `input` and `ctx`. The email variant calls `email-validator` with deliverability checks off. The SMS variant parses with no default region, so numbers without a leading `+` and country code are rejected.

### D12. Project layout

```
app/
  main.py                FastAPI app, startup builds registry
  settings.py            pydantic-settings, lease > timeout check
  db.py                  engine, session, migrations entry
  models.py              Notification ORM model
  schemas.py             request/response models, discriminated union
  api/notifications.py   POST and GET handlers
  api/errors.py          validation and key-reuse error handlers
  templates/registry.py  loader, validation, rendering, options models
  delivery/outcomes.py   SendResult and the three exceptions
  delivery/providers/    base.py, fake.py, (twilio.py, sendgrid.py later)
  delivery/worker.py     loop, claim, reap, finalize
  delivery/backoff.py
templates/
  <slug>/<channel>.yaml, <channel>.html, <channel>.txt
alembic/
tests/
```

## Risks / Trade-offs

- **Lease shorter than the work it covers** → successful or never-attempted sends recorded `unknown`. Mitigation: batch size 1 by default, one overall provider deadline, and a settings check that `batch_size × provider_deadline < lease`. Defaults leave a four-to-one margin for render and finalize.
- **Sequential batches** → later rows age against their lease while earlier rows send. Mitigation: the same check; raise concurrency by adding workers, not batch size.
- **Per-phase client timeouts** → total call time can exceed the nominal timeout. Mitigation: the overall deadline guard in D4.
- **Poll load** → idle workers query the table every poll interval. Mitigation: the partial index keeps the scan small and the interval is configurable.
- **Misclassified provider errors** → a retry after a delivered message, or an `unknown` that was safe to retry. Mitigation: adapters default to `OutcomeUnknown` when unsure, which errs toward loss, the side the product chose. Each adapter has a table-driven classification test.
- **Template deploy breaks pending rows** → permanent `failed` with a render error. Mitigation: documented rule that option contracts stay backward compatible; startup validation catches undeclared variables but cannot catch removed options still present in old rows.
- **Global idempotency keys** → two callers can collide. Mitigation: accepted for now, documented, and the index change for per-caller scoping is a one-line migration.
- **Reaper and worker race on the same row** → handled by the compare-and-set in D2; the worst case is a logged discarded outcome.
- **Clock skew between workers** → lease expiry is computed from `now()` in PostgreSQL, never from worker clocks, so skew is irrelevant.
- **Unbounded table growth** → not addressed. Terminal rows are kept forever in this change; a retention job is a later change.

## Migration Plan

Greenfield. Run Alembic migrations to create the table and index, start the API, start at least one worker. Rollback is dropping the service; there is no existing data to preserve.

## Open Questions

- Default values for max attempts, backoff, lease, and poll interval are stated in D10 as reasonable starting points. Tuning them is configuration and does not affect specs or tasks.
