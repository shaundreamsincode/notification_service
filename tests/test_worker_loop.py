import os
import signal
import subprocess
import sys
import threading
import time

from sqlalchemy import text

from app.delivery.providers.fake import FakeProvider, confirm
from app.settings import REPO_ROOT
from tests.helpers import fetch, insert_notification


def wait_for(predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def count(db, status):
    db.rollback()
    return db.execute(text("SELECT count(*) FROM notifications WHERE status = :s"), {"s": status}).scalar_one()


def run_in_thread(worker):
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    return thread


def test_loop_drains_pending_rows_to_sent(db, make_worker):
    for _ in range(5):
        insert_notification(db)
    provider = FakeProvider()
    worker = make_worker(provider, poll_interval_seconds=0.05)

    thread = run_in_thread(worker)
    try:
        assert wait_for(lambda: count(db, "sent") == 5)
    finally:
        worker.stop()
        thread.join(5)

    assert not thread.is_alive()
    assert provider.call_count == 5
    assert len(set(provider.idempotency_keys)) == 5  # each notification sent once


def test_loop_picks_up_rows_that_arrive_later(db, make_worker):
    worker = make_worker(FakeProvider(), poll_interval_seconds=0.05)
    thread = run_in_thread(worker)
    try:
        time.sleep(0.2)  # idle for a few polls
        insert_notification(db)
        assert wait_for(lambda: count(db, "sent") == 1)
    finally:
        worker.stop()
        thread.join(5)


def test_shutdown_stops_claiming_and_the_in_flight_send_finishes(db, make_worker):
    first = insert_notification(db)
    second = insert_notification(db)
    in_flight, may_finish = threading.Event(), threading.Event()

    def slow_send(message):
        in_flight.set()
        may_finish.wait(10)
        return confirm("SM-SLOW")

    provider = FakeProvider(slow_send)
    worker = make_worker(
        provider, batch_size=2, provider_deadline_seconds=5, lease_seconds=60, poll_interval_seconds=0.05
    )
    thread = run_in_thread(worker)

    assert in_flight.wait(5)
    worker.stop()  # SIGTERM arrives while the first row is mid-send
    may_finish.set()
    thread.join(5)

    assert not thread.is_alive()
    done = fetch(db, first["id"])
    assert done.status == "sent" and done.provider_message_id == "SM-SLOW"
    # The row claimed in the same batch was never sent: handed back, not lost.
    untouched = fetch(db, second["id"])
    assert untouched.status == "pending" and untouched.attempt_count == 0
    assert provider.call_count == 1


def test_no_claims_after_shutdown(db, make_worker):
    row = insert_notification(db)
    worker = make_worker(FakeProvider())
    worker.stop()
    worker.run()  # returns immediately
    assert fetch(db, row["id"]).status == "pending" and fetch(db, row["id"]).attempt_count == 0


# --- the real process ----------------------------------------------------------------

START_COMMAND = [sys.executable, "-m", "app.delivery.worker"]


def start_worker(database_url, **env):
    return subprocess.Popen(
        START_COMMAND,
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url, "NOTIF_POLL_INTERVAL_SECONDS": "0.1", **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_documented_start_command_runs_delivers_and_exits_cleanly_on_sigterm(db, database_url):
    for _ in range(3):
        insert_notification(db)
    process = start_worker(database_url)
    try:
        assert wait_for(lambda: count(db, "sent") == 3, timeout=20)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_sigint_also_shuts_down_cleanly(db, database_url):
    process = start_worker(database_url)
    try:
        time.sleep(1.0)  # let it start polling
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_process_exits_at_startup_when_the_batch_can_outlast_the_lease(db, database_url):
    process = start_worker(
        database_url,
        NOTIF_BATCH_SIZE="10",
        NOTIF_PROVIDER_DEADLINE_SECONDS="15",
        NOTIF_LEASE_SECONDS="60",
    )
    _, stderr = process.communicate(timeout=20)
    assert process.returncode == 2
    assert "lease_seconds" in stderr


def test_process_exits_when_provider_deadline_is_not_below_the_lease(db, database_url):
    process = start_worker(
        database_url, NOTIF_PROVIDER_DEADLINE_SECONDS="60", NOTIF_LEASE_SECONDS="30"
    )
    _, stderr = process.communicate(timeout=20)
    assert process.returncode == 2 and "lease_seconds" in stderr
