"""Request and response models for the notifications API."""

import math
from typing import Annotated, Any, Literal
from uuid import UUID

import phonenumbers
from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, Field, field_validator


def _check_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("template_options must not contain NaN or infinity")
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)


class _NotificationRequest(BaseModel):
    template_slug: str = Field(min_length=1)
    template_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template_options")
    @classmethod
    def _finite_numbers_only(cls, value: dict[str, Any]) -> dict[str, Any]:
        _check_finite(value)
        return value


# The validators below raise fixed messages: library messages can quote parts
# of the submitted value, and error bodies must never echo the recipient.


class EmailRequest(_NotificationRequest):
    channel: Literal["email"]
    recipient: str

    @field_validator("recipient")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        try:
            # Syntax only: no DNS or mailbox lookups.
            return validate_email(value, check_deliverability=False).normalized
        except EmailNotValidError:
            raise ValueError("not a valid email address") from None


class SmsRequest(_NotificationRequest):
    channel: Literal["sms"]
    recipient: str

    @field_validator("recipient")
    @classmethod
    def _normalize_phone(cls, value: str) -> str:
        try:
            # No default region: a number must carry its own country code.
            number = phonenumbers.parse(value, None)
        except phonenumbers.NumberParseException:
            raise ValueError("not a valid international phone number") from None
        if not phonenumbers.is_valid_number(number):
            raise ValueError("not a valid international phone number")
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


NotificationRequest = Annotated[EmailRequest | SmsRequest, Field(discriminator="channel")]


class NotificationAccepted(BaseModel):
    uuid: UUID
    status: str
