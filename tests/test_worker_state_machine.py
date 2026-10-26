import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.delivery import worker as w
from app.settings import Settings
from tests.helpers import Sql, fetch, insert_notification

REPEATS = 20


def claim(engine, worker_id="w1", batch_size=1):
    with engine.begin() as conn:
        return w.claim(conn, worker_id, batch_size)


# --- 7.1 claiming -----------------------------------------------------------------


def test_claim_updates_the_row(schema, db):
    row = insert_notification(db)

    (claimed,) = claim(schema, "worker-a")

    assert claimed.id == row["id"] and claimed.uuid == row["uuid"]
    after = fetch(db, row["id"])
    assert after.status == "processing"
    assert after.attempt_count == 1
    assert after.locked_by == "worker-a"
    assert after.locked_at is not None


def test_future_available_at_is_skipped(schema, db):
    insert_notification(db, available_at=Sql("now() + interval '1 hour'"))
    assert claim(schema) == []


def test_eligible_once_available_at_arrives(schema, db):
    insert_notification(db, available_at=Sql("now() - interval '1 second'"))
    assert len(claim(schema)) == 1


@pytest.mark.parametrize("status", ["processing", "sent", "failed", "unknown"])
def test_only_pending_rows_are_claimed(schema, db, status):
    insert_notification(db, status=status)
    assert claim(schema) == []


def test_oldest_first_with_id_breaking_ties(schema, db):
    mid_1 = insert_notification(db, available_at=Sql("now() - interval '30 minutes'"))
    oldest = insert_notification(db, available_at=Sql("now() - interval '1 hour'"))
    mid_2 = insert_notification(db, available_at=Sql("now() - interval '30 minutes'"))
    # Make the two middle rows' times exactly equal so only the id separates them.
    db.execute(
        text("UPDATE notifications SET available_at = (SELECT available_at FROM notifications WHERE id = :a) WHERE id = :b"),
        {"a": mid_1["id"], "b": mid_2["id"]},
    )  # fmt: skip
    db.commit()

    claimed = claim(schema, batch_size=3)

    assert [c.id for c in claimed] == [oldest["id"], mid_1["id"], mid_2["id"]]


def test_batch_size_limits_the_claim_and_takes_the_oldest(schema, db):
    newer = insert_notification(db, available_at=Sql("now() - interval '1 minute'"))
    older = insert_notification(db, available_at=Sql("now() - interval '1 hour'"))

    (claimed,) = claim(schema, batch_size=1)

    assert claimed.id == older["id"]
    assert fetch(db, newer["id"]).status == "pending"


def test_default_batch_size_claims_one_row(schema, db):
    for _ in range(3):
        insert_notification(db)
    batch_size = Settings().batch_size
    assert batch_size == 1
    assert len(claim(schema, batch_size=batch_size)) == 1


def test_claimed_row_is_not_claimed_again(schema, db):
    insert_notification(db)
    assert len(claim(schema, "a")) == 1
    assert claim(schema, "b") == []


def test_concurrent_sessions_never_claim_the_same_row(schema, db):
    for i in range(REPEATS):
        row = insert_notification(db)
        barrier = threading.Barrier(2)

        def poll(worker_id):
            barrier.wait()
            return claim(schema, worker_id)

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(poll, ["a", "b"]))

        claimed_ids = [c.id for batch in results for c in batch]
        assert claimed_ids == [row["id"]], f"iteration {i}: {claimed_ids}"
        assert fetch(db, row["id"]).attempt_count == 1


def test_concurrent_sessions_split_many_rows_without_overlap(schema, db):
    ids = {insert_notification(db)["id"] for _ in range(30)}
    barrier = threading.Barrier(4)

    def drain(worker_id):
        barrier.wait()
        mine = []
        while batch := claim(schema, worker_id, batch_size=2):
            mine.extend(c.id for c in batch)
        return mine

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(drain, ["a", "b", "c", "d"]))

    flat = [i for r in results for i in r]
    assert sorted(flat) == sorted(ids)  # every row exactly once


# --- 7.2 outcome writes -----------------------------------------------------------

STATUSES = ["pending", "processing", "sent", "failed", "unknown"]


def claimed_for(row, worker_id="w1"):
    return w.Claimed(row["id"], row["uuid"], "sms", "t", "r", {}, 1, 5, None)  # type: ignore[arg-type]


OUTCOMES = {
    "sent": lambda conn, c, wid: w.mark_sent(conn, c, wid, "SM1"),
    "failed": lambda conn, c, wid: w.mark_failed(conn, c, wid, "provider_rejected"),
    "retry": lambda conn, c, wid: w.mark_retry(conn, c, wid, "provider_unavailable", 30),
    "unknown": lambda conn, c, wid: w.mark_unknown(conn, c, wid, "provider_timeout"),
}
EXPECTED_STATUS = {"sent": "sent", "failed": "failed", "retry": "pending", "unknown": "unknown"}


