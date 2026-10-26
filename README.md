# Notification service

FastAPI and PostgreSQL service that accepts templated SMS and email notification
requests, stores them in a transactional outbox, and delivers them from
background workers with at-most-once semantics.

See `openspec/changes/add-notification-service/` for the proposal, specs, and design.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL for the service database | `postgresql+psycopg://localhost/notifications` |
| `TEST_DATABASE_URL` | Database used by the test suite | none |

`TEST_DATABASE_URL` must point at a database you can lose. The test session drops
and recreates its `public` schema. Database tests are skipped when it is unset.

```
export TEST_DATABASE_URL='postgresql+psycopg:///notifications?host=/tmp'
uv run pytest
```

A disposable server also works:

```
docker run --rm -d -p 54329:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_DB=notifications_test postgres:18
export TEST_DATABASE_URL='postgresql+psycopg://postgres:test@localhost:54329/notifications_test'
```
