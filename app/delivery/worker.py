"""The delivery worker: the notification state machine (design D1-D4, D10).

Claiming, outcome writes and reaping are plain SQL. ``Worker`` adds per-row
processing and the loop. Logs carry identifiers and category codes only,
never a recipient, an option value, or provider text.
"""

import logging
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text

from app.delivery.backoff import backoff_seconds
from app.delivery.outcomes import (
    ErrorCategory,
    OutcomeUnknown,
    PermanentRejection,
    TransientRejection,
)
from app.delivery.providers.base import OutgoingMessage, Provider
from app.settings import Settings
from app.templates.registry import TemplateLoadError, TemplateRegistry, TemplateRenderError

logger = logging.getLogger("notifications.worker")


@dataclass(frozen=True)
class Claimed:
    id: int
    uuid: UUID
    channel: str
    template_slug: str
    recipient: str
    template_options: dict[str, Any]
    attempt_count: int
    max_attempts: int
    available_at: datetime


_CLAIM = text(
    """
    UPDATE notifications
    SET status = 'processing',
        attempt_count = attempt_count + 1,
        locked_by = :worker_id,
        locked_at = now(),
        updated_at = now()
    WHERE id IN (
      SELECT id FROM notifications
      WHERE status = 'pending' AND available_at <= now()
      ORDER BY available_at, id
      LIMIT :batch_size
      FOR UPDATE SKIP LOCKED
    )
    RETURNING id, uuid, channel, template_slug, recipient, template_options,
              attempt_count, max_attempts, available_at
    """
)


def claim(conn: Connection, worker_id: str, batch_size: int) -> list[Claimed]:
    """Atomically move up to ``batch_size`` eligible rows to ``processing``.

    The lock is released when the caller's transaction commits, before any
    provider call: the lease, not a held lock, is what protects the row.
    """
    rows = conn.execute(_CLAIM, {"worker_id": worker_id, "batch_size": batch_size}).all()
    # RETURNING order is not guaranteed.
    return sorted((Claimed(*row) for row in rows), key=lambda c: (c.available_at, c.id))


# Every outcome write is one conditional UPDATE: the row must still be
# `processing` under this worker's claim. Zero rows updated means the lease
# expired and the reaper (or another transition) got there first.
_FINISH = """
    UPDATE notifications
    SET {assignments}, updated_at = now()
    WHERE id = :id AND status = 'processing' AND locked_by = :worker_id
"""


def _finish(
    conn: Connection,
    claimed: Claimed,
    worker_id: str,
    assignments: str,
    params: dict[str, Any],
    provider_message_id: str | None = None,
) -> bool:
    result = conn.execute(
        text(_FINISH.format(assignments=assignments)),
        {"id": claimed.id, "worker_id": worker_id, **params},
    )
    if result.rowcount == 1:
        return True
    # Only identifiers: no recipient, options, or error text.
    logger.warning(
        "discarded outcome for notification %s (provider message id %s)",
        claimed.uuid,
        provider_message_id or "none",
    )
    return False


def mark_sent(
    conn: Connection, claimed: Claimed, worker_id: str, provider_message_id: str
) -> bool:
    return _finish(
        conn,
        claimed,
        worker_id,
        "status = 'sent', sent_at = now(), provider_message_id = :pmid",
        {"pmid": provider_message_id},
        provider_message_id,
    )


def mark_failed(conn: Connection, claimed: Claimed, worker_id: str, last_error: str) -> bool:
    return _finish(
        conn, claimed, worker_id, "status = 'failed', last_error = :err", {"err": last_error}
    )


def mark_unknown(conn: Connection, claimed: Claimed, worker_id: str, last_error: str) -> bool:
    return _finish(
        conn, claimed, worker_id, "status = 'unknown', last_error = :err", {"err": last_error}
    )


def mark_retry(
    conn: Connection, claimed: Claimed, worker_id: str, last_error: str, delay_seconds: float
) -> bool:
    return _finish(
        conn,
        claimed,
        worker_id,
        "status = 'pending', last_error = :err, locked_by = NULL, locked_at = NULL, "
        "available_at = now() + make_interval(secs => :delay)",
        {"err": last_error, "delay": delay_seconds},
    )


def release(conn: Connection, claimed: Claimed, worker_id: str) -> bool:
    """Give back a claimed row that was never sent (shutdown mid-batch).

    Safe because nothing reached the provider; the attempt it was charged for
    is refunded.
    """
    return _finish(
        conn,
        claimed,
        worker_id,
        "status = 'pending', attempt_count = attempt_count - 1, "
        "locked_by = NULL, locked_at = NULL",
        {},
    )


_REAP = text(
    """
    UPDATE notifications
    SET status = 'unknown', last_error = 'lease_expired', updated_at = now()
    WHERE status = 'processing' AND locked_at < now() - make_interval(secs => :lease)
    """
)


def reap(conn: Connection, lease_seconds: float) -> int:
    """Mark rows whose lease expired as ``unknown``. Uses database time, so
    worker clock skew is irrelevant. Idempotent; any worker may run it."""
    return conn.execute(_REAP, {"lease": lease_seconds}).rowcount


