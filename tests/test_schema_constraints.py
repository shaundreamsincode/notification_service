import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

INSERT = text(
    """
    INSERT INTO notifications
      (idempotency_key, request_hash, channel, template_slug, recipient,
       template_options, status, max_attempts)
    VALUES (:key, 'h', :channel, 'password_reset', 'a@example.com',
            '{}'::jsonb, :status, 5)
    """
)


def insert(conn, key="k1", channel="email", status="pending"):
    conn.execute(INSERT, {"key": key, "channel": channel, "status": status})


def test_valid_row_is_accepted(db):
    insert(db)
    db.commit()


@pytest.mark.parametrize("status", ["pending", "processing", "sent", "failed", "unknown"])
def test_every_status_is_allowed(db, status):
    insert(db, status=status)
    db.commit()


def test_invalid_status_is_rejected(db):
    with pytest.raises(IntegrityError, match="ck_notifications_status"):
        insert(db, status="queued")


def test_invalid_channel_is_rejected(db):
    with pytest.raises(IntegrityError, match="ck_notifications_channel"):
        insert(db, channel="push")


def test_duplicate_idempotency_key_is_rejected(db):
    insert(db, key="same")
    with pytest.raises(IntegrityError, match="idempotency_key"):
        insert(db, key="same")


def test_rows_get_different_uuids(db):
    insert(db, key="a")
    insert(db, key="b")
    uuids = db.execute(text("SELECT uuid FROM notifications")).scalars().all()
    assert len(uuids) == 2 and len(set(uuids)) == 2


def test_claim_predicate_uses_the_partial_index(db):
    for i in range(50):
        insert(db, key=f"done{i}", status="sent")
    insert(db, key="waiting", status="pending")
    db.execute(text("ANALYZE notifications"))
    db.execute(text("SET enable_seqscan = off"))
    plan = "\n".join(
        db.execute(
            text(
                "EXPLAIN SELECT id FROM notifications "
                "WHERE status = 'pending' AND available_at <= now() "
                "ORDER BY available_at, id LIMIT 1"
            )
        ).scalars()
    )
    assert "ix_notifications_claim" in plan
