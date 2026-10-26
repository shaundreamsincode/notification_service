# Notification service

A FastAPI and PostgreSQL service that internal systems call to send templated
SMS and email: one `POST` with a template slug, a recipient and options, and a
`GET` to read back what happened. The service owns intake, storage, templating,
delivery and failure handling; the actual send is stubbed behind a provider
interface, so no message leaves the machine.

Docs: [API](docs/api.md), [operations](docs/operations.md), [templates](templates/README.md).

## Design

- **Transactional outbox.** `POST /notifications` validates, writes one row and
  returns `202`. Workers deliver asynchronously. A caller never waits on a vendor.
- **Claim with `FOR UPDATE SKIP LOCKED`.** Workers share nothing but Postgres:
  no queue, no coordinator. Throughput comes from adding workers.
- **Lease and reaper.** A claimed row whose lease expires becomes `unknown` and
  is never retried, because a worker that died mid-send may have succeeded.
- **At-most-once delivery.** Losing a reminder beats double-sending a password
  reset. The loss is visible: `unknown` is its own status, not a kind of failure.
- **Idempotency.** `Idempotency-Key` has a unique constraint, inserts use
  `ON CONFLICT DO NOTHING`, and a canonical request hash decides equivalence.
  Same key with a different body is `422`; concurrent duplicates get one row.
- **Provider outcomes are classified before they touch the state machine:**
  transient (retry with backoff), permanent (`failed`), or unknown. A timeout is
  unknown, not retried, because the request may have been written.
- **Templates are files.** A YAML manifest declares the options contract, with
  Jinja `.html`/`.txt` bodies beside it. API and worker validate every template
  at startup and refuse to boot on a bad one, so intake and send cannot disagree.
- **PII stays in one table.** Error bodies and logs never echo a recipient, an
  option value, rendered content or provider text; `last_error` is a fixed code.
- **Public IDs are UUIDs.** The integer primary key never leaves the database.

The reasoning behind each is in
[design.md](openspec/changes/add-notification-service/design.md).

## Run it

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/) and a local PostgreSQL.

```bash
uv sync
createdb notifications
export DATABASE_URL=postgresql+psycopg://localhost/notifications   # the default; adjust if needed
uv run python -m alembic upgrade head
```

In two terminals (same `DATABASE_URL`):

```bash
uv run python -m uvicorn app.main:app --port 8000
uv run python -m app.delivery.worker
```

The worker uses the fake provider: it confirms every send and delivers nothing.

```bash
curl -sS -X POST http://localhost:8000/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d '{"channel":"sms","template_slug":"appointment_reminder","recipient":"+14155550123","template_options":{"patient_name":"Ann","appointment_time":"Mon 9:00"}}'
# {"uuid":"<uuid>","status":"pending"}

curl -sS http://localhost:8000/notifications/<uuid>
# status is "sent" once the worker has picked it up (within a second)
```

Repeat the `POST` and you get the same UUID back. Change the recipient and keep
the key and you get `422 idempotency_key_reused`. Other settings
(`NOTIF_MAX_ATTEMPTS`, `NOTIF_LEASE_SECONDS`, ...) are in
[docs/operations.md](docs/operations.md).

## Tests

```bash
createdb notifications_test
export TEST_DATABASE_URL=postgresql+psycopg://localhost/notifications_test
uv run python -m pytest
```

The suite drops and recreates the `public` schema of that database each
session, so point it at one you can lose. It covers intake validation, idempotency
races (threaded, repeated), the worker state machine, lease expiry, backoff,
template loading and rendering, migrations, and end-to-end runs through the fake
provider. Database tests are skipped when `TEST_DATABASE_URL` is unset.

## How it was built

Spec-first with [OpenSpec](https://github.com/Fission-AI/OpenSpec): a proposal,
then an adversarial review of it, then specs, design and a task list, each
reviewed the same way, then implementation straight from the task list. The
artifacts are in
[openspec/changes/add-notification-service/](openspec/changes/add-notification-service/)
and the reviews in
[adversarial/reviews/](openspec/changes/add-notification-service/adversarial/reviews/).

## Next steps

- Scope the idempotency key by calling service (a required `X-Caller` header
  folded into the uniqueness), since two internal systems could otherwise
  collide on the same key.
- Per-provider rate limiting (token bucket in Postgres or Redis), so a backlog
  after an outage doesn't flood the vendor, and because providers have
  steady-state limits too.
- OpenTelemetry tracing from intake through send, plus a `notification_events`
  table, so "a member says they didn't get it" is answerable from the UUID.
- Real SendGrid and Twilio adapters on the existing `HttpProvider` base, which
  already owns the deadline and outcome classification.
- An admin/ops view over `failed` and `unknown` rows, since today they are
  only reachable with SQL.
- Scheduled sends (`send_at`) and a cancel endpoint for not-yet-claimed rows,
  both cheap on top of `available_at` and the claim predicate.
- Dead-letter handling and alerting on rising `unknown` counts, which is the
  signal that a lease or deadline is mis-sized.
