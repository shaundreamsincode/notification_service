# Spec Delta

## Purpose

Defines file-based notification templates per slug and channel, each with a typed options contract, and guarantees that every template the service runs with has been validated and renders deterministically.

## ADDED Requirements

### Requirement: Templates are organized by slug and channel variant
The system SHALL load templates from a template directory containing one subdirectory per slug. Each slug SHALL have one or more channel variants (`email`, `sms`), each declared by a YAML manifest with its body files beside it. A slug and channel SHALL identify at most one variant.

#### Scenario: Slug with both channels
- **WHEN** slug `appointment-reminder` has an email manifest and an SMS manifest
- **THEN** requests for either channel with that slug resolve to the matching variant

#### Scenario: Slug with one channel only
- **WHEN** slug `password-reset` has only an email variant
- **THEN** a request for `sms` with that slug matches no template

### Requirement: Manifests declare a typed options contract
Each manifest SHALL declare every option the variant accepts, each with a name, a type from a fixed set (`string`, `integer`, `number`, `boolean`), and whether it is required. Optional options SHALL be either given a declared default or rendered as absent.

#### Scenario: Declared optional option with default
- **WHEN** a manifest declares optional option `clinic_name` with default `our clinic` and a request omits it
- **THEN** the render uses `our clinic`

### Requirement: Email variants define subject, HTML, and plain text
An email variant SHALL declare a subject template in its manifest and SHALL have both an HTML body file and a plain-text body file. An SMS variant SHALL have exactly one plain-text body file and SHALL NOT declare a subject.

#### Scenario: Email render produces three parts
- **WHEN** an email variant is rendered with valid options
- **THEN** the result contains a subject, an HTML body, and a plain-text body

#### Scenario: SMS render produces one body
- **WHEN** an SMS variant is rendered with valid options
- **THEN** the result contains exactly one plain-text body and no subject

### Requirement: All templates are validated at startup
The system SHALL load and validate every template before serving requests or claiming work. Startup SHALL fail, naming the slug, channel, and problem, if any manifest is malformed, a required body file is missing, an option type is not in the supported set, or a subject or body references an option the manifest does not declare.

#### Scenario: Undeclared reference fails startup
- **WHEN** an SMS body references `{{ provider_name }}` and the manifest does not declare `provider_name`
- **THEN** the process exits at startup with an error naming the slug, the `sms` channel, and `provider_name`

#### Scenario: Missing body file fails startup
- **WHEN** an email variant has no plain-text body file
- **THEN** the process exits at startup with an error naming the slug and the missing file

#### Scenario: One bad template blocks the whole deploy
- **WHEN** one template among many is invalid
- **THEN** the process does not start serving with the valid subset

### Requirement: Rendering is strict
Rendering SHALL fail rather than produce output when a template references a value that is not supplied and has no default. A render SHALL NOT silently substitute an empty string for a missing value.

#### Scenario: Missing value fails the render
- **WHEN** a variant is rendered without a value for a referenced option that has no default
- **THEN** the render fails with an error naming the option

### Requirement: HTML bodies escape option values
When rendering an email HTML body, the system SHALL HTML-escape option values. Plain-text bodies, SMS bodies, and subjects SHALL NOT be HTML-escaped.

#### Scenario: Markup in an option is escaped in HTML
- **WHEN** option `name` is `<b>Al</b>` and the HTML body renders `{{ name }}`
- **THEN** the HTML body contains `&lt;b&gt;Al&lt;/b&gt;`

#### Scenario: Plain text is not escaped
- **WHEN** the same option renders in the plain-text body
- **THEN** the plain-text body contains `<b>Al</b>`

### Requirement: Rendered subjects are single-line and non-empty
A rendered email subject SHALL be non-empty after trimming and SHALL NOT contain line breaks. A render producing an empty subject or a subject with a line break SHALL fail.

#### Scenario: Line break in subject fails the render
- **WHEN** an option containing a newline is rendered into the subject
- **THEN** the render fails

### Requirement: Rendered bodies are non-empty
A render SHALL fail if any required body part (HTML or plain text for email, the body for SMS) is empty after trimming whitespace.

#### Scenario: Empty SMS body fails the render
- **WHEN** an SMS variant renders to only whitespace for the supplied options
- **THEN** the render fails

### Requirement: Rendering is deterministic
Rendering the same variant with the same options SHALL produce identical output, so that the intake dry-run render and the send-time render agree whenever the template has not changed.

#### Scenario: Repeated render matches
- **WHEN** a variant is rendered twice with the same options
- **THEN** both outputs are byte-for-byte identical
