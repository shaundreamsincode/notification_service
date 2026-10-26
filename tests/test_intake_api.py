import uuid

import pytest
from sqlalchemy import text

from tests.helpers import dump, email_body, post, row_count, sms_body

# --- 4.1 / 4.5: accepting requests -------------------------------------------


def test_valid_email_request_is_accepted(client, db):
    response = post(client, email_body())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert str(uuid.UUID(body["uuid"])) == body["uuid"]
    row = db.execute(text("SELECT status, recipient FROM notifications")).one()
    assert row.status == "pending" and row.recipient == "ann@example.com"


def test_valid_sms_request_is_accepted_with_normalized_recipient(client, db):
    response = post(client, sms_body("+1 (415) 555-0123"))
    assert response.status_code == 202
    assert db.execute(text("SELECT recipient FROM notifications")).scalar_one() == "+14155550123"


def test_response_exposes_only_uuid_and_status(client):
    assert set(post(client, sms_body()).json()) == {"uuid", "status"}


def test_unsupported_channel_is_rejected_and_nothing_stored(client, db):
    response = post(client, sms_body(channel="push"))
    assert response.status_code == 422
    assert row_count(db) == 0


def test_invalid_email_names_the_recipient_field(client, db):
    response = post(client, email_body("not-an-email"))
    assert response.status_code == 422
    assert [e["field"] for e in response.json()["errors"]] == ["recipient"]
    assert row_count(db) == 0


def test_phone_number_on_email_channel_is_rejected(client):
    response = post(client, email_body("+14155550123"))
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "recipient"


def test_sms_number_without_country_code_is_rejected(client):
    response = post(client, sms_body("(415) 555-0100"))
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "recipient"


def test_invalid_phone_number_names_the_recipient_field(client, db):
    response = post(client, sms_body("12345"))
    assert response.status_code == 422
    assert [e["field"] for e in response.json()["errors"]] == ["recipient"]
    assert row_count(db) == 0


def test_valid_address_on_a_domain_with_no_mail_records_is_accepted(client):
    response = post(client, email_body("ann@no-such-domain-xyzzy-12345.example.com"))
    assert response.status_code == 202


def test_two_notifications_get_different_uuids(client):
    first = post(client, sms_body(), key="one").json()["uuid"]
    second = post(client, sms_body(), key="two").json()["uuid"]
    assert first != second


def test_invalid_json_body_is_a_422(client):
    response = client.post(
        "/notifications", content=b"{nope", headers={"Idempotency-Key": "k"}
    )
    assert response.status_code == 422


# --- 4.2: errors never echo sensitive values ----------------------------------


def test_invalid_recipient_error_omits_the_value(client):
    secret = "not-an-email-secret@@"
    response = post(client, email_body(secret))
    assert response.status_code == 422
    assert secret not in response.text and "secret" not in response.text
    assert response.json()["errors"][0]["field"] == "recipient"


def test_invalid_phone_error_omits_the_value(client):
    response = post(client, sms_body("+1555SECRET"))
    assert response.status_code == 422
    assert "SECRET" not in response.text and "+1555" not in response.text


def test_mistyped_option_error_omits_the_value(client):
    options = {"name": "Ann", "reset_url": "https://example.com/r", "expires_in_minutes": "9876-private"}
    response = post(client, email_body(template_options=options))
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "template_options.expires_in_minutes"
    assert "9876-private" not in response.text


def test_undeclared_option_error_omits_other_option_values(client):
    options = {"patient_name": "Ann-private", "appointment_time": "t", "surprise": "value-private"}
    response = post(client, sms_body(template_options=options))
    assert response.status_code == 422
    assert "private" not in response.text
    assert [e["field"] for e in response.json()["errors"]] == ["template_options.surprise"]


def test_unsupported_channel_error_omits_the_value_and_recipient(client):
    response = post(client, sms_body("+14155550123", channel="push-private"))
    assert response.status_code == 422
    assert "private" not in response.text and "+14155550123" not in response.text


