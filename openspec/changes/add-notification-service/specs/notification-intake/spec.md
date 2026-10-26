# Spec Delta

## Purpose

Accepts notification requests over HTTP, validates them against the channel and template contract, and records them durably exactly once per idempotency key.

## ADDED Requirements

### Requirement: Accept notification requests
The system SHALL expose `POST /notifications` accepting a JSON body with `channel`, `template_slug`, `recipient`, and `template_options`. A valid request SHALL be stored with status `pending` and answered with `202 Accepted` and a body holding the notification `uuid` and `status`.

#### Scenario: Valid email request is accepted
- **WHEN** a client posts channel `email`, slug `password_reset`, a valid email recipient, all required options, and an `Idempotency-Key` header
- **THEN** the system responds `202 Accepted` with a new notification UUID and status `pending`
- **AND** a notification with status `pending` exists for that UUID

#### Scenario: Unsupported channel is rejected
- **WHEN** a client posts a channel other than `email` or `sms`
- **THEN** the system responds `422` and stores nothing

### Requirement: Notifications are identified publicly by UUID
Each notification SHALL be assigned a random UUID when it is stored. The API SHALL identify notifications only by this UUID and SHALL NOT expose any internal identifier in requests or responses.

#### Scenario: Response carries a UUID
- **WHEN** a client creates a notification
- **THEN** the response's `uuid` is a valid UUID
- **AND** the response contains no other notification identifier

#### Scenario: UUIDs are unique
- **WHEN** two notifications are created with different idempotency keys
- **THEN** they have different UUIDs

### Requirement: Require an idempotency key
Every `POST /notifications` request SHALL carry an `Idempotency-Key` header holding a non-empty string of at most 255 characters. Requests without a valid key SHALL be rejected before any validation that depends on the body.

#### Scenario: Missing key is rejected
- **WHEN** a client posts an otherwise valid request without an `Idempotency-Key` header
- **THEN** the system responds `400` and stores nothing

#### Scenario: Overlong key is rejected
- **WHEN** a client posts a request whose `Idempotency-Key` is longer than 255 characters
- **THEN** the system responds `400` and stores nothing

### Requirement: Replay requests with a reused idempotency key
A request whose `Idempotency-Key` matches a stored notification and whose body is equivalent to the original SHALL NOT create a new notification. The system SHALL respond `202 Accepted` with the existing notification's UUID and current status. The key lookup SHALL precede template validation, so a replay never fails because the template has since changed.

#### Scenario: Retried request returns the original notification
- **WHEN** a client posts the same body twice with the same `Idempotency-Key`
- **THEN** both responses carry the same notification UUID
- **AND** exactly one notification exists for that key

#### Scenario: Replay reflects current status
- **WHEN** a client replays a request whose notification has since been sent
- **THEN** the response carries status `sent`

#### Scenario: Replay of a terminal failure does not resubmit
- **WHEN** a client replays the key of a notification whose status is `failed` or `unknown`
- **THEN** the original notification is returned unchanged
- **AND** nothing is resent

#### Scenario: Replay succeeds after template removal
- **WHEN** a client replays an equivalent request after the referenced template has been removed from the deployment
- **THEN** the original notification is returned rather than a template validation error

### Requirement: Request equivalence is judged after normalization
Two requests under the same key SHALL be equivalent when `channel`, `template_slug`, the normalized `recipient`, and `template_options` are equal. Options SHALL compare as JSON values: key order and whitespace are ignored, numbers compare by numeric value, and template defaults are not applied. Comparison SHALL NOT depend on the currently deployed template.

#### Scenario: Key order in options does not matter
- **WHEN** a replay sends the same options with object keys in a different order
- **THEN** it is equivalent and returns the original notification

#### Scenario: Differently formatted phone number
- **WHEN** a replay sends `+1 (415) 555-0100` where the original sent `+14155550100`
- **THEN** it is equivalent because both normalize to the same E.164 number

#### Scenario: Numerically different option value
- **WHEN** a replay sends option `count` as `2` where the original sent `3`
- **THEN** it is not equivalent and the response is `422` with code `idempotency_key_reused`

#### Scenario: Same number written differently
- **WHEN** a replay sends option `count` as `1.0` where the original sent `1`
- **THEN** it is equivalent and returns the original notification

### Requirement: Reject reused idempotency keys with a different payload
A request whose `Idempotency-Key` matches a stored notification but whose body is not equivalent to the original SHALL be rejected with `422` and error code `idempotency_key_reused`, distinct from ordinary validation errors. It SHALL NOT change the stored notification, and the error SHALL NOT include the original recipient or options.

