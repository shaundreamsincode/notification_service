# Spec Delta

## Purpose

Owns the notification state machine from acceptance to a terminal outcome: exclusive claiming by workers, leases, retries, sending through the channel provider at most once, and the status view callers read back.

## ADDED Requirements

### Requirement: Notification statuses and transitions
A notification SHALL be in exactly one of `pending`, `processing`, `sent`, `failed`, or `unknown`. The only allowed transitions SHALL be `pending` to `processing`, `processing` to `pending`, `processing` to `sent`, `processing` to `failed`, and `processing` to `unknown`. `sent`, `failed`, and `unknown` are terminal and SHALL never change.

#### Scenario: New notification starts pending
- **WHEN** a notification is accepted at intake
- **THEN** its status is `pending`

#### Scenario: Terminal status never changes
- **WHEN** a notification is `sent`, `failed`, or `unknown`
- **THEN** no later worker, reaper, or retry changes its status

#### Scenario: Pending cannot skip processing
- **WHEN** a notification is `pending`
- **THEN** the only status it can move to is `processing`

### Requirement: Workers claim pending notifications exclusively
A worker SHALL only process a notification it has claimed. Claiming SHALL atomically move a notification from `pending` to `processing`, increment its attempt count, and record which worker claimed it and when. Two workers SHALL never claim the same notification at the same time.

#### Scenario: Concurrent workers do not share work
- **WHEN** two workers poll at the same time and one pending notification is eligible
- **THEN** exactly one worker claims it
- **AND** the other worker claims nothing or a different notification

#### Scenario: Claim updates the notification
- **WHEN** a worker claims a pending notification with attempt count 0
- **THEN** its status is `processing`, its attempt count is 1, and it records the worker's identity and claim time

### Requirement: Only eligible notifications are claimed
A worker SHALL only claim notifications whose status is `pending` and whose next-eligible time is now or earlier. Eligible notifications SHALL be claimed oldest-eligible first.

#### Scenario: Notification in backoff is skipped
- **WHEN** a pending notification's next-eligible time is in the future
- **THEN** no worker claims it until that time passes

#### Scenario: Terminal notifications are never claimed
- **WHEN** a notification's status is `sent`, `failed`, or `unknown`
- **THEN** no worker claims it

### Requirement: Render at send time
The worker SHALL render the notification's template from the stored slug, channel, and options immediately before sending. Rendered content SHALL NOT be persisted.

#### Scenario: Rendering failure is permanent
- **WHEN** a claimed notification's template no longer exists or fails to render
- **THEN** the notification is marked `failed` with a render-failure last error set
- **AND** nothing is sent

### Requirement: Send with a provider idempotency key
Each send SHALL pass the notification's UUID to the provider as an idempotency key, so that providers which honor such keys deduplicate any repeated send of the same notification.

#### Scenario: Idempotency key is the notification UUID
- **WHEN** a worker sends the notification with UUID `U`
- **THEN** the provider receives `U` as the idempotency key

#### Scenario: Retry reuses the same key
- **WHEN** the notification with UUID `U` is sent on its second attempt
- **THEN** the provider receives the same idempotency key `U` as on the first attempt

### Requirement: Record successful sends
When the provider confirms acceptance, the notification SHALL be marked `sent` with the send time and the provider's message id.

#### Scenario: Provider accepts the message
- **WHEN** the provider confirms acceptance and returns message id `SM123`
- **THEN** the notification's status is `sent`, its sent time is set, and its provider message id is `SM123`

### Requirement: At-most-once delivery
A notification SHALL NOT be sent again when the system cannot tell whether a previous attempt reached the provider. That includes timeouts, dropped connections, expired leases, and any provider response that is neither a recognizable acceptance nor a rejection. Such notifications SHALL be marked `unknown` with the last error set.

#### Scenario: Provider call times out
- **WHEN** the provider call times out or the connection drops after the request was sent
- **THEN** the notification is marked `unknown` with the last error set
- **AND** it is never claimed again

#### Scenario: Worker dies mid-send
- **WHEN** a notification has been `processing` for longer than the configured lease timeout
- **THEN** the system marks it `unknown` with a lease-expired last error set
- **AND** it is never claimed again

