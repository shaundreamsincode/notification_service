from pydantic import TypeAdapter

from app.idempotency import normalize_json, request_hash
from app.schemas import NotificationRequest

adapter = TypeAdapter(NotificationRequest)


def hash_of(recipient="+14155550100", channel="sms", slug="t", options=None):
    return request_hash(
        adapter.validate_python(
            {
                "channel": channel,
                "template_slug": slug,
                "recipient": recipient,
                "template_options": options or {},
            }
        )
    )


def test_reordered_option_keys_are_equal():
    assert hash_of(options={"a": 1, "b": {"x": 1, "y": 2}}) == hash_of(
        options={"b": {"y": 2, "x": 1}, "a": 1}
    )


def test_formatted_phone_number_equals_e164():
    assert hash_of("+1 (415) 555-0100") == hash_of("+14155550100")


def test_one_equals_one_point_zero():
    assert hash_of(options={"n": 1}) == hash_of(options={"n": 1.0})
    assert hash_of(options={"n": [1, {"m": 2}]}) == hash_of(options={"n": [1.0, {"m": 2.0}]})


def test_different_numbers_differ():
    assert hash_of(options={"n": 2}) != hash_of(options={"n": 3})
    assert hash_of(options={"n": 1.5}) != hash_of(options={"n": 1})


def test_true_differs_from_one():
    assert hash_of(options={"n": True}) != hash_of(options={"n": 1})
    assert normalize_json({"n": False}) == {"n": False}


def test_each_field_participates():
    base = hash_of()
    assert hash_of(recipient="+14155550199") != base
    assert hash_of(slug="u") != base
    assert hash_of(channel="email", recipient="a@example.com") != hash_of(
        channel="email", recipient="b@example.com"
    )


def test_defaults_are_not_applied():
    # Explicitly passing a value that equals the template default is a different request.
    assert hash_of(options={}) != hash_of(options={"clinic": "our clinic"})


def test_hash_does_not_depend_on_any_template():
    # request_hash takes only the parsed request; this documents the contract.
    import inspect

    assert list(inspect.signature(request_hash).parameters) == ["request"]


def test_string_that_looks_like_a_number_stays_a_string():
    assert hash_of(options={"n": "1"}) != hash_of(options={"n": 1})
