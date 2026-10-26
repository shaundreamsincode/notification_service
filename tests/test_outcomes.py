import pytest

from app.delivery.outcomes import (
    ERROR_MESSAGES,
    ErrorCategory,
    OutcomeUnknown,
    PermanentRejection,
    SendResult,
    TransientRejection,
    public_error,
)

PHONE = "+14155550123"


def test_every_category_has_one_constant_message():
    assert set(ERROR_MESSAGES) == set(ErrorCategory)
    for category in ErrorCategory:
        assert public_error(category.value) == (category.value, ERROR_MESSAGES[category])


@pytest.mark.parametrize(
    ("cls", "category"),
    [
        (PermanentRejection, ErrorCategory.PROVIDER_REJECTED),
        (TransientRejection, ErrorCategory.PROVIDER_UNAVAILABLE),
        (TransientRejection, ErrorCategory.PROVIDER_RATE_LIMITED),
        (OutcomeUnknown, ErrorCategory.PROVIDER_TIMEOUT),
        (OutcomeUnknown, ErrorCategory.PROVIDER_UNCLASSIFIED),
    ],
)
def test_exception_maps_to_its_fixed_category_and_message(cls, category):
    exc = cls(category)
    assert exc.category is category
    assert str(exc) == ERROR_MESSAGES[category]


def test_each_class_has_a_default_category():
    assert PermanentRejection().category is ErrorCategory.PROVIDER_REJECTED
    assert TransientRejection().category is ErrorCategory.PROVIDER_UNAVAILABLE
    assert OutcomeUnknown().category is ErrorCategory.PROVIDER_UNCLASSIFIED


@pytest.mark.parametrize(
    ("cls", "category"),
    [
        (PermanentRejection, ErrorCategory.PROVIDER_TIMEOUT),
        (TransientRejection, ErrorCategory.PROVIDER_REJECTED),
        (OutcomeUnknown, ErrorCategory.LEASE_EXPIRED),
        (OutcomeUnknown, ErrorCategory.RENDER_FAILED),
    ],
)
def test_a_class_cannot_carry_another_classes_category(cls, category):
    with pytest.raises(ValueError):
        cls(category)


@pytest.mark.parametrize("cls", [PermanentRejection, TransientRejection, OutcomeUnknown])
def test_provider_detail_is_never_exposed(cls):
    exc = cls(provider_detail=f"The number {PHONE} is not a mobile number")

    assert PHONE not in str(exc)
    assert PHONE not in repr(exc)
    assert PHONE not in str(exc.args)
    assert PHONE not in repr(vars(exc))
    assert str(exc) == ERROR_MESSAGES[exc.category]


def test_detail_is_not_chained_through_the_cause():
    try:
        try:
            raise ValueError(f"provider said {PHONE}")
        except ValueError as raw:
            raise PermanentRejection(provider_detail=str(raw)) from None
    except PermanentRejection as exc:
        assert exc.__cause__ is None
        assert PHONE not in str(exc)


def test_send_result_carries_the_message_id():
    assert SendResult("SM123").provider_message_id == "SM123"


def test_unknown_stored_error_is_replaced_not_echoed():
    code, message = public_error(f"bad number {PHONE}")
    assert PHONE not in code and PHONE not in message
