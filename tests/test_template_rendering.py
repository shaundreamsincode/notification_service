import pytest

from app.templates.registry import TemplateRegistry, TemplateRenderError, TemplateVariant
from tests.test_template_loading import write


def email_variant(tmp_path, subject="Hello {{ name }}", html="<p>{{ name }}</p>", text="{{ name }}",
                  options="name: {type: string}") -> TemplateVariant:
    write(
        tmp_path,
        "t/email.yaml",
        f"""
        slug: t
        channel: email
        subject: {subject!r}
        options:
          {options}
        """,
    )
    write(tmp_path, "t/email.html", html)
    write(tmp_path, "t/email.txt", text)
    variant = TemplateRegistry.load(tmp_path).get("t", "email")
    assert variant is not None
    return variant


def sms_variant(tmp_path, text="{{ name }}", options="name: {type: string}") -> TemplateVariant:
    write(
        tmp_path,
        "s/sms.yaml",
        f"""
        slug: s
        channel: sms
        options:
          {options}
        """,
    )
    write(tmp_path, "s/sms.txt", text)
    variant = TemplateRegistry.load(tmp_path).get("s", "sms")
    assert variant is not None
    return variant


def test_email_renders_subject_html_and_text(tmp_path):
    message = email_variant(tmp_path).render({"name": "Ann"})
    assert message.subject == "Hello Ann"
    assert message.html == "<p>Ann</p>"
    assert message.text == "Ann"


def test_sms_renders_one_text_body(tmp_path):
    message = sms_variant(tmp_path).render({"name": "Ann"})
    assert message.text == "Ann" and message.html is None and message.subject is None


def test_html_injection_is_escaped_but_plain_text_is_not(tmp_path):
    payload = "<script>alert(1)</script>"
    message = email_variant(tmp_path).render({"name": payload})
    assert "<script>" not in (message.html or "")
    assert "&lt;script&gt;" in (message.html or "")
    assert message.text == payload
    assert message.subject == f"Hello {payload}"


def test_sms_values_are_not_html_escaped(tmp_path):
    assert sms_variant(tmp_path).render({"name": "a & <b>"}).text == "a & <b>"


def test_missing_value_fails_naming_the_option(tmp_path):
    with pytest.raises(TemplateRenderError, match="'name' is undefined"):
        email_variant(tmp_path).render({})


def test_guarded_omitted_optional_renders(tmp_path):
    variant = sms_variant(
        tmp_path,
        text="Hi{% if nick is defined %} {{ nick }}{% endif %}",
        options="nick: {type: string, required: false}",
    )
    assert variant.render({}).text == "Hi"
    assert variant.render({"nick": "Al"}).text == "Hi Al"


def test_unguarded_omitted_optional_fails(tmp_path):
    variant = sms_variant(tmp_path, text="Hi {{ nick }}", options="nick: {type: string, required: false}")
    with pytest.raises(TemplateRenderError, match="'nick' is undefined"):
        variant.render({})


def test_newline_in_subject_fails(tmp_path):
    with pytest.raises(TemplateRenderError, match="line break"):
        email_variant(tmp_path).render({"name": "Ann\nBcc: x@evil.test"})


def test_carriage_return_in_subject_fails(tmp_path):
    with pytest.raises(TemplateRenderError, match="line break"):
        email_variant(tmp_path).render({"name": "Ann\rBcc"})


def test_whitespace_only_subject_fails(tmp_path):
    variant = email_variant(tmp_path, subject="{{ name }}")
    with pytest.raises(TemplateRenderError, match="subject: rendered empty"):
        variant.render({"name": "   "})


def test_whitespace_only_sms_body_fails(tmp_path):
    with pytest.raises(TemplateRenderError, match="text body: rendered empty"):
        sms_variant(tmp_path).render({"name": "  \n "})


def test_whitespace_only_html_body_fails(tmp_path):
    variant = email_variant(tmp_path, html="{{ name }}")
    with pytest.raises(TemplateRenderError, match="html body: rendered empty"):
        variant.render({"name": " "})


def test_two_renders_are_byte_identical(tmp_path):
    variant = email_variant(tmp_path)
    first, second = variant.render({"name": "Ann"}), variant.render({"name": "Ann"})
    assert first == second
    assert first.html is not None and first.html.encode() == (second.html or "").encode()


def test_render_errors_never_repeat_option_values(tmp_path):
    variant = sms_variant(
        tmp_path, text="{{ n + 1 }}", options="n: {type: string}"
    )
    with pytest.raises(TemplateRenderError) as info:
        variant.render({"n": "+14155550123"})
    assert "+14155550123" not in str(info.value)
