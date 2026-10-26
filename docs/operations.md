# Operations

## Processes

| Process | Command |
|---|---|
| Migrate the database | `uv run python -m alembic upgrade head` |
| API | `uv run python -m uvicorn app.main:app` |
| Worker | `uv run python -m app.delivery.worker` |

Run the API and at least one worker against the same database. Add throughput by
running more workers, not by raising `NOTIF_BATCH_SIZE`: workers share nothing
but PostgreSQL, and claim rows with `FOR UPDATE SKIP LOCKED` so no two take the
same notification.

The API and the worker both load and validate every template at startup and
refuse to start if one is invalid, naming it. See `templates/README.md`.

**The worker currently uses a fake provider.** Real SMS and email adapters are a
later change. The fake confirms every send without delivering anything, and the
worker logs a warning saying so at startup. Do not point production traffic at it.

## Configuration

All settings are environment variables. The first is the only one without the
`NOTIF_` prefix.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://localhost/notifications` | SQLAlchemy URL. |
| `NOTIF_TEMPLATE_DIR` | `templates/` in the repository | Directory of templates. |
| `NOTIF_MAX_ATTEMPTS` | `5` | Send attempts before a transiently rejected notification is `failed`. Copied onto each row when it is accepted. |
| `NOTIF_BACKOFF_BASE_SECONDS` | `2` | Retry delay ceiling after the first attempt; doubles each attempt. |
| `NOTIF_BACKOFF_CAP_SECONDS` | `300` | Largest retry delay ceiling. The actual delay is random up to the ceiling. |
| `NOTIF_POLL_INTERVAL_SECONDS` | `1` | How long an idle worker sleeps between polls. |
| `NOTIF_BATCH_SIZE` | `1` | Rows a worker claims per poll. |
| `NOTIF_LEASE_SECONDS` | `60` | How long a worker may hold a claimed row before it is marked `unknown`. |
| `NOTIF_PROVIDER_DEADLINE_SECONDS` | `15` | Overall wall-clock limit on one provider call. |

### The lease rule

`NOTIF_BATCH_SIZE × NOTIF_PROVIDER_DEADLINE_SECONDS` must be **less than**
`NOTIF_LEASE_SECONDS`. Rows in a batch are sent one after another but all start
their lease when claimed, so the last row of a batch must still finish inside it.
The API and the worker refuse to start otherwise (the worker exits with status 2
and names `lease_seconds`).

This is why the batch size is 1. A lease that expires while a send is in flight
turns a notification that may well have been delivered into `unknown`.

## Shutdown

On `SIGTERM` or `SIGINT` the worker stops claiming. A send already in flight
finishes, bounded by the provider deadline, and its outcome is recorded. Rows
claimed in the same batch but not yet sent are handed back to `pending` with
their attempt refunded. The process then exits with status 0.

Set the orchestrator's termination grace period above
`NOTIF_PROVIDER_DEADLINE_SECONDS`.

A worker killed outright (`SIGKILL`, a crash, a lost host) leaves its claimed
row `processing`. After the lease expires any worker marks it `unknown`, with
`lease_expired` as its last error. It is never resent.

## Reading the logs

Logs contain notification UUIDs, provider message ids, error category codes and
exception type names. They never contain a recipient, a template option, rendered
content, or provider response text. A line like

```
discarded outcome for notification <uuid> (provider message id <id>)
```

means a worker finished after its lease had expired and the row was already
`unknown`; the provider id lets you check that message with the provider.

## What needs a human

There is no admin view yet. `failed` and `unknown` rows stay in the table.

```sql
SELECT uuid, status, last_error, attempt_count, updated_at
FROM notifications
WHERE status IN ('failed', 'unknown')
ORDER BY updated_at DESC;
```

A rising count of `unknown` with `lease_expired` usually means the lease is too
short for the provider deadline, or workers are being killed mid-send.
