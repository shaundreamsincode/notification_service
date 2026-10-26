"""Delivery outcomes and the fixed ``last_error`` vocabulary (design D4, D7).

``last_error`` holds one of these category codes, never free text. Messages are
constants: no provider text, recipient, or option value can reach a client.
"""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNCLASSIFIED = "provider_unclassified"
    LEASE_EXPIRED = "lease_expired"
    RENDER_FAILED = "render_failed"


ERROR_MESSAGES: dict[ErrorCategory, str] = {
    ErrorCategory.PROVIDER_RATE_LIMITED: "The provider rate-limited the request.",
    ErrorCategory.PROVIDER_UNAVAILABLE: "The provider was unavailable.",
    ErrorCategory.PROVIDER_REJECTED: "The provider rejected the notification.",
    ErrorCategory.PROVIDER_TIMEOUT: (
        "The provider did not respond in time; whether it was delivered is unknown."
    ),
    ErrorCategory.PROVIDER_UNCLASSIFIED: (
        "The provider's response could not be interpreted; whether it was delivered is unknown."
    ),
    ErrorCategory.LEASE_EXPIRED: (
        "The worker did not report an outcome before its lease expired; "
        "whether it was delivered is unknown."
    ),
    ErrorCategory.RENDER_FAILED: "The notification could not be rendered from its template.",
}

_FALLBACK_CODE = "unspecified"
_FALLBACK_MESSAGE = "The notification did not complete."


def public_error(stored: str) -> tuple[str, str]:
    """The (code, message) a client may see for a stored ``last_error``.

    Anything outside the fixed vocabulary is replaced, so a bad value in the
    column can never reach a response.
    """
    try:
        category = ErrorCategory(stored)
    except ValueError:
        return _FALLBACK_CODE, _FALLBACK_MESSAGE
    return category.value, ERROR_MESSAGES[category]


@dataclass(frozen=True)
class SendResult:
    """The provider confirmed it accepted the message."""

    provider_message_id: str


class OutcomeError(Exception):
    """A send that did not end in confirmed acceptance.

    The message is the category's fixed message. ``provider_detail`` exists so
    an adapter can hand over whatever the provider said without deciding what
    is safe: it is discarded here and appears in no attribute, message or repr.
    """

    allowed: tuple[ErrorCategory, ...]

    def __init__(self, category: ErrorCategory | None = None, provider_detail: str | None = None):
        category = category or self.allowed[0]
        if category not in self.allowed:
            raise ValueError(f"{type(self).__name__} cannot carry category {category.value!r}")
        self.category = category
        super().__init__(ERROR_MESSAGES[category])


class PermanentRejection(OutcomeError):
    """The provider definitely refused and will never accept this message."""

    allowed = (ErrorCategory.PROVIDER_REJECTED,)


class TransientRejection(OutcomeError):
    """The provider definitely did not accept the message, and may later."""

    allowed = (ErrorCategory.PROVIDER_UNAVAILABLE, ErrorCategory.PROVIDER_RATE_LIMITED)


class OutcomeUnknown(OutcomeError):
    """The message may or may not have been delivered. Never retried."""

    allowed = (ErrorCategory.PROVIDER_UNCLASSIFIED, ErrorCategory.PROVIDER_TIMEOUT)
