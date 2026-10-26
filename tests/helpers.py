"""Shared request builders for API tests."""

import json
from typing import Any

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
