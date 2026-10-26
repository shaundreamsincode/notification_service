# Proposal

## Why

Two edges of a notification system are outside our control: callers retry requests they already made, and providers sometimes accept a message without telling us. This change puts one durable record between those edges so each requested notification has exactly one row, one attempt sequence, and one explicit outcome a caller can read back. The API accepts fast and delivery happens asynchronously.

The product stance is at-most-once: a recipient must never receive the same notification twice, and in exchange some notifications will be lost when a provider's answer is unknown. That trade is acceptable only if the loss is visible, so "we do not know whether this was sent" is its own outcome rather than a kind of failure.

## What Changes

- New FastAPI service backed by PostgreSQL. Greenfield.
- `POST /notifications` takes `channel`, `template_slug`, `recipient`, and `template_options`, requires an `Idempotency-Key` header, stores a `pending` notification, and returns `202 Accepted` with the notification's UUID and status.
- Idempotency keys are global for now because there is no caller identity. A replayed key with an equivalent body returns the original notification. A replayed key with a different body is rejected. Per-caller key scoping arrives with authentication later and is a compatible change.
- Recipient validation per channel: email syntax for email, phone numbers normalized to E.164 for SMS.
- File-based templates: one directory per slug, a YAML manifest per channel variant declaring a typed options contract and an email subject, with bodies in sibling files (HTML and plain text for email, plain text for SMS). All templates are loaded and validated at process start, and startup fails if a body references an option the manifest does not declare.
- Intake rejects requests whose slug and channel match no template, that miss required options, that pass undeclared or mistyped options, or whose options fail a dry-run render.
- One `notifications` table as a transactional outbox with statuses `pending`, `processing`, `sent`, `failed`, and `unknown`. Each row has an internal integer primary key and a public UUID. The API identifies notifications only by UUID.
- Workers claim `pending` rows with row-level locks under a lease, render at send time, and send with the notification UUID as the provider idempotency key.
- Provider outcomes drive the state machine: confirmed is `sent`; transient rejection returns to `pending` with backoff up to a maximum attempt count; permanent rejection is `failed`; timeout, dropped connection, or expired lease is `unknown` and is never retried.
- A lease reaper marks expired `processing` rows `unknown`. A worker finishing after its lease expired cannot overwrite that outcome.
- `GET /notifications/{uuid}` returns status, attempt count, timestamps, and a last error that never contains recipient or option values.

## Capabilities

### New Capabilities

- `notification-intake`: Accepting a request on `POST /notifications`, validating channel, recipient, and the template options contract, and enforcing idempotency through the `Idempotency-Key` header, including rejection of key reuse with a different payload.
- `notification-templates`: Defining templates per slug and channel, declaring each template's options contract, validating all templates at startup, and rendering email (subject, HTML, plain text) and SMS bodies.
- `notification-lifecycle`: The notification state machine and its transitions, exclusive claiming, leases and lease expiry, retry policy, sending through a channel provider with the UUID as idempotency key, classification of provider outcomes, and the status view callers read through `GET /notifications/{uuid}`.

### Modified Capabilities

None.

## Impact

- **New code**: the whole application, including the API, worker process, lease reaper, template loader, provider adapters, and database migrations.
- **New dependencies**: FastAPI, Pydantic, SQLAlchemy, Alembic, a PostgreSQL driver, Jinja2, PyYAML, `email-validator`, and `phonenumbers`.
- **Infrastructure**: a PostgreSQL database and at least one worker process running beside the API.
- **Operational constraint**: the provider call timeout must be shorter than the lease, or a slow but successful send is recorded `unknown` and lost. There is no admin view of `failed` or `unknown` rows in this change.
- **Callers**: must keep the key they sent, treat `failed` as safe to resubmit with a new key, and treat `unknown` as possibly delivered.
- **Template deploys**: pending rows render against the deployed template. Option contracts must stay backward compatible while rows can be pending.
- **Data**: the database stores recipients and template options, which can contain PII or PHI. This change assumes the database is encrypted at rest and adds no column-level protection.
- **External systems**: SMS and email providers sit behind an adapter interface.
- **Out of scope**: real provider integrations, authentication and per-caller key scoping, rate limiting, scheduling, options scrubbing, and listing or searching notifications.
