# Spec Delta

## Purpose

Governs a notification from acceptance to a single explicit outcome: exclusive claiming under a lease, at-most-once sending through a channel provider, retry of transient rejections, and a status view callers can read back.

## ADDED Requirements

### Requirement: Notification statuses and allowed transitions
A notification SHALL have exactly one status from `pending`, `processing`, `sent`, `failed`, `unknown`. The only allowed transitions SHALL be `pending`→`processing`, `processing`→`sent`, `processing`→`pending`, `processing`→`failed`, and `processing`→`unknown`.

#### Scenario: New notification starts pending
- **WHEN** a notification is accepted
- **THEN** its status is `pending` with an attempt count of `0`

#### Scenario: No transition skips processing
- **WHEN** a `pending` notification has not been claimed
- **THEN** it cannot become `sent`, `failed`, or `unknown`

### Requirement: Terminal statuses are immutable
`sent`, `failed`, and `unknown` SHALL be terminal. Once a notification reaches a terminal status, no worker, reaper, or API request SHALL change its status, attempt count, or last error.

#### Scenario: Unknown is never retried
- **WHEN** a notification is `unknown`
- **THEN** it is never claimed again and no provider is called for it again

### Requirement: Claiming is exclusive and leased
A worker SHALL claim only `pending` notifications whose next attempt time has arrived. Claiming SHALL set the status to `processing`, increment the attempt count, and grant a lease with an expiry. No two workers SHALL hold a claim on the same notification at the same time.

#### Scenario: Concurrent workers do not double-claim
- **WHEN** two workers poll at the same moment and one notification is eligible
- **THEN** exactly one worker claims it

#### Scenario: Claim increments attempts
- **WHEN** a notification with attempt count `1` is claimed
- **THEN** its status is `processing` and its attempt count is `2`

#### Scenario: Backed-off notification is not claimed early
- **WHEN** a `pending` notification's next attempt time is in the future
- **THEN** no worker claims it

### Requirement: Rendering happens at send time against the deployed template
A worker SHALL render the notification with the template variant deployed in its own process at send time. If the render fails, the notification SHALL become `failed` without any provider call.

#### Scenario: Template changed incompatibly while pending
- **WHEN** a pending notification's options no longer satisfy the deployed variant at send time
- **THEN** the notification becomes `failed` and no provider is called

### Requirement: Provider calls carry the notification UUID as idempotency key
Every provider call for a notification SHALL pass the notification's public UUID as the provider idempotency key, and the same UUID SHALL be used on every attempt for that notification.

#### Scenario: Retry reuses the same key
- **WHEN** a notification is sent on its second attempt after a transient rejection
- **THEN** the provider receives the same idempotency key as on the first attempt

### Requirement: Provider outcomes determine the next status
The worker SHALL classify each provider call: a confirmed acceptance SHALL make the notification `sent`; a transient rejection SHALL return it to `pending` (or `failed` if attempts are exhausted); a permanent rejection SHALL make it `failed`; a timeout, dropped connection, or any response that does not establish acceptance or rejection SHALL make it `unknown`.

#### Scenario: Confirmed send
- **WHEN** the provider confirms acceptance
- **THEN** the notification becomes `sent`

#### Scenario: Permanent rejection
- **WHEN** the provider rejects the message as permanently undeliverable
- **THEN** the notification becomes `failed` and is not retried

#### Scenario: Timeout is unknown, not failed
- **WHEN** the provider call times out
- **THEN** the notification becomes `unknown` and is not retried

#### Scenario: Unclassifiable response is unknown
- **WHEN** the provider returns a response the adapter cannot classify as acceptance or rejection
- **THEN** the notification becomes `unknown`

### Requirement: Transient rejections retry with backoff up to a limit
After a transient rejection, the notification SHALL return to `pending` with a next attempt time that grows with each attempt. When the attempt count has reached the configured maximum, a transient rejection SHALL instead make the notification `failed`.

#### Scenario: Retry is delayed
- **WHEN** attempt 1 ends in a transient rejection
- **THEN** the notification is `pending` with a next attempt time later than now

#### Scenario: Backoff grows
- **WHEN** attempts 1 and 2 both end in transient rejections
- **THEN** the delay before attempt 3 is longer than the delay before attempt 2

#### Scenario: Attempts exhausted
- **WHEN** the final allowed attempt ends in a transient rejection
- **THEN** the notification becomes `failed`

### Requirement: Expired leases resolve to unknown
A lease reaper SHALL mark every `processing` notification whose lease has expired as `unknown`. This SHALL apply regardless of whether the provider was ever called, because the service cannot tell.

#### Scenario: Worker crash mid-send
- **WHEN** a worker dies while holding a claim and its lease expires
- **THEN** the reaper marks the notification `unknown`

#### Scenario: Unexpired lease is left alone
- **WHEN** a `processing` notification's lease has not expired
- **THEN** the reaper does not change it

### Requirement: Late workers cannot overwrite a resolved outcome
A worker SHALL record an outcome only if the notification is still `processing` under the worker's own claim. If the lease has expired or the notification has been resolved by anyone else, the worker's outcome SHALL be discarded and the existing status SHALL stand.

#### Scenario: Late success after reaping
- **WHEN** the reaper marks a notification `unknown` and the original worker then receives a confirmed acceptance
- **THEN** the notification remains `unknown`

#### Scenario: Late worker after re-claim
- **WHEN** a worker's claim has been superseded by a newer claim on the same notification
- **THEN** the older worker's outcome is not recorded

### Requirement: Provider timeout must be shorter than the lease
The service SHALL refuse to start if the configured provider call timeout is greater than or equal to the configured lease duration.

#### Scenario: Misconfigured timeout
- **WHEN** the provider timeout is set to 60 seconds and the lease to 30 seconds
- **THEN** the worker process exits at startup with a configuration error

### Requirement: Status view
`GET /notifications/{uuid}` SHALL return the notification's UUID, channel, template slug, status, attempt count, created and last-updated timestamps, and last error. It SHALL NOT return the recipient, the template options, or the internal identifier.

#### Scenario: Read back a sent notification
- **WHEN** a caller gets an existing notification that was sent
- **THEN** the response is `200 OK` with `status` `sent` and its attempt count

#### Scenario: Status view omits recipient and options
- **WHEN** a caller gets any notification
- **THEN** the response contains neither the recipient nor any option value

#### Scenario: Unknown or malformed UUID
- **WHEN** a caller gets a UUID that does not exist, or a path segment that is not a UUID
- **THEN** the response is `404 Not Found`

### Requirement: Last error is a sanitized classification
The last error SHALL be a service-defined error category with a fixed message, set when an attempt ends in anything other than a confirmed send. It SHALL NOT include raw provider response text, the recipient, or any option value. It SHALL be null for a notification that has never had an unsuccessful attempt.

#### Scenario: Provider echoes the recipient in its error
- **WHEN** a provider's rejection message contains the recipient's phone number
- **THEN** the stored and returned last error contains only the service's category and does not contain the phone number

#### Scenario: Unknown outcome records a reason
- **WHEN** a notification becomes `unknown` because its lease expired
- **THEN** its last error indicates lease expiry
