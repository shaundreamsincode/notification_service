import uuid

from sqlalchemy import text

from tests.helpers import post, sms_body

SECRET_NUMBER = "+14155550123"


def create(client, **overrides):
    return post(client, sms_body(SECRET_NUMBER, **overrides)).json()["uuid"]


def test_existing_notification_is_returned(client):
    notification_id = create(client)

    response = client.get(f"/notifications/{notification_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["uuid"] == notification_id
    assert body["channel"] == "sms"
    assert body["template_slug"] == "appointment_reminder"
    assert body["status"] == "pending"
    assert body["attempt_count"] == 0
    assert body["last_error"] is None
    assert body["sent_at"] is None
    assert body["created_at"] and body["updated_at"]


def test_response_has_exactly_the_documented_fields(client):
    body = client.get(f"/notifications/{create(client)}").json()
    assert set(body) == {
        "uuid", "channel", "template_slug", "status", "attempt_count",
        "last_error", "created_at", "updated_at", "sent_at",
    }  # fmt: skip


def test_body_has_neither_recipient_options_nor_internal_id(client, db):
    notification_id = create(client)
    internal_id = db.execute(text("SELECT id FROM notifications")).scalar_one()

    response = client.get(f"/notifications/{notification_id}")

    for secret in (SECRET_NUMBER, "5550123", "Ann", "Mon 9:00", "patient_name"):
        assert secret not in response.text
    assert "id" not in response.json()
    assert internal_id is not None


def test_well_formed_unknown_uuid_is_404(client):
    response = client.get(f"/notifications/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_malformed_uuid_is_422(client):
    response = client.get("/notifications/123")
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "uuid"


def test_last_error_is_a_category_with_a_fixed_message(client, db):
    notification_id = create(client)
    db.execute(
        text("UPDATE notifications SET status = 'unknown', last_error = 'lease_expired'")
    )
    db.commit()

    body = client.get(f"/notifications/{notification_id}").json()

    assert body["status"] == "unknown"
    assert body["last_error"]["code"] == "lease_expired"
    assert "lease expired" in body["last_error"]["message"]


def test_last_error_never_carries_text_outside_the_vocabulary(client, db):
    """Even if a bad value reached the column, it is not echoed to the client."""
    notification_id = create(client)
    db.execute(
        text("UPDATE notifications SET status = 'failed', last_error = :e"),
        {"e": f"provider said bad number {SECRET_NUMBER}"},
    )
    db.commit()

    response = client.get(f"/notifications/{notification_id}")

    assert SECRET_NUMBER not in response.text
    assert response.json()["last_error"] == {
        "code": "unspecified",
        "message": "The notification did not complete.",
    }
