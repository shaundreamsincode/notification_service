import pytest

from app.delivery.outcomes import (
    ErrorCategory,
    OutcomeUnknown,
    PermanentRejection,
    SendResult,
    TransientRejection,
)
from app.delivery.providers.base import OutgoingMessage
from app.delivery.providers.fake import (
    FakeProvider,
    confirm,
    reject_permanently,
    reject_transiently,
    time_out,
    unclassifiable,
)
from app.templates.registry import RenderedMessage


def message(key="U-1", recipient="+14155550123") -> OutgoingMessage:
    return OutgoingMessage("sms", recipient, RenderedMessage(None, None, "hi"), key)


def test_confirms_with_a_message_id():
    assert FakeProvider(confirm("SM123")).send(message()) == SendResult("SM123")


def test_confirms_by_default_with_distinct_ids():
    provider = FakeProvider()
    first, second = provider.send(message()), provider.send(message())
    assert first.provider_message_id != second.provider_message_id


def test_transient_rejection():
    with pytest.raises(TransientRejection) as info:
        FakeProvider(reject_transiently()).send(message())
    assert info.value.category is ErrorCategory.PROVIDER_RATE_LIMITED


def test_permanent_rejection():
    with pytest.raises(PermanentRejection):
        FakeProvider(reject_permanently()).send(message())


def test_timeout_is_unknown():
    with pytest.raises(OutcomeUnknown) as info:
        FakeProvider(time_out()).send(message())
    assert info.value.category is ErrorCategory.PROVIDER_TIMEOUT


def test_unclassifiable_response_is_unknown():
    with pytest.raises(OutcomeUnknown) as info:
        FakeProvider(unclassifiable()).send(message())
    assert info.value.category is ErrorCategory.PROVIDER_UNCLASSIFIED


def test_outcomes_play_in_order_then_default():
    provider = FakeProvider(reject_transiently(), confirm("SM9"))
    with pytest.raises(TransientRejection):
        provider.send(message())
    assert provider.send(message()).provider_message_id == "SM9"
    assert provider.send(message()).provider_message_id.startswith("FAKE-")


def test_explicit_default_applies_once_the_queue_is_empty():
    provider = FakeProvider(default=time_out())
    for _ in range(2):
        with pytest.raises(OutcomeUnknown):
            provider.send(message())


def test_records_idempotency_keys_and_call_count():
    provider = FakeProvider(reject_transiently())
    with pytest.raises(TransientRejection):
        provider.send(message(key="U-1"))
    provider.send(message(key="U-1"))
    assert provider.call_count == 2
    assert provider.idempotency_keys == ["U-1", "U-1"]


def test_a_callable_outcome_can_use_the_message():
    def echo(sent: OutgoingMessage):
        raise PermanentRejection(provider_detail=f"cannot reach {sent.recipient}")

    provider = FakeProvider(echo)
    with pytest.raises(PermanentRejection) as info:
        provider.send(message(recipient="+14155550123"))
    assert "5550123" not in str(info.value)


def test_queue_adds_outcomes_later():
    provider = FakeProvider()
    provider.queue(reject_permanently())
    with pytest.raises(PermanentRejection):
        provider.send(message())
