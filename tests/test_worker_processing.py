import logging

import pytest
from sqlalchemy import text

from app.delivery.outcomes import (
    ErrorCategory,
    OutcomeUnknown,
    PermanentRejection,
    SendResult,
    TransientRejection,
)
from app.delivery.providers.fake import (
    FakeProvider,
    confirm,
    reject_permanently,
    reject_transiently,
    time_out,
    unclassifiable,
)
from tests.helpers import Sql, fetch, insert_notification

PHONE = "+14155550123"


def make_due(db, notification_id):
    db.execute(text("UPDATE notifications SET available_at = now() WHERE id = :i"), {"i": notification_id})
    db.commit()


def test_confirmed_send_becomes_sent(db, make_worker):
    row = insert_notification(db)
    provider = FakeProvider(confirm("SM123"))

    assert make_worker(provider).run_once() == 1

    after = fetch(db, row["id"])
    assert after.status == "sent" and after.provider_message_id == "SM123"
    assert after.sent_at is not None and after.attempt_count == 1


def test_provider_receives_the_uuid_as_idempotency_key_and_rendered_content(db, make_worker):
    row = insert_notification(db)
    provider = FakeProvider()

    make_worker(provider).run_once()

    (call,) = provider.calls
    assert call.idempotency_key == str(row["uuid"])
    assert call.recipient == PHONE and call.channel == "sms"
    assert call.content.text.strip() == "Hi Ann, reminder: your appointment at our clinic is on Mon 9:00."


def test_email_is_rendered_with_all_three_parts(db, make_worker):
    insert_notification(
        db,
        channel="email",
        template_slug="password_reset",
        recipient="ann@example.com",
        template_options={"name": "Ann", "reset_url": "https://example.com/r"},
    )
    provider = FakeProvider()
    make_worker(provider).run_once()
    content = provider.calls[0].content
    assert content.subject == "Reset your password" and content.html and content.text


def test_transient_rejection_goes_back_to_pending_with_backoff(db, make_worker):
    row = insert_notification(db)
    make_worker(FakeProvider(reject_transiently())).run_once()

    after = fetch(db, row["id"])
    assert after.status == "pending" and after.attempt_count == 1
    assert after.last_error == "provider_rate_limited"
    assert after.locked_by is None
    wait = db.execute(text("SELECT extract(epoch FROM available_at - now()) FROM notifications")).scalar_one()
    assert 0 < wait <= 2.0  # attempt 1: ceiling is the 2s base


def test_backed_off_row_is_not_claimed_until_due(db, make_worker):
    insert_notification(db)
    worker = make_worker(FakeProvider(reject_transiently()))
    assert worker.run_once() == 1
    assert worker.run_once() == 0  # still in backoff


def test_retry_reuses_the_same_provider_key(db, make_worker):
    row = insert_notification(db)
    provider = FakeProvider(reject_transiently(), confirm("SM2"))
    worker = make_worker(provider)

    worker.run_once()
    make_due(db, row["id"])
    worker.run_once()

    assert provider.idempotency_keys == [str(row["uuid"])] * 2
    after = fetch(db, row["id"])
    assert after.status == "sent" and after.attempt_count == 2


def test_attempts_exhausted_becomes_failed(db, make_worker):
    row = insert_notification(db, max_attempts=2)
    provider = FakeProvider(default=reject_transiently(ErrorCategory.PROVIDER_UNAVAILABLE))
    worker = make_worker(provider)

    worker.run_once()
    assert fetch(db, row["id"]).status == "pending"
    make_due(db, row["id"])
    worker.run_once()

    after = fetch(db, row["id"])
    assert after.status == "failed" and after.attempt_count == 2
    assert after.last_error == "provider_unavailable"
    make_due(db, row["id"])
    assert worker.run_once() == 0  # terminal: never claimed


def test_permanent_rejection_fails_on_the_first_attempt(db, make_worker):
    row = insert_notification(db)
    provider = FakeProvider(reject_permanently())
    make_worker(provider).run_once()

    after = fetch(db, row["id"])
    assert after.status == "failed" and after.attempt_count == 1
    assert after.last_error == "provider_rejected"
    assert provider.call_count == 1


