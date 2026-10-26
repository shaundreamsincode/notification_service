"""A scriptable provider for tests and local runs. No network."""

from collections import deque
from collections.abc import Callable

from app.delivery.outcomes import (
    ErrorCategory,
    OutcomeUnknown,
    PermanentRejection,
    SendResult,
    TransientRejection,
)
from app.delivery.providers.base import OutgoingMessage, Provider

# What one call does: return a result, raise an exception, or compute either
# from the message (a callable may raise, e.g. to echo the recipient).
Outcome = SendResult | Exception | Callable[[OutgoingMessage], SendResult]


def confirm(message_id: str = "FAKE-1") -> SendResult:
    return SendResult(message_id)


def reject_transiently(
    category: ErrorCategory = ErrorCategory.PROVIDER_RATE_LIMITED,
) -> TransientRejection:
    return TransientRejection(category)


def reject_permanently() -> PermanentRejection:
    return PermanentRejection()


def time_out() -> OutcomeUnknown:
    return OutcomeUnknown(ErrorCategory.PROVIDER_TIMEOUT)


def unclassifiable() -> OutcomeUnknown:
    return OutcomeUnknown(ErrorCategory.PROVIDER_UNCLASSIFIED)


class FakeProvider(Provider):
    """Plays queued outcomes in order, then ``default`` (confirm by default).

    Records every call so tests can assert on the idempotency key and on how
    many times the provider was reached.
    """

    def __init__(self, *outcomes: Outcome, default: Outcome | None = None):
        self._queue: deque[Outcome] = deque(outcomes)
        self._default = default
        self.calls: list[OutgoingMessage] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def idempotency_keys(self) -> list[str]:
        return [call.idempotency_key for call in self.calls]

    def queue(self, *outcomes: Outcome) -> None:
        self._queue.extend(outcomes)

    def send(self, message: OutgoingMessage) -> SendResult:
        self.calls.append(message)
        outcome = self._queue.popleft() if self._queue else self._default
        if outcome is None:
            outcome = SendResult(f"FAKE-{self.call_count}")
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome(message)
        return outcome
