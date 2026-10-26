# Spec Delta

## Purpose

Defines notification templates per slug and channel, declares the options each template accepts, and renders the channel-specific bodies that providers deliver.

## ADDED Requirements

### Requirement: Templates are defined per slug and channel
Each template SHALL be identified by a slug and a channel. A slug MAY have variants for several channels, and each variant SHALL be defined independently. Templates SHALL be stored as files in the codebase, not in the database.

#### Scenario: Slug with two channel variants
- **WHEN** `appointment_reminder` has both an email and an SMS variant
- **THEN** requests for either channel resolve to that channel's variant

#### Scenario: Slug with one channel variant
- **WHEN** `password_reset` has only an email variant
- **THEN** a request for `password_reset` on SMS resolves to no template

### Requirement: Manifest declares the template
Each template variant SHALL have a YAML manifest declaring its slug, channel, and options contract. Email manifests SHALL also declare a subject line. Template bodies SHALL live in separate files beside the manifest, not inside the YAML.

#### Scenario: Email variant files
- **WHEN** an email variant of `password_reset` is defined
- **THEN** it consists of a YAML manifest, an HTML body file, and a plain-text body file

#### Scenario: SMS variant files
- **WHEN** an SMS variant of `appointment_reminder` is defined
- **THEN** it consists of a YAML manifest and a plain-text body file

### Requirement: Options contract
Each manifest SHALL declare every option the template accepts. Each option SHALL declare a type of `string`, `integer`, `number`, or `boolean`, and whether it is required. Optional options MAY declare a default applied when omitted; an omitted optional option with no default is undefined.

#### Scenario: Default is applied
- **WHEN** an option is declared optional with default `our clinic` and the request omits it
- **THEN** rendering uses `our clinic` for that option

#### Scenario: Optional option without a default
- **WHEN** an option is declared optional with no default and the request omits it
- **THEN** the option is undefined in the render, as defined by the strict rendering requirement

### Requirement: Templates are validated at startup
The system SHALL load and validate every template when the API or a worker starts, and SHALL NOT serve with a partial set. Startup SHALL fail naming the template if a manifest is malformed, a body file is missing, two manifests claim the same slug and channel, an option type is unsupported, or a body or subject references an undeclared variable.

#### Scenario: Body references an undeclared variable
- **WHEN** an HTML body uses `{{ clinic_phone }}` and the manifest does not declare `clinic_phone`
- **THEN** startup fails with an error naming the template and the variable

#### Scenario: Missing body file
- **WHEN** an email manifest exists without a plain-text body file
- **THEN** startup fails with an error naming the template

#### Scenario: Duplicate slug and channel
- **WHEN** two manifests both declare slug `password_reset` and channel `email`
- **THEN** startup fails with an error naming both files

#### Scenario: Unsupported option type
- **WHEN** a manifest declares an option with type `date`
- **THEN** startup fails with an error naming the template and the option

#### Scenario: One bad template blocks the whole deploy
- **WHEN** one template among many is invalid
- **THEN** the process does not start serving with the valid subset

### Requirement: Rendering is strict
Rendering SHALL fail rather than substitute an empty string for an undefined value. An omitted optional option with no default is undefined, so a template SHALL guard its use, and an unguarded reference fails the render.

#### Scenario: Missing value fails the render
- **WHEN** a variant is rendered without a value for a referenced option that has no default
- **THEN** the render fails with an error naming the option

#### Scenario: Guarded reference to an omitted optional option
- **WHEN** an optional option with no default is omitted and the template guards its use
- **THEN** the render succeeds

#### Scenario: Unguarded reference to an omitted optional option
- **WHEN** an optional option with no default is omitted and the template references it without a guard
- **THEN** the render fails
- **AND** intake rejects the request with `422`

### Requirement: Email rendering
Rendering an email variant SHALL produce a subject, an HTML body, and a plain-text body from the same options. Option values inserted into the HTML body SHALL be HTML-escaped. The subject and plain-text body SHALL NOT be HTML-escaped.

#### Scenario: Email renders three parts
- **WHEN** an email variant is rendered with valid options
- **THEN** the result contains a non-empty subject, HTML body, and plain-text body

#### Scenario: HTML injection is escaped
- **WHEN** the option `patient_name` is `<script>alert(1)</script>`
- **THEN** the HTML body contains the escaped text and no `<script>` element
- **AND** the plain-text body contains the value unescaped

### Requirement: SMS rendering
Rendering an SMS variant SHALL produce a single plain-text body. Option values SHALL NOT be HTML-escaped.

#### Scenario: SMS renders a text body
- **WHEN** an SMS variant is rendered with valid options
- **THEN** the result is one non-empty plain-text body

### Requirement: Rendered subjects are single-line and non-empty
A rendered email subject SHALL be non-empty after trimming and SHALL NOT contain line breaks. A render producing an empty subject or a subject with a line break SHALL fail.

#### Scenario: Line break in subject fails the render
- **WHEN** an option containing a newline is rendered into the subject
- **THEN** the render fails

### Requirement: Rendered bodies are non-empty
A render SHALL fail if any required body part, HTML or plain text for email or the body for SMS, is empty after trimming whitespace.

#### Scenario: Empty SMS body fails the render
- **WHEN** an SMS variant renders to only whitespace for the supplied options
- **THEN** the render fails

### Requirement: Rendering is deterministic
Rendering the same variant with the same options SHALL produce identical output, so that the intake dry-run render and the send-time render agree whenever the template has not changed.

#### Scenario: Repeated render matches
- **WHEN** a variant is rendered twice with the same options
- **THEN** both outputs are byte-for-byte identical
