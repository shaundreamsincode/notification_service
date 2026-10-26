import pytest
from pydantic import ValidationError

from app.templates.registry import TemplateRegistry
from tests.test_template_loading import write

MANIFEST = """
slug: appointment_reminder
channel: sms
options:
  patient_name: {type: string}
  count: {type: integer}
  ratio: {type: number, required: false}
  urgent: {type: boolean, required: false, default: false}
  clinic: {type: string, required: false, default: our clinic}
  note: {type: string, required: false}
"""


@pytest.fixture
def variant(tmp_path):
    write(tmp_path, "appointment_reminder/sms.yaml", MANIFEST)
    write(tmp_path, "appointment_reminder/sms.txt", "hi {{ patient_name }}")
    found = TemplateRegistry.load(tmp_path).get("appointment_reminder", "sms")
    assert found is not None
    return found


def error_summary(exc: ValidationError) -> list[tuple[str, str]]:
    return [(str(e["loc"][0]), e["type"]) for e in exc.errors()]


def test_required_option_missing(variant):
    with pytest.raises(ValidationError) as info:
        variant.validate_options({"patient_name": "Ann"})
    assert error_summary(info.value) == [("count", "missing")]


def test_every_missing_required_option_is_reported(variant):
    with pytest.raises(ValidationError) as info:
        variant.validate_options({})
    assert {name for name, _ in error_summary(info.value)} == {"patient_name", "count"}


def test_undeclared_option_is_rejected(variant):
    with pytest.raises(ValidationError) as info:
        variant.validate_options({"patient_name": "Ann", "count": 1, "surprise": "x"})
    assert error_summary(info.value) == [("surprise", "extra_forbidden")]


@pytest.mark.parametrize(
    "bad",
    [
        {"patient_name": "Ann", "count": "3"},  # string for integer
        {"patient_name": "Ann", "count": 3.0},  # float for integer
        {"patient_name": "Ann", "count": True},  # bool for integer
        {"patient_name": 5, "count": 1},  # integer for string
        {"patient_name": "Ann", "count": 1, "urgent": "yes"},  # string for boolean
        {"patient_name": "Ann", "count": 1, "urgent": 1},  # integer for boolean
        {"patient_name": "Ann", "count": 1, "ratio": True},  # bool for number
        {"patient_name": "Ann", "count": 1, "note": None},  # null for optional
    ],
)
def test_wrong_types_are_rejected_without_coercion(variant, bad):
    with pytest.raises(ValidationError):
        variant.validate_options(bad)


def test_number_accepts_integers_and_floats_unchanged(variant):
    assert variant.validate_options({"patient_name": "A", "count": 1, "ratio": 2})["ratio"] == 2
    assert variant.validate_options({"patient_name": "A", "count": 1, "ratio": 2.5})["ratio"] == 2.5


def test_manifest_defaults_are_applied(variant):
    context = variant.validate_options({"patient_name": "Ann", "count": 1})
    assert context["clinic"] == "our clinic"
    assert context["urgent"] is False


def test_supplied_value_overrides_default(variant):
    context = variant.validate_options({"patient_name": "A", "count": 1, "clinic": "Elm St"})
    assert context["clinic"] == "Elm St"


def test_omitted_optional_without_default_is_undefined(variant):
    context = variant.validate_options({"patient_name": "Ann", "count": 1})
    assert "note" not in context
    assert "ratio" not in context


def test_non_object_options_are_rejected(variant):
    with pytest.raises(ValidationError):
        variant.validate_options(["not", "an", "object"])


def test_option_named_like_a_model_attribute_works(tmp_path):
    write(
        tmp_path,
        "t/sms.yaml",
        """
        slug: t
        channel: sms
        options:
          copy: {type: string}
          json: {type: string}
        """,
    )
    write(tmp_path, "t/sms.txt", "{{ copy }} {{ json }}")
    variant = TemplateRegistry.load(tmp_path).get("t", "sms")
    assert variant is not None
    assert variant.validate_options({"copy": "a", "json": "b"}) == {"copy": "a", "json": "b"}
