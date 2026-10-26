import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas import NotificationRequest

adapter = TypeAdapter(NotificationRequest)


def parse(channel, recipient, **extra):
    return adapter.validate_python(
        {"channel": channel, "template_slug": "t", "recipient": recipient, **extra}
    )


def rejected_fields(channel, recipient) -> list[str]:
    with pytest.raises(ValidationError) as info:
        parse(channel, recipient)
    return [str(e["loc"][-1]) for e in info.value.errors()]


def test_valid_email_is_accepted_and_domain_lowercased():
    assert parse("email", "Ann@EXAMPLE.com").recipient == "Ann@example.com"


def test_malformed_email_is_rejected():
    assert rejected_fields("email", "not-an-email") == ["recipient"]


def test_phone_number_on_email_channel_is_rejected():
    assert rejected_fields("email", "+14155550123") == ["recipient"]


def test_valid_address_on_a_domain_with_no_mail_records_is_accepted():
    assert parse("email", "ann@no-such-domain-xyzzy.example.com").channel == "email"


def test_formatted_phone_number_is_normalized_to_e164():
    assert parse("sms", "+1 (415) 555-0123").recipient == "+14155550123"


def test_invalid_phone_number_is_rejected():
    assert rejected_fields("sms", "12345") == ["recipient"]


def test_phone_number_without_country_code_is_rejected():
    assert rejected_fields("sms", "(415) 555-0100") == ["recipient"]


def test_unsupported_channel_is_rejected():
    with pytest.raises(ValidationError) as info:
        parse("push", "x")
    assert info.value.errors()[0]["type"] == "union_tag_invalid"


def test_missing_channel_is_rejected():
    with pytest.raises(ValidationError) as info:
        adapter.validate_python({"template_slug": "t", "recipient": "x"})
    assert info.value.errors()[0]["type"] == "union_tag_not_found"


def test_non_string_recipient_is_rejected():
    assert rejected_fields("sms", 14155550123) == ["recipient"]


def test_options_default_to_empty():
    assert parse("sms", "+14155550123").template_options == {}


def test_non_finite_numbers_in_options_are_rejected():
    with pytest.raises(ValidationError):
        parse("sms", "+14155550123", template_options={"x": {"y": [float("inf")]}})
