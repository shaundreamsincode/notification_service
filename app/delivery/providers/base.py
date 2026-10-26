"""The provider interface (design D4, D5).

``send`` returns a ``SendResult`` on confirmed acceptance and otherwise raises
exactly one of ``PermanentRejection``, ``TransientRejection`` or
``OutcomeUnknown``. An adapter that cannot tell which applies raises
``OutcomeUnknown``: it errs towards loss, never towards a duplicate.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.delivery.outcomes import (
    ErrorCategory,
    OutcomeError,
    OutcomeUnknown,
    PermanentRejection,
    SendResult,
    TransientRejection,
)
from app.templates.registry import RenderedMessage

# Shares of the overall deadline given to each httpx phase. They add up to
# less than 1, leaving headroom for the wall-clock guard to be the final word.
_CONNECT_SHARE = 0.2
_WRITE_SHARE = 0.2
_READ_SHARE = 0.4
_POOL_SHARE = 0.1


@dataclass(frozen=True)
class OutgoingMessage:
    channel: str
    recipient: str
    content: RenderedMessage
    # The notification UUID. Passed to the provider in whatever form it accepts
    # so that providers which dedupe collapse repeated sends of one notification.
    idempotency_key: str


class Provider(ABC):
    @abstractmethod
    def send(self, message: OutgoingMessage) -> SendResult: ...


def derive_timeouts(deadline_seconds: float) -> httpx.Timeout:
    """Per-phase httpx timeouts whose sum never exceeds the deadline.

    httpx applies connect, write, read and pool timeouts separately, and the
    read timeout restarts on every chunk, so on their own they bound nothing.
    """
    return httpx.Timeout(
        connect=deadline_seconds * _CONNECT_SHARE,
        write=deadline_seconds * _WRITE_SHARE,
        read=deadline_seconds * _READ_SHARE,
        pool=deadline_seconds * _POOL_SHARE,
    )


class HttpProvider(Provider):
    """Base for adapters that call a provider over HTTP.

    A subclass says how to build the request and how to read an accepted
    response. This class owns the deadline and the mapping from every HTTP
    result to one of the outcome classes (design D4).
    """

    def __init__(
        self, deadline_seconds: float, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.deadline_seconds = deadline_seconds
        self._transport = transport

    @abstractmethod
    def build_request(self, message: OutgoingMessage) -> httpx.Request: ...

    @abstractmethod
    def parse_success(self, response: httpx.Response) -> SendResult:
        """Read a 2xx response. Raise anything if it is not a recognizable acceptance."""

    def send(self, message: OutgoingMessage) -> SendResult:
        try:
            return asyncio.run(self._send(message))
        except OutcomeError:
            raise
        except Exception as exc:
            raise self.classify_exception(exc) from None

    async def _send(self, message: OutgoingMessage) -> SendResult:
        # asyncio.timeout cancels the call at the deadline, however the
        # provider drips its response. It cannot tell whether the request was
        # written, so expiry is an unknown outcome, never a retry.
        try:
            async with asyncio.timeout(self.deadline_seconds):
                async with httpx.AsyncClient(
                    timeout=derive_timeouts(self.deadline_seconds), transport=self._transport
                ) as client:
                    response = await client.send(self.build_request(message))
        except TimeoutError:
            raise OutcomeUnknown(ErrorCategory.PROVIDER_TIMEOUT) from None
        return self.classify_response(response)

    def classify_response(self, response: httpx.Response) -> SendResult:
        status = response.status_code
        if 200 <= status < 300:
            try:
                return self.parse_success(response)
            except Exception:
                raise OutcomeUnknown(ErrorCategory.PROVIDER_UNCLASSIFIED) from None
        if status == 429:
            raise TransientRejection(ErrorCategory.PROVIDER_RATE_LIMITED)
        if status >= 500:
            raise TransientRejection(ErrorCategory.PROVIDER_UNAVAILABLE)
        if 400 <= status < 500:
            raise PermanentRejection(ErrorCategory.PROVIDER_REJECTED)
        # 1xx and 3xx: not an acceptance, not a rejection.
        raise OutcomeUnknown(ErrorCategory.PROVIDER_UNCLASSIFIED)

    def classify_exception(self, exc: Exception) -> OutcomeError:
        # Only failures before any byte could be written are safe to retry: the
        # connection never opened. DNS failure and a refused connection both
        # surface as ConnectError.
        if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError)):
            return TransientRejection(ErrorCategory.PROVIDER_UNAVAILABLE)
        # The request may have been written: read/write timeouts, dropped
        # connections, protocol errors, and anything else we did not foresee.
        if isinstance(exc, httpx.TimeoutException):
            return OutcomeUnknown(ErrorCategory.PROVIDER_TIMEOUT)
        return OutcomeUnknown(ErrorCategory.PROVIDER_UNCLASSIFIED)