class Worker:
    def __init__(
        self,
        engine: Engine,
        registry: TemplateRegistry,
        providers: dict[str, Provider],
        settings: Settings,
        worker_id: str | None = None,
    ):
        missing = {"email", "sms"} - set(providers)
        if missing:
            raise ValueError(f"no provider configured for channel(s): {sorted(missing)}")
        self.engine = engine
        self.registry = registry
        self.providers = providers
        self.settings = settings
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self._stopping = threading.Event()

    def stop(self) -> None:
        """Stop claiming. A send already in flight finishes (bounded by the
        provider deadline) and its outcome is recorded."""
        self._stopping.set()

    # --- one pass -------------------------------------------------------------

    def run_once(self) -> int:
        """Reap, claim a batch, process it sequentially. Returns rows claimed."""
        with self.engine.begin() as conn:
            reaped = reap(conn, self.settings.lease_seconds)
            claimed = claim(conn, self.worker_id, self.settings.batch_size)
        if reaped:
            logger.warning("marked %d notification(s) unknown: lease expired", reaped)

        for index, notification in enumerate(claimed):
            if self._stopping.is_set():
                # Never sent, so safe to hand back rather than let the lease
                # turn them into `unknown`.
                with self.engine.begin() as conn:
                    for unsent in claimed[index:]:
                        release(conn, unsent, self.worker_id)
                break
            self.process(notification)
        return len(claimed)

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                claimed = self.run_once()
            except Exception as exc:
                # Typically the database. A row mid-send will expire into
                # `unknown`, which is the intended at-most-once outcome.
                logger.error("worker pass failed: %s", type(exc).__name__)
                claimed = 0
            if claimed == 0:
                self._stopping.wait(self.settings.poll_interval_seconds)

    # --- one notification -------------------------------------------------------

    def process(self, claimed: Claimed) -> None:
        content = self._render(claimed)
        if content is None:
            self._record(mark_failed, claimed, ErrorCategory.RENDER_FAILED.value)
            return

        message = OutgoingMessage(
            claimed.channel, claimed.recipient, content, idempotency_key=str(claimed.uuid)
        )
        try:
            result = self.providers[claimed.channel].send(message)
        except PermanentRejection as exc:
            self._record(mark_failed, claimed, exc.category.value)
        except TransientRejection as exc:
            if claimed.attempt_count >= claimed.max_attempts:
                self._record(mark_failed, claimed, exc.category.value)
            else:
                delay = backoff_seconds(
                    claimed.attempt_count,
                    self.settings.backoff_base_seconds,
                    self.settings.backoff_cap_seconds,
                )
                with self.engine.begin() as conn:
                    mark_retry(conn, claimed, self.worker_id, exc.category.value, delay)
        except OutcomeUnknown as exc:
            self._record(mark_unknown, claimed, exc.category.value)
        except Exception as exc:
            # An adapter bug or an unforeseen failure: we cannot know what
            # happened, so never retry. Log the type only; the message may
            # quote the provider.
            logger.error(
                "unexpected provider error for notification %s: %s",
                claimed.uuid,
                type(exc).__name__,
            )
            self._record(mark_unknown, claimed, ErrorCategory.PROVIDER_UNCLASSIFIED.value)
        else:
            with self.engine.begin() as conn:
                mark_sent(conn, claimed, self.worker_id, result.provider_message_id)

    def _render(self, claimed: Claimed) -> Any:
        """Render from the stored slug, channel and options; nothing is persisted.
        None means it cannot be rendered (template gone or options no longer fit)."""
        variant = self.registry.get(claimed.template_slug, claimed.channel)
        if variant is None:
            logger.error("no template for notification %s", claimed.uuid)
            return None
        try:
            return variant.render(variant.validate_options(claimed.template_options))
        except (ValidationError, TemplateRenderError):
            logger.error("render failed for notification %s", claimed.uuid)
            return None

    def _record(self, write: Any, claimed: Claimed, last_error: str) -> None:
        with self.engine.begin() as conn:
            write(conn, claimed, self.worker_id, last_error)


def main() -> int:
    from app.db import make_engine
    from app.delivery.providers.fake import FakeProvider
    from app.settings import ConfigurationError, get_settings

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(f"worker configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        registry = TemplateRegistry.load(settings.template_dir)
    except TemplateLoadError as exc:
        print(f"worker refused to start: {exc}", file=sys.stderr)
        return 1

    # Real provider adapters are a later change; the fake confirms everything
    # without sending anything.
    logger.warning("using the fake provider: no notification is actually delivered")
    provider = FakeProvider()
    engine = make_engine(settings.database_url)
    worker = Worker(engine, registry, {"email": provider, "sms": provider}, settings)

    def shutdown(signum: int, _frame: Any) -> None:
        logger.info("received signal %d: finishing the in-flight send, then exiting", signum)
        worker.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logger.info("worker %s started", worker.worker_id)
    worker.run()
    engine.dispose()
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