#### Scenario: Same key with a different recipient
- **WHEN** a client posts with key `abc` and recipient `a@example.com`, then posts with key `abc` and recipient `b@example.com`
- **THEN** the second response is `422` with code `idempotency_key_reused`
- **AND** the stored notification still targets `a@example.com`

#### Scenario: Conflict response does not leak the original payload
- **WHEN** a key-reuse rejection is returned
- **THEN** the response body contains neither the original recipient nor any original option value

### Requirement: Concurrent requests with the same key create at most one notification
When requests with the same `Idempotency-Key` arrive concurrently, the system SHALL create exactly one notification. Each request SHALL receive the original notification (equivalent body) or the key-reuse `422` (different body), and none SHALL receive a server error caused by the race.

#### Scenario: Simultaneous identical requests
- **WHEN** two identical requests with the same new key are processed at the same time
- **THEN** both responses carry the same notification UUID
- **AND** exactly one notification exists for that key

#### Scenario: Simultaneous requests with different bodies
- **WHEN** two requests with the same new key and different bodies are processed at the same time
- **THEN** exactly one notification exists
- **AND** one request succeeds and the other receives `422` with code `idempotency_key_reused`

### Requirement: Rejected requests do not consume the key
A request rejected with `400` or `422` SHALL NOT record its `Idempotency-Key`, so the caller can correct the request and resubmit under the same key.

#### Scenario: Corrected request reuses the key
- **WHEN** a request is rejected with `422` and the caller resubmits a corrected body with the same key
- **THEN** the corrected request is accepted with `202 Accepted`

### Requirement: Validate recipients per channel
Email recipients SHALL be syntactically valid addresses, validated without DNS or mailbox lookups. SMS recipients SHALL be valid international numbers including a country code, SHALL be stored in E.164, and no default region SHALL be assumed. Invalid recipients SHALL be rejected with `422`.

#### Scenario: Invalid email address
- **WHEN** a client posts channel `email` with recipient `not-an-email`
- **THEN** the system responds `422` identifying the recipient field

#### Scenario: Valid address on a domain with no mail records
- **WHEN** a client posts channel `email` with a syntactically valid address on a domain with no mail records
- **THEN** the request is accepted

#### Scenario: Phone number sent to email channel
- **WHEN** a client posts channel `email` with recipient `+14155550123`
- **THEN** the system responds `422` identifying the recipient field

#### Scenario: Phone number is normalized
- **WHEN** a client posts channel `sms` with recipient `+1 (415) 555-0123`
- **THEN** the stored recipient is `+14155550123`

#### Scenario: Invalid phone number
- **WHEN** a client posts channel `sms` with recipient `12345`
- **THEN** the system responds `422` identifying the recipient field

#### Scenario: SMS number without country code
- **WHEN** a client posts channel `sms` with recipient `(415) 555-0100`
- **THEN** the system responds `422` identifying the recipient field

### Requirement: Validate the template at intake
The system SHALL resolve the template matching `template_slug` and `channel`, and SHALL validate `template_options` against that template's options contract before storing anything. Failures SHALL be rejected with `422` and an error naming the problem.

#### Scenario: No template for slug and channel
- **WHEN** a client posts slug `password_reset` with channel `sms` and only an email variant exists
- **THEN** the system responds `422` stating no template exists for that slug and channel

#### Scenario: Missing required option
- **WHEN** a client omits an option the template declares as required
- **THEN** the system responds `422` naming each missing option

#### Scenario: Undeclared option
- **WHEN** a client passes an option the template does not declare
- **THEN** the system responds `422` naming each undeclared option

#### Scenario: Option has the wrong type
- **WHEN** a client passes a string for an option declared as an integer
- **THEN** the system responds `422` naming that option

#### Scenario: Template fails to render with the given options
- **WHEN** the options pass contract validation but rendering any body of the template fails
- **THEN** the system responds `422` and stores nothing

### Requirement: Validation errors do not echo sensitive values
Every `4xx` response from intake SHALL identify the offending field by name and SHALL NOT contain the submitted recipient or any submitted option value.

#### Scenario: Invalid recipient error omits the value
- **WHEN** a request is rejected for an invalid recipient
- **THEN** the error names the `recipient` field and does not contain the submitted recipient string

#### Scenario: Mistyped option error omits the value
- **WHEN** a request is rejected for a mistyped option
- **THEN** the error names the option and does not contain the submitted value
