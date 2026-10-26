# Spec Delta

## Purpose

Accepts notification requests over HTTP, rejects anything that could not be delivered as requested, and guarantees that a caller retrying the same request never creates a second notification.

## ADDED Requirements

### Requirement: Accept a valid notification request
The system SHALL accept `POST /notifications` with a JSON body containing `channel`, `template_slug`, `recipient`, and `template_options`, plus an `Idempotency-Key` header. A valid request SHALL be durably stored as a `pending` notification before the response is sent, and SHALL be answered with `202 Accepted` and a body containing the notification's public UUID and its status.

#### Scenario: Valid email request is accepted
- **WHEN** a caller posts a valid email request with an unused `Idempotency-Key`
- **THEN** the response is `202 Accepted` with a body containing a UUID `id` and `status` `pending`
- **AND** a subsequent `GET /notifications/{id}` returns that notification

#### Scenario: Acceptance does not wait for delivery
- **WHEN** a valid request is accepted
- **THEN** the response is returned without any provider having been called for that notification

#### Scenario: Internal identifier is not exposed
- **WHEN** any intake response is returned
- **THEN** it contains no internal integer identifier, only the public UUID

### Requirement: Idempotency-Key header is mandatory and bounded
The system SHALL reject a `POST /notifications` request that has no `Idempotency-Key` header, an empty key, or a key longer than 255 characters, with `400 Bad Request`, and SHALL create no notification.

#### Scenario: Missing key is rejected
- **WHEN** a caller posts an otherwise valid request without an `Idempotency-Key` header
- **THEN** the response is `400 Bad Request` and no notification is created

#### Scenario: Oversized key is rejected
- **WHEN** a caller posts a request whose `Idempotency-Key` is 256 characters long
- **THEN** the response is `400 Bad Request` and no notification is created

### Requirement: Replaying a key with an equivalent body returns the original notification
Idempotency keys SHALL be global across all callers. When a key has already been used and the new request body is equivalent to the original, the system SHALL create no new notification and SHALL respond `202 Accepted` with the original notification's UUID and its current status. Equivalence SHALL be judged on the normalized request (see "Request equivalence is judged after normalization").

#### Scenario: Exact replay returns the original
- **WHEN** a caller repeats a request with the same key and identical body
- **THEN** the response carries the same UUID as the first response and no second notification exists

#### Scenario: Replay reports current status, not original status
- **WHEN** a caller replays a request whose notification has since become `sent`
- **THEN** the response carries the original UUID and `status` `sent`

#### Scenario: Replay of a terminal failure does not resubmit
- **WHEN** a caller replays the key of a notification whose status is `failed` or `unknown`
- **THEN** the original notification is returned unchanged and nothing is resent

#### Scenario: Replay succeeds even if the template was since removed
- **WHEN** a caller replays an equivalent request after the referenced template has been removed from the deployment
- **THEN** the original notification is returned rather than a template validation error

### Requirement: Replaying a key with a different body is rejected
When a key has already been used and the new request body is not equivalent to the original, the system SHALL respond `409 Conflict`, SHALL create no notification, and SHALL leave the original notification unchanged. The error body SHALL NOT include the original request's recipient or options.

#### Scenario: Different recipient under a reused key
- **WHEN** a caller reuses a key with a different `recipient`
- **THEN** the response is `409 Conflict` and the original notification is unchanged

#### Scenario: Conflict response does not leak the original payload
- **WHEN** a `409 Conflict` is returned for key reuse
- **THEN** the response body contains neither the original recipient nor any original option value

### Requirement: Request equivalence is judged after normalization
Two requests under the same key SHALL be equivalent when their `channel`, `template_slug`, normalized `recipient`, and `template_options` are equal, where options compare as JSON values regardless of object key order or whitespace.

#### Scenario: Key order in options does not matter
- **WHEN** a replay sends the same options with object keys in a different order
- **THEN** it is treated as equivalent and returns the original notification

#### Scenario: Differently formatted phone number for the same subscriber
- **WHEN** a replay sends `+1 (415) 555-0100` where the original sent `+14155550100`
- **THEN** it is treated as equivalent because both normalize to the same E.164 number

#### Scenario: Numerically different option value
- **WHEN** a replay sends option `count` as `2` where the original sent `3`
- **THEN** it is not equivalent and the response is `409 Conflict`

