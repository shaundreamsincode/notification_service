# Templates

A template is a set of files in `templates/<slug>/`. Each channel variant of a
slug is defined independently:

| Channel | Files |
|---|---|
| `email` | `email.yaml`, `email.html`, `email.txt` |
| `sms` | `sms.yaml`, `sms.txt` |

The body files share the manifest's file name. Bodies are Jinja2, never inline in
the YAML. Both the API and the worker load and validate every template at
startup and refuse to start if any one is invalid, naming the template.

## Manifest

```yaml
slug: appointment_reminder
channel: email
subject: "Appointment reminder for {{ appointment_time }}"
options:
  patient_name: {type: string}
  appointment_time: {type: string}
  clinic: {type: string, required: false, default: our clinic}
  instructions: {type: string, required: false}
```

| Field | Meaning |
|---|---|
| `slug` | Template name. Together with `channel` it must be unique. |
| `channel` | `email` or `sms`. |
| `subject` | Email only. Rendered as a template; must come out non-empty and on one line. |
| `options` | Every option the template accepts, by name. |

Each option declares:

| Field | Meaning |
|---|---|
| `type` | `string`, `integer`, `number`, or `boolean`. Types are strict: `"3"` is not an integer. |
| `required` | Defaults to `true`. |
| `default` | Optional options only. Applied when the option is omitted. |

A request may not pass an option the manifest does not declare. A body or the
subject may not reference a variable the manifest does not declare, and no Jinja
globals (such as `range`) are available.

## Optional options need a guard

An optional option with no `default` is **undefined** when omitted, not empty.
Rendering is strict, so an unguarded `{{ instructions }}` fails the render and
intake rejects the request with `422`. Guard every use:

```jinja
{% if instructions is defined %}{{ instructions }}{% endif %}
```

## Escaping

Option values in `.html` bodies are HTML-escaped. Subjects, `.txt` bodies and SMS
bodies are not escaped. Every rendered part must be non-empty, and a subject
must not contain a line break.

## Changing templates while notifications are pending

A pending notification is rendered at send time against the template deployed
then. If the option contract changed incompatibly, the notification becomes
`failed`. While notifications can be pending, keep changes backward compatible:

- Do not remove or rename an option, or change its type.
- Do not make an optional option required.
- Adding an optional option is safe.
