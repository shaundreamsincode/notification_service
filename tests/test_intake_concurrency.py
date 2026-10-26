import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from tests.helpers import post, row_count, sms_body

REPEATS = 20


def race(client, bodies, key):
    """Post each body under one key at (nearly) the same moment."""
    barrier = threading.Barrier(len(bodies))

    def send(body):
        barrier.wait()
        return post(client, body, key=key)

    with ThreadPoolExecutor(len(bodies)) as pool:
        return list(pool.map(send, bodies))


def test_identical_concurrent_requests_create_one_notification(client, db):
    for i in range(REPEATS):
        responses = race(client, [sms_body(), sms_body()], key=f"same-{i}")
        assert [r.status_code for r in responses] == [202, 202]
        assert len({r.json()["uuid"] for r in responses}) == 1
        count = db.execute(
            text("SELECT count(*) FROM notifications WHERE idempotency_key = :k"),
            {"k": f"same-{i}"},
        ).scalar_one()
        db.rollback()
        assert count == 1


def test_many_identical_concurrent_requests_create_one_notification(client, db):
    for i in range(REPEATS):
        responses = race(client, [sms_body()] * 6, key=f"many-{i}")
        assert {r.status_code for r in responses} == {202}
        assert len({r.json()["uuid"] for r in responses}) == 1
    assert row_count(db) == REPEATS


def test_different_concurrent_requests_yield_one_success_and_one_conflict(client, db):
    for i in range(REPEATS):
        responses = race(
            client, [sms_body("+14155550101"), sms_body("+14155550102")], key=f"diff-{i}"
        )
        codes = sorted(r.status_code for r in responses)
        assert codes == [202, 422], codes  # never a 5xx
        loser = next(r for r in responses if r.status_code == 422)
        assert loser.json()["code"] == "idempotency_key_reused"
    assert row_count(db) == REPEATS


def test_vanished_winner_is_a_409_not_a_500(client, db, monkeypatch):
    """The key conflicts on insert but the re-select finds nothing (the winning
    row was removed in between). Must be a controlled error, with or without -O."""
    from app.api import notifications

    post(client, sms_body(), key="abc")
    monkeypatch.setattr(notifications, "_find", lambda session, key: None)

    response = post(client, sms_body(), key="abc")

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_conflict"
    assert row_count(db) == 1


def test_lost_race_is_resolved_by_reselecting(client, db, monkeypatch):
    """Deterministically take the ON CONFLICT path: the first lookup misses even
    though the row exists, as if a concurrent request committed in between."""
    from app.api import notifications

    original = post(client, sms_body(), key="abc").json()
    real_find = notifications._find
    calls = []

    def blind_first_lookup(session, key):
        calls.append(key)
        return None if len(calls) == 1 else real_find(session, key)

    monkeypatch.setattr(notifications, "_find", blind_first_lookup)

    same = post(client, sms_body(), key="abc")
    assert same.status_code == 202 and same.json() == original

    calls.clear()
    other = post(client, sms_body("+14155550199"), key="abc")
    assert other.status_code == 422 and other.json()["code"] == "idempotency_key_reused"
    assert row_count(db) == 1