### Requirement: Concurrent requests with the same key create at most one notification
When two or more requests with the same key arrive concurrently, the system SHALL create exactly one notification. Each concurrent request SHALL receive either the original notification (equivalent body) or `409 Conflict` (different body), and none SHALL receive a server error caused by the race.

#### Scenario: Simultaneous identical requests
- **WHEN** two identical requests with the same new key are processed at the same time
- **THEN** exactly one notification exists and both responses carry its UUID

### Requirement: Channel must be supported
The system SHALL accept only `email` and `sms` as `channel` values and SHALL reject any other value with `422 Unprocessable Entity`.

#### Scenario: Unsupported channel
- **WHEN** a caller posts a request with `channel` `push`
- **THEN** the response is `422 Unprocessable Entity` and no notification is created

### Requirement: Email recipients must be syntactically valid
For the `email` channel, the system SHALL reject a `recipient` that is not a syntactically valid email address with `422 Unprocessable Entity`. Validation SHALL be syntactic only and SHALL NOT depend on DNS or mailbox lookups.

#### Scenario: Malformed address
- **WHEN** an email request has recipient `alice@`
- **THEN** the response is `422 Unprocessable Entity`

#### Scenario: Validation is deterministic offline
- **WHEN** an email request has a syntactically valid address on a domain with no mail records
- **THEN** the request is accepted

### Requirement: SMS recipients are normalized to E.164
For the `sms` channel, the system SHALL parse the `recipient` as an international phone number, store it in E.164 form, and reject with `422 Unprocessable Entity` a number that is invalid or that lacks a country code. No default region SHALL be assumed.

#### Scenario: Formatted international number is normalized
- **WHEN** an SMS request has recipient `+44 20 7946 0958`
- **THEN** the request is accepted and the stored recipient is `+442079460958`

#### Scenario: Number without country code is rejected
- **WHEN** an SMS request has recipient `(415) 555-0100`
- **THEN** the response is `422 Unprocessable Entity`

### Requirement: Template and options must match a template contract
The system SHALL reject with `422 Unprocessable Entity` a request whose `template_slug` and `channel` match no loaded template variant, that omits a required option, that passes an option the variant does not declare, or that passes an option of the wrong type.

#### Scenario: Unknown template for channel
- **WHEN** a request names a slug that has an email variant but asks for `sms`
- **THEN** the response is `422 Unprocessable Entity`

#### Scenario: Missing required option
- **WHEN** a request omits an option the variant declares as required
- **THEN** the response is `422 Unprocessable Entity` naming the missing option

#### Scenario: Undeclared option
- **WHEN** a request passes an option the variant does not declare
- **THEN** the response is `422 Unprocessable Entity` naming the unexpected option

#### Scenario: Mistyped option
- **WHEN** a request passes a string for an option declared as integer
- **THEN** the response is `422 Unprocessable Entity` naming the option

### Requirement: Options must survive a dry-run render
After contract validation passes, the system SHALL render the variant with the supplied options and SHALL reject with `422 Unprocessable Entity` a request whose render fails, so that a stored notification is renderable against the template deployed at intake time.

#### Scenario: Render failure rejects the request
- **WHEN** a request's options pass the contract but cause the template render to fail
- **THEN** the response is `422 Unprocessable Entity` and no notification is created

### Requirement: Validation errors do not echo sensitive values
Every `4xx` response from intake SHALL identify the offending field by name and SHALL NOT contain the submitted recipient or any submitted option value.

#### Scenario: Invalid recipient error omits the value
- **WHEN** a request is rejected for an invalid recipient
- **THEN** the error names the `recipient` field and does not contain the submitted recipient string

#### Scenario: Mistyped option error omits the value
- **WHEN** a request is rejected for a mistyped option
- **THEN** the error names the option and does not contain the submitted value

### Requirement: Rejected requests do not consume the key
A request rejected for validation (`400` or `422`) SHALL NOT record its `Idempotency-Key`, so the caller can correct the request and resubmit under the same key.

#### Scenario: Corrected request reuses the key
- **WHEN** a request is rejected with `422` and the caller resubmits a corrected body with the same key
- **THEN** the corrected request is accepted with `202 Accepted`
