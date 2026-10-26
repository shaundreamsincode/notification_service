# Proposal

## Why

Two edges of a notification system are unreliable and outside our control: callers retry requests they already made, and providers sometimes accept a message without telling us. Without a system in between, every caller re-solves templating, retries, and duplicate suppression on its own, and nobody can say afterwards whether a given message went out. This change puts one durable record between those two edges so that each requested notification has exactly one row, one delivery attempt sequence, and one explicit outcome a caller can read back.

The product stance is at-most-once: a recipient must never receive the same notification twice, and in exchange the system accepts that some notifications will be lost when a provider's answer is unknown. That trade is only acceptable if the loss is visible, so the proposal treats "we do not know whether this was sent" as its own outcome rather than folding it into "failed".

## What Changes

- New HTTP service (FastAPI) with a PostgreSQL database. Greenfield.
- `POST /notifications` accepts `channel` (`email` or `sms`), `template_slug`, `recipient`, and `template_options`, and requires an `Idempotency-Key` header. It stores the request as a `pending` notification and returns `202 Accepted` with the notification's public UUID and status. Idempotency keys are global for now because the service has no caller identity; adding authentication later will scope keys per caller, which is a compatible change.
- A replayed key with an equivalent body returns the original notification. A replayed key with a different body is rejected, because silently returning a notification that does not match what the caller sent hides a caller bug.
- `GET /notifications/{uuid}` returns status, attempt count, timestamps, and the last error. It never returns options or rendered content.
- Recipient validation depends on channel: email syntax for email, phone numbers parsed and stored in E.164 for SMS.
- Templates live in the repository as files, one directory per slug, with a YAML manifest per channel variant that declares the typed options contract and a subject for email, and body files beside it (HTML and plain text for email, plain text for SMS). Templates are loaded and validated at process start. Startup fails if a body references an option the manifest does not declare, so an options payload that passes the contract is guaranteed to render. Intake therefore validates the contract only and does not render.
- One `notifications` table used as a transactional outbox. Each row has an internal integer primary key and a public UUID. Statuses are `pending`, `processing`, `sent`, `failed`, and `unknown`.
- Workers claim `pending` rows with row-level locks (`SELECT ... FOR UPDATE SKIP LOCKED`), move them to `processing` under a lease, render at send time, and call the channel provider with the notification UUID as the provider idempotency key.
- Outcome classification is the core of delivery: provider confirmed means `sent`; provider definitely rejected and the error is transient means back to `pending` with backoff up to a maximum attempt count; provider definitely rejected permanently means `failed`; timeout, dropped connection, or expired lease means `unknown` and the notification is never retried.
- A lease reaper marks `processing` rows whose lease expired as `unknown`. A late worker that finishes after its lease expired cannot overwrite that outcome.

## Capabilities

### New Capabilities

- `notification-intake`: Accepting a notification request, validating channel, recipient, and the template options contract, and enforcing request idempotency through the `Idempotency-Key` header, including rejection of key reuse with a different payload.
- `notification-templates`: Defining templates per slug and channel as files, declaring each variant's typed options contract, validating all templates at startup, and rendering email (subject, HTML, plain text) and SMS bodies with correct escaping.
- `notification-lifecycle`: The notification state machine: statuses and their allowed transitions, exclusive claiming by workers, leases and lease expiry, retry eligibility and backoff, and the status view callers read through `GET /notifications/{uuid}`. This is owned separately from intake and delivery so that the state machine has one owner instead of being split between the endpoint that creates rows and the worker that advances them.
- `notification-delivery`: Sending a claimed notification through its channel's provider adapter with the notification UUID as the provider idempotency key, and classifying each provider response as confirmed, transient rejection, permanent rejection, or unknown.

### Modified Capabilities

None. There are no existing specs.

## Impact

- **New code**: the API, a worker process, a lease reaper (which can run inside the worker loop), the template loader, provider adapters with a fake adapter for tests, and migrations.
- **New dependencies**: FastAPI, Pydantic, a PostgreSQL driver and migration tool, Jinja2, PyYAML, `email-validator`, and `phonenumbers`. The ORM choice is a design decision; the claim query needs hand-written SQL either way.
- **Operational**: a PostgreSQL instance and at least one worker process. The worker's provider timeout must be shorter than the lease, or a slow but successful send will be recorded as `unknown` and lost. There is no admin view of `failed` or `unknown` notifications in this change; operators query the table directly.
- **Callers**: must store the `Idempotency-Key` they send and must treat `unknown` as "possibly delivered, do not resubmit" and `failed` as "not delivered, safe to resubmit with a new key".
- **Templates and deploys**: pending rows are rendered with whichever template version the worker has loaded. A template change that removes or renames a required option can break notifications accepted before the deploy. Template contracts must stay backward compatible while rows can be pending.
- **Data**: recipients and template options are PII and may be PHI. This change relies on database encryption at rest and keeps options and rendered bodies out of logs and API responses. Column-level encryption and options scrubbing after a terminal state are out of scope.
- **Out of scope**: real provider integrations, authentication, per-caller key scoping, rate limiting, scheduling or send windows, and listing or searching notifications.