#### Scenario: Unclassifiable response is unknown
- **WHEN** the provider returns a response the adapter cannot classify as acceptance or rejection
- **THEN** the notification is marked `unknown`
- **AND** it is never claimed again

### Requirement: Retry definite rejections with backoff
When the provider definitely did not accept the message and the error is transient, the notification SHALL return to `pending` with a next-eligible time set by exponential backoff, until it reaches the configured maximum attempt count. Each unsuccessful attempt SHALL set the last error.

#### Scenario: Transient rejection is retried
- **WHEN** the provider rejects with a rate-limit error on attempt 1 of 5
- **THEN** the notification returns to `pending` with a later next-eligible time and the last error set

#### Scenario: Attempts exhausted
- **WHEN** a transient rejection occurs on the final allowed attempt
- **THEN** the notification is marked `failed` with the last error set

#### Scenario: Permanent rejection is not retried
- **WHEN** the provider rejects the message as invalid, such as an unreachable recipient
- **THEN** the notification is marked `failed` on that attempt with the last error set

### Requirement: Stale workers cannot overwrite outcomes
A worker SHALL record an outcome only if the notification is still `processing` under that worker's own claim, as one atomic conditional update. The lease reaper SHALL act only on notifications whose lease has expired. Whichever of worker and reaper commits first decides the outcome, and the other's update SHALL be discarded and logged.

#### Scenario: Late success before the reaper runs
- **WHEN** a worker's lease has expired, the reaper has not yet run, and the worker reports confirmed acceptance
- **THEN** the notification becomes `sent`

#### Scenario: Reaper committed first
- **WHEN** the reaper marks a notification `unknown` and the original worker then reports success
- **THEN** the notification stays `unknown`
- **AND** the discarded outcome is logged with the notification UUID and provider message id

#### Scenario: Unexpired lease is left alone
- **WHEN** a `processing` notification's lease has not expired
- **THEN** the reaper does not change it

### Requirement: Provider timeout must be shorter than the lease
The service SHALL refuse to start if the configured provider call timeout is greater than or equal to the configured lease duration.

#### Scenario: Misconfigured timeout
- **WHEN** the provider timeout is set to 60 seconds and the lease to 30 seconds
- **THEN** the process exits at startup with a configuration error

### Requirement: Last error is a sanitized classification
The last error SHALL be a service-defined category with a fixed message, set whenever an attempt ends other than in a confirmed send. It SHALL NOT include raw provider text, the recipient, or any option value. It SHALL be null until an attempt is unsuccessful.

#### Scenario: Provider echoes the recipient in its error
- **WHEN** a provider's rejection message contains the recipient's phone number
- **THEN** the stored and returned last error contains only the service's category and not the phone number

#### Scenario: Unknown outcome records a reason
- **WHEN** a notification becomes `unknown` because its lease expired
- **THEN** its last error indicates lease expiry

### Requirement: Report notification status
The system SHALL expose `GET /notifications/{uuid}` returning the notification's UUID, channel, template slug, status, attempt count, last error, and timestamps. It SHALL NOT return the recipient, template options, or rendered content. The last error SHALL follow the sanitized-classification requirement. Unknown UUIDs SHALL return `404`, and malformed UUIDs SHALL return `422`.

#### Scenario: Fetch an existing notification
- **WHEN** a client requests a notification by a UUID that exists
- **THEN** the system responds `200` with its UUID, channel, template slug, status, attempt count, last error, and timestamps

#### Scenario: Status view omits recipient and options
- **WHEN** a client requests any notification
- **THEN** the response contains neither the recipient nor any option value

#### Scenario: Last error omits sensitive values
- **WHEN** a notification failed because the provider rejected recipient `+14155550123`
- **THEN** the returned last error does not contain `+14155550123`

#### Scenario: Fetch an unknown notification
- **WHEN** a client requests a well-formed UUID that matches no notification
- **THEN** the system responds `404`

#### Scenario: Fetch with a malformed UUID
- **WHEN** a client requests `GET /notifications/123`
- **THEN** the system responds `422`
