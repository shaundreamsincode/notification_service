# API

Two endpoints. Delivery is asynchronous: `POST /notifications` records the
request and answers `202`; a worker sends it later. Read the outcome back with
`GET /notifications/{uuid}`.

The examples assume a local API and a shell variable `BASE`:

```bash
BASE=http://localhost:8000
```

## Create a notification

`POST /notifications`

Headers:

| Header | Required | Notes |
|---|---|---|
| `Idempotency-Key` | yes | 1 to 255 characters after trimming surrounding whitespace. See [Idempotency](#idempotency). |
| `Content-Type` | yes | `application/json` |

Body:

| Field | Type | Notes |
|---|---|---|
| `channel` | `"email"` or `"sms"` | |
| `template_slug` | string | A template with a variant for this channel must exist. |
| `recipient` | string | Email: a syntactically valid address (no DNS or mailbox lookup). SMS: an international number with country code; stored as E.164. No default region is assumed. |
| `template_options` | object | Must match the template's options exactly: every required option present, no undeclared option, strict types (`"3"` is not an integer). Defaults to `{}`. |

```bash
curl -sS -X POST $BASE/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: reminder-2026-10-27-ann' \
  -d '{"channel":"sms","template_slug":"appointment_reminder","recipient":"+1 (415) 555-0123","template_options":{"patient_name":"Ann","appointment_time":"Mon 9:00"}}' \
  -w '\nHTTP %{http_code}\n'
```

```
{"uuid":"53c29c5f-c611-4cff-83c1-5f7eda2a0f77","status":"pending"}
HTTP 202
```

`uuid` is the only identifier the API exposes. `202` means the request is stored,
not that it was delivered.

Nothing is stored unless the whole request is valid: the template exists for the
channel, the options match its contract, and the template renders with them.

## Idempotency

Every request carries an `Idempotency-Key`. Retrying a request with the same key
never creates a second notification. Keys are global (there is no caller identity
yet), so make them unique, for example by including your own record's id.

Two requests under one key are **equivalent** when `channel`, `template_slug`,
the normalized `recipient`, and `template_options` are equal. Option key order
and whitespace are ignored, numbers compare by value (`1` equals `1.0`), and
`+1 (415) 555-0123` equals `+14155550123`. Template defaults are not applied
before comparing.

- **Equivalent replay**: `202` with the original `uuid` and its *current* status.
  It does not resend, whatever the status is, and it does not depend on the
  template still existing.
- **Different request, same key**: `422` with code `idempotency_key_reused`. The
  stored notification is unchanged and the error does not contain its contents.
- **Rejected requests do not use up the key.** After a `400` or `422` you can fix
  the request and send it again with the same key.

```bash
curl -sS -X POST $BASE/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: reminder-2026-10-27-ann' \
  -d '{"channel":"sms","template_slug":"appointment_reminder","recipient":"+14155550123","template_options":{"appointment_time":"Mon 9:00","patient_name":"Ann"}}' \
  -w '\nHTTP %{http_code}\n'
```

```
{"uuid":"53c29c5f-c611-4cff-83c1-5f7eda2a0f77","status":"pending"}
HTTP 202
```

The same key with another recipient:

```bash
curl -sS -X POST $BASE/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: reminder-2026-10-27-ann' \
  -d '{"channel":"sms","template_slug":"appointment_reminder","recipient":"+14155550199","template_options":{"patient_name":"Ann","appointment_time":"Mon 9:00"}}' \
  -w '\nHTTP %{http_code}\n'
```

```
{"code":"idempotency_key_reused","message":"this Idempotency-Key was already used with a different request","errors":[]}
HTTP 422
```

## Errors

Every error body has the same shape: a `code`, a `message`, and `errors`, a list
of `{field, message}` pairs. Error bodies name the offending field but never
repeat a submitted value, so a rejected recipient or option is not echoed back.

| Status | `code` | Meaning |
|---|---|---|
| 400 | `invalid_idempotency_key` | Key missing, empty, or longer than 255 characters. Checked before the body. |
| 404 | `not_found` | No notification with that UUID. |
| 422 | `validation_error` | Malformed body or UUID, unsupported channel, or invalid recipient. |
| 422 | `template_not_found` | No template for that slug and channel. |
| 422 | `invalid_template_options` | A required option is missing, an option is undeclared, or one has the wrong type. Each is listed in `errors`, e.g. `template_options.appointment_time`. |
| 422 | `render_failed` | The options passed validation but the template failed to render. |
| 422 | `idempotency_key_reused` | The key was used with a different request. |
| 409 | `idempotency_key_conflict` | Two requests raced on a new key and the winner could not be read back. Safe to retry. Not expected in practice. |

```bash
curl -sS -X POST $BASE/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: reminder-2026-10-27-bob' \
  -d '{"channel":"sms","template_slug":"appointment_reminder","recipient":"+14155550123","template_options":{"patient_name":"Bob"}}' \
  -w '\nHTTP %{http_code}\n'
```

```
{"code":"invalid_template_options","message":"template_options do not match the template's options","errors":[{"field":"template_options.appointment_time","message":"field is required"}]}
HTTP 422
```

```bash
curl -sS -X POST $BASE/notifications \
  -H 'Content-Type: application/json' \
  -d '{"channel":"sms","template_slug":"appointment_reminder","recipient":"+14155550123","template_options":{}}' \
  -w '\nHTTP %{http_code}\n'
```

```
{"code":"invalid_idempotency_key","message":"Idempotency-Key header is required","errors":[]}
HTTP 400
```

## Read a notification

`GET /notifications/{uuid}`

Returns the status view. It never contains the recipient, the template options,
or rendered content.

```bash
ID=53c29c5f-c611-4cff-83c1-5f7eda2a0f77   # the uuid from the create response
curl -sS $BASE/notifications/$ID -w '\nHTTP %{http_code}\n'
```

```
{"uuid":"53c29c5f-c611-4cff-83c1-5f7eda2a0f77","channel":"sms","template_slug":"appointment_reminder","status":"pending","attempt_count":0,"last_error":null,"created_at":"2026-10-25T23:10:07.195904-04:00","updated_at":"2026-10-25T23:10:07.195904-04:00","sent_at":null}
HTTP 200
```

| Field | Notes |
|---|---|
| `uuid`, `channel`, `template_slug` | As submitted. |
| `status` | One of the five statuses below. |
| `attempt_count` | Delivery attempts made so far. |
| `last_error` | `null` until an attempt is unsuccessful, then `{code, message}`. Cleared again when the notification is `sent`. See below. |
| `created_at`, `updated_at` | ISO 8601 timestamps. |
| `sent_at` | Set when the provider confirmed the message. |

An unknown UUID is `404`; a malformed one is `422`.

### Statuses

| Status | Meaning | Final? |
|---|---|---|
| `pending` | Waiting for a worker, or waiting to retry after a temporary provider rejection. | no |
| `processing` | A worker has claimed it and is sending. | no |
| `sent` | The provider confirmed it accepted the message. | yes |
| `failed` | Definitely not delivered: the provider permanently rejected it, retries ran out, or it could not be rendered. | yes |
| `unknown` | The service cannot tell whether it was delivered: the provider timed out, the connection dropped, the provider's answer was not understood, or the worker's lease expired. | yes |

`sent`, `failed` and `unknown` never change again.

### `last_error`

`last_error.code` is one of a fixed set, each with a fixed message. It never
contains provider text, the recipient, or an option value.

| `code` | Typical status |
|---|---|
| `provider_rate_limited`, `provider_unavailable` | `pending` (retrying) or `failed` (retries ran out) |
| `provider_rejected`, `render_failed` | `failed` |
| `provider_timeout`, `provider_unclassified`, `lease_expired` | `unknown` |

## Guidance for callers

- **Keep the key you sent** until you have a final status. Retrying with the same
  key is always safe.
- **`failed` is safe to resubmit.** Send it again with a **new** key: replaying the
  old key returns the old `failed` notification.
- **Treat `unknown` as possibly delivered.** The service never resends an
  `unknown` notification, because a duplicate to a recipient is worse than a
  visible gap here. Decide on your side whether to send again with a new key,
  knowing the recipient may receive two.