@pytest.mark.parametrize(
    ("outcome", "category"),
    [
        (time_out(), "provider_timeout"),
        (unclassifiable(), "provider_unclassified"),
        (RuntimeError(f"adapter bug for {PHONE}"), "provider_unclassified"),
    ],
)
def test_ambiguous_outcomes_are_unknown_and_never_sent_again(db, make_worker, outcome, category):
    row = insert_notification(db)
    provider = FakeProvider(outcome)
    worker = make_worker(provider)

    worker.run_once()
    after = fetch(db, row["id"])
    assert after.status == "unknown" and after.last_error == category

    make_due(db, row["id"])
    assert worker.run_once() == 0
    assert provider.call_count == 1


def test_missing_template_fails_without_sending(db, make_worker):
    row = insert_notification(db, template_slug="removed_template")
    provider = FakeProvider()
    make_worker(provider).run_once()

    after = fetch(db, row["id"])
    assert after.status == "failed" and after.last_error == "render_failed"
    assert provider.call_count == 0


def test_options_that_no_longer_fit_the_template_fail_without_sending(db, make_worker):
    row = insert_notification(db, template_options={"patient_name": "Ann"})  # missing a required option
    provider = FakeProvider()
    make_worker(provider).run_once()

    assert fetch(db, row["id"]).last_error == "render_failed"
    assert provider.call_count == 0


def test_no_column_holds_rendered_content(db, make_worker):
    row = insert_notification(db)
    make_worker(FakeProvider()).run_once()

    columns = set(fetch(db, row["id"])._fields)
    assert columns == {
        "id", "uuid", "idempotency_key", "request_hash", "channel", "template_slug",
        "recipient", "template_options", "status", "attempt_count", "max_attempts",
        "available_at", "locked_by", "locked_at", "last_error", "provider_message_id",
        "created_at", "updated_at", "sent_at",
    }  # fmt: skip
    whole_row = db.execute(text("SELECT t::text FROM notifications t")).scalar_one()
    assert "reminder: your appointment" not in whole_row


def test_logs_contain_no_recipient_or_option_values(db, make_worker, caplog):
    """A provider that echoes the recipient, in every way it can fail."""

    def echo_permanent(message):
        raise PermanentRejection(provider_detail=f"{message.recipient} is not a mobile number")

    def echo_transient(message):
        raise TransientRejection(provider_detail=f"busy, retry {message.recipient}")

    def echo_unknown(message):
        raise OutcomeUnknown(provider_detail=f"garbled reply about {message.recipient}")

    def echo_crash(message):
        raise RuntimeError(f"cannot handle {message.recipient} {message.content.text}")

    rows = [insert_notification(db) for _ in range(4)]
    provider = FakeProvider(echo_permanent, echo_transient, echo_unknown, echo_crash)
    worker = make_worker(provider)

    with caplog.at_level(logging.DEBUG):
        for _ in rows:
            worker.run_once()
            db.execute(text("UPDATE notifications SET available_at = now() WHERE status = 'pending'"))
            db.commit()

    assert provider.call_count == 4
    assert PHONE not in caplog.text and "5550123" not in caplog.text
    assert "Ann" not in caplog.text and "Mon 9:00" not in caplog.text
    # What they do contain is useful: identifiers and types.
    assert any(str(r["uuid"]) in caplog.text for r in rows)


def test_discarded_outcome_is_logged_with_uuid_and_provider_message_id(db, make_worker, caplog):
    row = insert_notification(db)

    def reaper_wins_first(message):
        db.execute(text("UPDATE notifications SET status = 'unknown', last_error = 'lease_expired'"))
        db.commit()
        return SendResult("SM-LATE")

    with caplog.at_level(logging.INFO):
        make_worker(FakeProvider(reaper_wins_first)).run_once()

    assert fetch(db, row["id"]).status == "unknown"
    assert str(row["uuid"]) in caplog.text and "SM-LATE" in caplog.text
    assert PHONE not in caplog.text


def test_expired_leases_are_reaped_on_each_pass(db, make_worker):
    stuck = insert_notification(
        db, status="processing", locked_by="dead-worker", locked_at=Sql("now() - interval '5 minutes'")
    )
    provider = FakeProvider()
    make_worker(provider).run_once()

    assert fetch(db, stuck["id"]).status == "unknown"
    assert provider.call_count == 0


def test_oldest_eligible_row_is_sent_first(db, make_worker):
    newer = insert_notification(db, available_at=Sql("now() - interval '1 minute'"))
    older = insert_notification(db, available_at=Sql("now() - interval '1 hour'"))
    provider = FakeProvider()
    worker = make_worker(provider)
    worker.run_once()
    assert fetch(db, older["id"]).status == "sent" and fetch(db, newer["id"]).status == "pending"
