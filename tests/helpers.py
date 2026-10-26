"""Shared request builders for API tests."""

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, text

SMS_OPTIONS = {"patient_name": "Ann", "appointment_time": "Mon 9:00"}


def sms_body(recipient: str = "+14155550123", **overrides: Any) -> dict[str, Any]:
    body = {
        "channel": "sms",
        "template_slug": "appointment_reminder",
        "recipient": recipient,
        "template_options": dict(SMS_OPTIONS),
    }
    body.update(overrides)
    return body


def email_body(recipient: str = "ann@example.com", **overrides: Any) -> dict[str, Any]:
    body = {
        "channel": "email",
        "template_slug": "password_reset",
        "recipient": recipient,
        "template_options": {"name": "Ann", "reset_url": "https://example.com/r"},
    }
    body.update(overrides)
    return body


def post(client, body: Any, key: str | None = "key-1", **kwargs):
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post("/notifications", json=body, headers=headers, **kwargs)


def row_count(db: Connection) -> int:
    db.rollback()  # end any open snapshot so we see committed rows
    return db.execute(text("SELECT count(*) FROM notifications")).scalar_one()


def dump(response) -> str:
    return json.dumps(response.json())


class Sql(str):
    """A SQL expression spliced in place of a bound value, e.g. ``Sql("now() - interval '1 hour'")``."""


def insert_notification(db: Connection, **overrides: Any) -> dict[str, Any]:
    """Insert a row directly (bypassing the API) and return its id and uuid."""
    values: dict[str, Any] = {
        "idempotency_key": f"k-{uuid4()}",
        "request_hash": "h",
        "channel": "sms",
        "template_slug": "appointment_reminder",
        "recipient": "+14155550123",
        "template_options": json.dumps(SMS_OPTIONS),
        "status": "pending",
        "max_attempts": 5,
    }
    values.update(overrides)
    if not isinstance(values["template_options"], str):
        values["template_options"] = json.dumps(values["template_options"])

    placeholders, params = [], {}
    for column, value in values.items():
        if isinstance(value, Sql):
            placeholders.append(str(value))
        elif column == "template_options":
            placeholders.append("CAST(:template_options AS jsonb)")
            params[column] = value
        else:
            placeholders.append(f":{column}")
            params[column] = value
    row = db.execute(
        text(
            f"INSERT INTO notifications ({', '.join(values)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING id, uuid"
        ),
        params,
    ).one()
    db.commit()
    return {"id": row.id, "uuid": row.uuid}


def fetch(db: Connection, notification_id: int) -> Any:
    db.rollback()  # see rows committed by other connections
    return db.execute(
        text("SELECT * FROM notifications WHERE id = :id"), {"id": notification_id}
    ).one()
