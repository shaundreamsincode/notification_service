"""POST through the API, run the worker against the fake provider, read back via GET."""

from sqlalchemy import text

from app.delivery.outcomes import PermanentRejection
from app.delivery.providers.fake import (
    FakeProvider,
    confirm,
    reject_transiently,
    time_out,
)
from tests.helpers import post, sms_body

PHONE = "+14155550123"


def submit(client, key="e2e-1", **overrides):
    response = post(client, sms_body(PHONE, **overrides), key=key)
    assert response.status_code == 202
    return response.json()["uuid"]


def make_due(db):
    db.execute(text("UPDATE notifications SET available_at = now() WHERE status = 'pending'"))
    db.commit()


def status_of(client, notification_id):
    return client.get(f"/notifications/{notification_id}").json()


def test_confirmed_send_reaches_sent(client, db, make_worker):
    notification_id = submit(client)
    assert status_of(client, notification_id)["status"] == "pending"

    make_worker(FakeProvider(confirm("SM123"))).run_once()

    body = status_of(client, notification_id)
    assert body["status"] == "sent" and body["attempt_count"] == 1
    assert body["sent_at"] is not None and body["last_error"] is None
    assert "SM123" not in str(body)  # the provider's id is not part of the status view


def test_transient_rejection_then_success(client, db, make_worker):
    notification_id = submit(client)
    provider = FakeProvider(reject_transiently(), confirm())
    worker = make_worker(provider)

    worker.run_once()
    retrying = status_of(client, notification_id)
    assert retrying["status"] == "pending" and retrying["attempt_count"] == 1
    assert retrying["last_error"]["code"] == "provider_rate_limited"

    make_due(db)
    worker.run_once()
    body = status_of(client, notification_id)
    assert body["status"] == "sent" and body["attempt_count"] == 2
    assert body["last_error"] is None
    assert provider.idempotency_keys == [notification_id] * 2


def test_permanent_rejection_reaches_failed(client, db, make_worker):
    notification_id = submit(client)
    provider = FakeProvider(PermanentRejection())

    make_worker(provider).run_once()

    body = status_of(client, notification_id)
    assert body["status"] == "failed" and body["attempt_count"] == 1
    assert body["last_error"]["code"] == "provider_rejected"
    assert provider.call_count == 1


def test_timeout_reaches_unknown(client, db, make_worker):
    notification_id = submit(client)
    make_worker(FakeProvider(time_out())).run_once()

    body = status_of(client, notification_id)
    assert body["status"] == "unknown"
    assert body["last_error"]["code"] == "provider_timeout"


def test_replaying_an_unknown_key_does_not_send_again(client, db, make_worker):
    provider = FakeProvider(time_out())
    worker = make_worker(provider)
    notification_id = submit(client, key="the-key")
    worker.run_once()
    assert provider.call_count == 1

    replay = post(client, sms_body(PHONE), key="the-key")
    worker.run_once()
    worker.run_once()

    assert replay.status_code == 202
    assert replay.json() == {"uuid": notification_id, "status": "unknown"}
    assert provider.call_count == 1
    assert status_of(client, notification_id)["status"] == "unknown"


def test_provider_message_echoing_the_phone_number_does_not_reach_the_status_view(
    client, db, make_worker
):
    notification_id = submit(client)

    def echo(message):
        raise PermanentRejection(provider_detail=f"{message.recipient} is not reachable")

    make_worker(FakeProvider(echo)).run_once()

    response = client.get(f"/notifications/{notification_id}")
    assert response.json()["status"] == "failed"
    assert PHONE not in response.text and "5550123" not in response.text
    assert response.json()["last_error"]["code"] == "provider_rejected"


def test_email_flows_end_to_end(client, db, make_worker):
    response = post(
        client,
        {
            "channel": "email",
            "template_slug": "password_reset",
            "recipient": "Ann@Example.com",
            "template_options": {"name": "Ann", "reset_url": "https://example.com/r"},
        },
    )
    provider = FakeProvider()
    make_worker(provider).run_once()

    assert status_of(client, response.json()["uuid"])["status"] == "sent"
    assert provider.calls[0].recipient == "Ann@example.com"
    assert provider.calls[0].content.subject == "Reset your password"