@pytest.mark.parametrize("outcome", OUTCOMES)
@pytest.mark.parametrize("start", STATUSES)
def test_only_processing_rows_under_the_workers_claim_change(schema, db, start, outcome):
    row = insert_notification(
        db, status=start, locked_by="w1", updated_at=Sql("now() - interval '1 hour'")
    )
    before = fetch(db, row["id"])

    with schema.begin() as conn:
        applied = OUTCOMES[outcome](conn, claimed_for(row), "w1")

    after = fetch(db, row["id"])
    if start == "processing":
        assert applied is True
        assert after.status == EXPECTED_STATUS[outcome]
        assert after.updated_at > before.updated_at
    else:
        assert applied is False
        assert tuple(after) == tuple(before)  # nothing changed, including terminal rows


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_another_workers_claim_cannot_be_overwritten(schema, db, outcome):
    row = insert_notification(db, status="processing", locked_by="someone-else")
    before = fetch(db, row["id"])

    with schema.begin() as conn:
        assert OUTCOMES[outcome](conn, claimed_for(row), "w1") is False

    assert tuple(fetch(db, row["id"])) == tuple(before)


def test_sent_records_time_and_provider_message_id(schema, db):
    row = insert_notification(db, status="processing", locked_by="w1")
    with schema.begin() as conn:
        w.mark_sent(conn, claimed_for(row), "w1", "SM123")
    after = fetch(db, row["id"])
    assert after.status == "sent" and after.provider_message_id == "SM123"
    assert after.sent_at is not None


def test_retry_sets_backoff_error_and_releases_the_lock(schema, db):
    row = insert_notification(db, status="processing", locked_by="w1", locked_at=Sql("now()"))
    with schema.begin() as conn:
        w.mark_retry(conn, claimed_for(row), "w1", "provider_rate_limited", 60)
    after = fetch(db, row["id"])
    assert after.status == "pending" and after.last_error == "provider_rate_limited"
    assert after.locked_by is None and after.locked_at is None
    wait = db.execute(
        text("SELECT extract(epoch FROM available_at - now()) FROM notifications")
    ).scalar_one()
    assert 55 < wait <= 60


def test_late_success_after_lease_expiry_but_before_reaping_becomes_sent(schema, db):
    row = insert_notification(
        db, status="processing", locked_by="w1", locked_at=Sql("now() - interval '10 minutes'")
    )
    with schema.begin() as conn:  # lease long expired, reaper has not run
        assert w.mark_sent(conn, claimed_for(row), "w1", "SM1") is True
    assert fetch(db, row["id"]).status == "sent"


def test_reaper_committing_first_wins_and_the_discard_is_logged(schema, db, caplog):
    row = insert_notification(
        db, status="processing", locked_by="w1", locked_at=Sql("now() - interval '10 minutes'")
    )
    with schema.begin() as conn:
        assert w.reap(conn, lease_seconds=60) == 1

    with caplog.at_level(logging.INFO), schema.begin() as conn:
        assert w.mark_sent(conn, claimed_for(row), "w1", "SM-LATE") is False

    after = fetch(db, row["id"])
    assert after.status == "unknown" and after.provider_message_id is None
    assert str(row["uuid"]) in caplog.text and "SM-LATE" in caplog.text
    assert "+14155550123" not in caplog.text


# --- 7.3 reaper -------------------------------------------------------------------


def test_expired_rows_become_unknown_with_lease_expired(schema, db):
    row = insert_notification(
        db,
        status="processing",
        locked_by="w1",
        locked_at=Sql("now() - interval '120 seconds'"),
        updated_at=Sql("now() - interval '1 hour'"),
    )
    before = fetch(db, row["id"])

    with schema.begin() as conn:
        assert w.reap(conn, lease_seconds=60) == 1

    after = fetch(db, row["id"])
    assert after.status == "unknown" and after.last_error == "lease_expired"
    assert after.updated_at > before.updated_at


def test_unexpired_rows_are_untouched(schema, db):
    row = insert_notification(
        db, status="processing", locked_by="w1", locked_at=Sql("now() - interval '30 seconds'")
    )
    before = fetch(db, row["id"])
    with schema.begin() as conn:
        assert w.reap(conn, lease_seconds=60) == 0
    assert tuple(fetch(db, row["id"])) == tuple(before)


@pytest.mark.parametrize("status", ["pending", "sent", "failed", "unknown"])
def test_reaper_ignores_rows_that_are_not_processing(schema, db, status):
    insert_notification(db, status=status, locked_at=Sql("now() - interval '1 hour'"))
    with schema.begin() as conn:
        assert w.reap(conn, lease_seconds=60) == 0


def test_reaped_rows_are_never_claimed_again(schema, db):
    insert_notification(
        db, status="processing", locked_by="w1", locked_at=Sql("now() - interval '5 minutes'")
    )
    with schema.begin() as conn:
        w.reap(conn, lease_seconds=60)
    assert claim(schema) == []


def test_release_refunds_the_attempt_for_a_row_never_sent(schema, db):
    row = insert_notification(db)
    (claimed,) = claim(schema, "w1")
    with schema.begin() as conn:
        assert w.release(conn, claimed, "w1") is True
    after = fetch(db, row["id"])
    assert after.status == "pending" and after.attempt_count == 0 and after.locked_by is None
