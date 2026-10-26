import textwrap
from pathlib import Path

import pytest

from app.templates.registry import TemplateLoadError, TemplateRegistry

EMAIL_MANIFEST = """
slug: password_reset
channel: email
subject: "Reset for {{ name }}"
options:
  name: {type: string}
"""


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return path


def write_email(root: Path, slug="password_reset", manifest=EMAIL_MANIFEST, html="<p>Hi {{ name }}</p>"):
    write(root, f"{slug}/email.yaml", manifest)
    write(root, f"{slug}/email.html", html)
    write(root, f"{slug}/email.txt", "Hi {{ name }}")


def load_problems(root: Path) -> str:
    with pytest.raises(TemplateLoadError) as info:
        TemplateRegistry.load(root)
    return str(info.value)


def test_valid_set_loads_and_resolves_by_slug_and_channel(tmp_path):
    write_email(tmp_path)
    write(
        tmp_path,
        "password_reset/sms.yaml",
        """
        slug: password_reset
        channel: sms
        options:
          name: {type: string}
        """,
    )
    write(tmp_path, "password_reset/sms.txt", "Hi {{ name }}")

    registry = TemplateRegistry.load(tmp_path)

    email = registry.get("password_reset", "email")
    sms = registry.get("password_reset", "sms")
    assert email is not None and email.channel == "email"
    assert sms is not None and sms.channel == "sms"
    assert email.options["name"].required is True


def test_slug_with_only_an_email_variant_has_no_sms_template(tmp_path):
    write_email(tmp_path)
    assert TemplateRegistry.load(tmp_path).get("password_reset", "sms") is None


def test_malformed_manifest_fails_naming_the_file(tmp_path):
    write(tmp_path, "broken/email.yaml", "slug: [unclosed")
    assert "broken/email.yaml" in load_problems(tmp_path)


def test_missing_body_file_names_the_template_and_file(tmp_path):
    write_email(tmp_path)
    (tmp_path / "password_reset/email.txt").unlink()
    message = load_problems(tmp_path)
    assert "password_reset" in message and "email" in message
    assert "missing body file email.txt" in message


def test_duplicate_slug_and_channel_names_both_files(tmp_path):
    write_email(tmp_path)
    write_email(tmp_path, slug="copy", manifest=EMAIL_MANIFEST)  # also declares password_reset
    message = load_problems(tmp_path)
    assert "password_reset/email.yaml" in message and "copy/email.yaml" in message


def test_unsupported_option_type_names_template_and_option(tmp_path):
    write_email(
        tmp_path,
        manifest="""
        slug: password_reset
        channel: email
        subject: Hello
        options:
          expires: {type: date}
        """,
        html="<p>x</p>",
    )
    message = load_problems(tmp_path)
    assert "password_reset" in message and "'expires'" in message and "'date'" in message


def test_undeclared_variable_in_html_body_names_template_and_variable(tmp_path):
    write_email(tmp_path, html="<p>{{ name }} call {{ clinic_phone }}</p>")
    message = load_problems(tmp_path)
    assert "password_reset" in message and "email.html" in message
    assert "'clinic_phone'" in message


def test_undeclared_variable_in_subject_is_rejected(tmp_path):
    write_email(
        tmp_path,
        manifest="""
        slug: password_reset
        channel: email
        subject: "Hi {{ nickname }}"
        options:
          name: {type: string}
        """,
    )
    assert "subject references undeclared variable 'nickname'" in load_problems(tmp_path)


def test_template_globals_are_not_available(tmp_path):
    write_email(tmp_path, html="{% for i in range(3) %}{{ name }}{% endfor %}")
    assert "'range'" in load_problems(tmp_path)


def test_syntax_error_in_a_body_is_reported(tmp_path):
    write_email(tmp_path, html="{% if name %}unclosed")
    assert "syntax error" in load_problems(tmp_path)


def test_email_manifest_requires_a_subject(tmp_path):
    write_email(
        tmp_path,
        manifest="""
        slug: password_reset
        channel: email
        options: {}
        """,
        html="<p>x</p>",
    )
    assert "subject" in load_problems(tmp_path)


def test_one_bad_template_blocks_the_whole_deploy(tmp_path):
    write_email(tmp_path)
    write(tmp_path, "bad/sms.yaml", "slug: bad\nchannel: sms\noptions: {x: {type: date}}\n")
    write(tmp_path, "bad/sms.txt", "hi")

    message = load_problems(tmp_path)

    assert "bad" in message
    assert "password_reset" not in message  # the valid one is fine, but nothing is served


def test_all_problems_are_reported_together(tmp_path):
    write(tmp_path, "a/email.yaml", "slug: [oops")
    write(tmp_path, "b/email.yaml", "slug: [oops")
    err = None
    try:
        TemplateRegistry.load(tmp_path)
    except TemplateLoadError as exc:
        err = exc
    assert err is not None and len(err.problems) == 2


def test_missing_template_directory_fails(tmp_path):
    assert "does not exist" in load_problems(tmp_path / "nope")


def test_default_on_a_required_option_is_rejected(tmp_path):
    write_email(
        tmp_path,
        manifest="""
        slug: password_reset
        channel: email
        subject: Hi
        options:
          name: {type: string, required: true, default: x}
        """,
        html="<p>x</p>",
    )
    assert "cannot have a default" in load_problems(tmp_path)


def test_default_of_the_wrong_type_is_rejected(tmp_path):
    write_email(
        tmp_path,
        manifest="""
        slug: password_reset
        channel: email
        subject: Hi
        options:
          count: {type: integer, required: false, default: "three"}
        """,
        html="<p>x</p>",
    )
    assert "default for option 'count'" in load_problems(tmp_path)