# --- 4.3: Idempotency-Key header ----------------------------------------------


def test_missing_key_is_rejected_and_nothing_stored(client, db):
    response = post(client, sms_body(), key=None)
    assert response.status_code == 400
    assert row_count(db) == 0


def test_empty_key_is_rejected(client, db):
    response = client.post("/notifications", json=sms_body(), headers={"Idempotency-Key": ""})
    assert response.status_code == 400
    assert row_count(db) == 0


def test_255_character_key_is_accepted(client):
    assert post(client, sms_body(), key="k" * 255).status_code == 202


def test_256_character_key_is_rejected(client, db):
    response = post(client, sms_body(), key="k" * 256)
    assert response.status_code == 400
    assert row_count(db) == 0


def test_key_is_checked_before_the_body(client):
    # The body is invalid too, but the missing key is reported first.
    assert post(client, {"channel": "push"}, key=None).status_code == 400


def test_key_is_checked_before_a_malformed_json_body(client):
    assert client.post("/notifications", content=b"{nope").status_code == 400


# --- 4.5: template validation -------------------------------------------------


def test_unknown_template_is_rejected(client, db):
    response = post(client, sms_body(template_slug="nope"))
    assert response.status_code == 422
    assert response.json()["code"] == "template_not_found"
    assert row_count(db) == 0


def test_template_without_that_channel_is_rejected(client):
    response = post(client, sms_body(template_slug="password_reset"))
    assert response.status_code == 422
    assert "password_reset" in response.json()["message"] and "sms" in response.json()["message"]


def test_missing_required_options_are_each_named(client, db):
    response = post(client, sms_body(template_options={}))
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["errors"]}
    assert fields == {"template_options.patient_name", "template_options.appointment_time"}
    assert row_count(db) == 0


def test_undeclared_option_is_named(client, db):
    options = {"patient_name": "A", "appointment_time": "t", "extra_one": 1, "extra_two": 2}
    response = post(client, sms_body(template_options=options))
    assert {e["field"] for e in response.json()["errors"]} == {
        "template_options.extra_one",
        "template_options.extra_two",
    }
    assert row_count(db) == 0


def test_render_failure_is_rejected_and_nothing_stored(make_client, tmp_path, db):
    from tests.test_template_loading import write

    write(
        tmp_path,
        "needs_guard/sms.yaml",
        """
        slug: needs_guard
        channel: sms
        options:
          nick: {type: string, required: false}
        """,
    )
    write(tmp_path, "needs_guard/sms.txt", "Hi {{ nick }}")  # unguarded optional
    client = make_client(tmp_path)

    response = post(client, sms_body(template_slug="needs_guard", template_options={}))

    assert response.status_code == 422
    assert response.json()["code"] == "render_failed"
    assert row_count(db) == 0


def test_manifest_defaults_apply_at_intake(client):
    assert post(client, sms_body()).status_code == 202  # `clinic` omitted, has a default


# --- replays ------------------------------------------------------------------


def test_replay_returns_the_original_notification(client, db):
    first = post(client, sms_body(), key="abc").json()
    second = post(client, sms_body(), key="abc")
    assert second.status_code == 202
    assert second.json() == first
    assert row_count(db) == 1


@pytest.mark.parametrize("status", ["sent", "failed", "unknown", "processing"])
def test_replay_reflects_current_status_and_changes_nothing(client, db, status):
    original = post(client, sms_body(), key="abc").json()
    db.execute(text("UPDATE notifications SET status = :s"), {"s": status})
    db.commit()

    replay = post(client, sms_body(), key="abc")

    assert replay.status_code == 202
    assert replay.json() == {"uuid": original["uuid"], "status": status}
    assert row_count(db) == 1
    assert db.execute(text("SELECT status, attempt_count FROM notifications")).one() == (status, 0)


