import re

import pytest

from app.settings import REPO_ROOT
from app.templates.registry import TemplateRegistry

TEMPLATE_DIR = REPO_ROOT / "templates"


@pytest.fixture(scope="module")
def registry() -> TemplateRegistry:
    # The repository's real templates: an invalid committed template fails CI here.
    return TemplateRegistry.load(TEMPLATE_DIR)


def test_samples_are_loaded(registry):
    assert registry.get("appointment_reminder", "email") is not None
    assert registry.get("appointment_reminder", "sms") is not None
    assert registry.get("password_reset", "email") is not None
    assert registry.get("password_reset", "sms") is None


def render(registry, slug, channel, options):
    variant = registry.get(slug, channel)
    assert variant is not None
    return variant.render(variant.validate_options(options))


def test_appointment_reminder_email(registry):
    message = render(
        registry,
        "appointment_reminder",
        "email",
        {"patient_name": "Ann", "appointment_time": "Mon 9:00"},
    )
    assert message.subject == "Appointment reminder for Mon 9:00"
    assert "our clinic" in message.text and "our clinic" in (message.html or "")


def test_appointment_reminder_email_with_instructions(registry):
    message = render(
        registry,
        "appointment_reminder",
        "email",
        {"patient_name": "Ann", "appointment_time": "Mon", "instructions": "Fast first"},
    )
    assert "Fast first" in message.text and "Fast first" in (message.html or "")


def test_appointment_reminder_sms(registry):
    message = render(
        registry,
        "appointment_reminder",
        "sms",
        {"patient_name": "Ann", "appointment_time": "Mon 9:00", "clinic": "Elm St"},
    )
    assert message.text.strip() == "Hi Ann, reminder: your appointment at Elm St is on Mon 9:00."


def test_password_reset_email(registry):
    message = render(
        registry,
        "password_reset",
        "email",
        {"name": "Ann", "reset_url": "https://example.com/r?t=1&x=2"},
    )
    assert message.subject == "Reset your password"
    assert "expires in 60 minutes" in message.text
    assert "t=1&amp;x=2" in (message.html or "")


def test_documented_manifest_example_is_a_real_template(registry):
    readme = (TEMPLATE_DIR / "README.md").read_text()
    example = re.search(r"## Manifest\n\n```yaml\n(.*?)```", readme, re.S)
    assert example is not None
    actual = (TEMPLATE_DIR / "appointment_reminder" / "email.yaml").read_text()
    assert example.group(1).strip() == actual.strip()
    assert registry.get("appointment_reminder", "email") is not None