def test_replay_after_template_removal_returns_the_original(make_client, tmp_path):
    from tests.test_template_loading import write

    write(tmp_path, "t/sms.yaml", "slug: t\nchannel: sms\noptions: {}\n")
    write(tmp_path, "t/sms.txt", "hello")
    body = sms_body(template_slug="t", template_options={})
    original = post(make_client(tmp_path), body, key="abc").json()

    (tmp_path / "t" / "sms.yaml").unlink()
    (tmp_path / "t" / "sms.txt").unlink()
    write(tmp_path, "other/sms.yaml", "slug: other\nchannel: sms\noptions: {}\n")
    write(tmp_path, "other/sms.txt", "hello")
    after_deploy = make_client(tmp_path)

    assert post(after_deploy, body, key="abc").json() == original
    assert post(after_deploy, body, key="fresh").status_code == 422


def test_different_recipient_under_same_key_is_rejected(client, db):
    post(client, email_body("a@example.com"), key="abc")
    response = post(client, email_body("b@example.com"), key="abc")
    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"
    assert db.execute(text("SELECT recipient FROM notifications")).scalar_one() == "a@example.com"


def test_key_reuse_error_does_not_leak_the_original_payload(client):
    options = {"patient_name": "Original-Private", "appointment_time": "t"}
    post(client, sms_body("+14155550123", template_options=options), key="abc")
    response = post(client, sms_body("+14155550199"), key="abc")
    assert response.json()["code"] == "idempotency_key_reused"
    assert "5550123" not in dump(response) and "Original-Private" not in dump(response)


def test_key_reuse_is_judged_before_the_template(client):
    post(client, sms_body(), key="abc")
    # Different body AND an unknown template: the key conflict wins.
    response = post(client, sms_body(template_slug="nope"), key="abc")
    assert response.json()["code"] == "idempotency_key_reused"


def test_option_key_order_does_not_matter(client, db):
    a = {"patient_name": "Ann", "appointment_time": "t"}
    b = {"appointment_time": "t", "patient_name": "Ann"}
    first = post(client, sms_body(template_options=a), key="abc").json()
    assert post(client, sms_body(template_options=b), key="abc").json() == first


def test_differently_formatted_phone_number_is_equivalent(client):
    first = post(client, sms_body("+14155550100"), key="abc").json()
    assert post(client, sms_body("+1 (415) 555-0100"), key="abc").json() == first


def test_numerically_different_option_is_not_equivalent(client):
    def body(n):
        return email_body(template_options={"name": "A", "reset_url": "u", "expires_in_minutes": n})

    post(client, body(3), key="abc")
    response = post(client, body(2), key="abc")
    assert response.status_code == 422 and response.json()["code"] == "idempotency_key_reused"


def test_same_number_written_differently_is_equivalent(client):
    # A float for an integer option would fail validation, so use a number option via raw JSON
    # on a replay: the hash treats 1 and 1.0 alike, and a replay never revalidates options.
    def body(n):
        return email_body(template_options={"name": "A", "reset_url": "u", "expires_in_minutes": n})

    first = post(client, body(1), key="abc").json()
    replay = client.post(
        "/notifications",
        content=(
            '{"channel":"email","template_slug":"password_reset","recipient":"ann@example.com",'
            '"template_options":{"name":"A","reset_url":"u","expires_in_minutes":1.0}}'
        ),
        headers={"Idempotency-Key": "abc", "Content-Type": "application/json"},
    )
    assert replay.status_code == 202 and replay.json() == first


# --- 4.7: rejected requests do not consume the key ----------------------------


def test_rejected_request_does_not_consume_the_key(client, db):
    rejected = post(client, sms_body(template_options={}), key="abc")
    assert rejected.status_code == 422
    corrected = post(client, sms_body(), key="abc")
    assert corrected.status_code == 202
    assert row_count(db) == 1


def test_no_response_contains_the_internal_id(client, db):
    accepted = post(client, sms_body())
    internal_id = db.execute(text("SELECT id FROM notifications")).scalar_one()
    assert set(accepted.json()) == {"uuid", "status"}
    assert "id" not in accepted.json()
    assert internal_id is not None
