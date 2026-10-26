# Comparison Review: specs

**Main**: `specs/{notification-intake,notification-templates,notification-lifecycle}/spec.md`
**Adversary**: `adversarial/artifacts/specs/{notification-intake,notification-templates,notification-lifecycle}/spec.md`

Both versions were derived from the reconciled proposal. The adversary wrote its specs without having read the main specs.

## Grading bar

- **Value-centric**: does each requirement protect something a caller, recipient, or operator relies on (no duplicate sends, visible loss, no PHI leakage)?
- **RFC 2119**: normative SHALL/MUST and no hedged "should". A MAY is fine only where it grants an option.
- **Atomic and testable**: one behavior per requirement, and each scenario can become a test with no hidden definitions.
- **Artifact mechanics**: `## Purpose` on new capabilities, `####` scenarios, descriptions of 500 characters or fewer, no implementation names.
- **Coverage**: every behavior the proposal commits to, plus edge cases that create divergent implementations if left unstated.

Both versions pass mechanics: same three capability paths, Purpose sections present, correct heading levels, and no description over the limit. They differ on coverage and on how precisely they define things.

## Coverage comparison

| Area | Main | Adversary |
|---|---|---|
| Intake: accept, 202, UUID-only identity | Yes, plus random and unique UUIDs | Yes, plus "no provider called before 202" |
| Key required and bounded (400) | Yes, checked before body validation | Yes, no ordering stated |
| Replay returns original with current status | Yes | Yes |
| Concurrent same-key | Identical bodies only | Identical bodies, and no 5xx from the race |
| Key reuse with a different body | `422` | `409` |
| Definition of "equivalent body" | **None** | Normalized: options compared as JSON values, recipient after E.164 normalization |
| Replay of `failed`/`unknown` key returns original, no resend | No | Yes |
| Replay after the template was removed | No | Yes (returns original) |
| Rejected (400/422) requests do not consume the key | **No** | Yes |
| Intake 4xx bodies do not echo recipient or options | No | Yes |
| SMS without country code (no default region) | Ambiguous (`12345` only) | Yes, rejected |
| Phone number sent to email channel | Yes | No |
| Email validation is offline and deterministic | No | Yes |
| Templates: per slug/channel, YAML manifest, sibling bodies | Yes | Yes |
| Typed options, defaults, optional-absent | Yes | Yes, but with an internal contradiction (see below) |
| Startup validation | Malformed, missing body, **duplicate slug+channel**, undeclared variable; API and worker | Malformed, missing body, unsupported type, undeclared variable; "one bad template blocks deploy" |
| Strict rendering (no silent empty substitution) | No | Yes |
| HTML escaping only in HTML body | Yes | Yes |
| Subject single-line (header injection) | No | Yes |
| Non-empty parts | In scenarios only | Normative requirement, and the render fails otherwise |
| Deterministic render | No | Yes |
| Lifecycle: statuses, transitions, terminal immutability | Yes | Yes |
| Claim records worker identity and claim time; oldest-first | **Yes** | No |
| Rendered content not persisted | **Yes** | No |
| `sent` records sent time and provider message id | **Yes** | No |
| Named "At-most-once delivery" requirement | **Yes** | Spread across several requirements |
| Unclassifiable provider response → `unknown` | No | Yes |
| Late worker vs. reaper | Reaper case only; "still holds" undefined | Defined, but too strict (see below) |
| Provider timeout < lease enforced at startup | No | Yes |
| Discarded late outcome is logged | **Yes** | No |
| `last_error` content | Must not contain recipient or option values | Service-defined category only, no raw provider text, null until an unsuccessful attempt |
| GET excludes recipient | Implied by the field list only | Explicit |
| Malformed UUID on GET | `422` | `404` |

## Material divergences

### Intake

1. **Key reuse with a different body: `422` (Main) vs `409` (Adversary).** Main is stronger. The IETF `Idempotency-Key` header draft names `422` for a key reused with a different payload and reserves `409` for a request that arrives while the original is still in flight. Main's weakness is that this `422` cannot be told apart from an ordinary validation `422`, and callers need to branch on it. Keep `422`, but require a distinct, machine-readable error code for key reuse.

2. **Definition of "equivalent body" (Adversary stronger, blocking).** Main says "whose body differs from the original" but never defines "differs". Two reasonable implementations would disagree on whether reordered option keys, `+1 (415) 555-0123` versus `+14155550123`, or `1` versus `1.0` count as different. That means Main's 422 scenario can't be tested at the edges, and callers retrying through a re-serializing proxy would get spurious rejections. The adversary compares after normalization. The `1` vs `1.0` case still needs a decision: say whether options are compared as typed JSON values after contract coercion.

3. **Rejected requests do not consume the key (Adversary stronger, blocking).** Main is silent. If a `422` records the key, a caller who fixes a typo and retries with the same key hits the different-body rejection forever. The proposal tells callers to "keep the key they sent", so this rule is part of the caller contract.

4. **Replaying a key whose notification is `failed`/`unknown`, or whose template was removed (Adversary stronger).** The proposal tells callers to resubmit `failed` with a *new* key, so a same-key replay must return the original and must not resend. That is the at-most-once guarantee seen from intake. Returning the original even after the template is gone pins down the order "idempotency lookup before template validation". Main's "key checked before body validation" covers the header only, not the lookup.

5. **Intake error bodies must not echo PHI (Adversary stronger).** Main protects `last_error` but lets `422` bodies echo the submitted recipient and options. Frameworks echo input in validation errors by default, so leaving this unspecified means it will leak.

6. **SMS default region (Adversary stronger).** Main's `12345` scenario does not settle `(415) 555-0123`. Leaving it open means the implementation will quietly assume a default region.

7. **Main-only strengths to keep:** random and unique UUIDs, checking the key before the body, and the "phone number sent to email channel" scenario.

### Templates

8. **Duplicate slug+channel at startup (Main stronger).** Main's manifests declare their own slug and channel, so duplicates can happen and must fail startup. Keep it.

9. **Strict rendering (Adversary stronger, but its text needs fixing).** Main has no rule against a template silently rendering a missing value as an empty string, which sends a message like "Your appointment is at ." The adversary's version contradicts itself: its options requirement says optional options without a default render as absent, but its strict-rendering requirement says a reference to an unsupplied value with no default fails. Main's "optional without a default ... does not fail" sharpens the conflict. The rule needs to say: an absent optional option is undefined; a template must guard it explicitly, and an unguarded reference fails the render. Startup validation can't catch an unguarded optional reference statically, so the intake dry-run render is what catches it. Add a scenario for it.

10. **Single-line subject (Adversary stronger).** A newline in an option rendered into the subject is a header-injection vector, which matters with real providers later. Adopt it.

11. **Non-empty parts as a normative rule (Adversary stronger).** Main only says "non-empty" inside THEN clauses. Neither version says what happens when a part renders empty. Adopt the adversary's rule that the render fails.

12. **Determinism and unsupported option type at startup (Adversary, minor).** Determinism is what makes the intake dry-run render meaningful. Adopt both cheaply.

### Lifecycle

13. **Claim metadata, oldest-first, sent metadata, non-persisted rendering, logging discarded outcomes (Main stronger).** These are observable and useful to operators. The adversary dropped them. Keep all of them.

14. **"Still holds" for late workers (both weak, blocking).** Main says a worker records an outcome only for a notification "it still holds", which is undefined when the lease has expired but the reaper hasn't run yet. The adversary's version discards the outcome whenever the lease has expired. That is safe, but it throws away a confirmed `sent` that arrived a moment late and turns it into `unknown`, which is the very loss the proposal is trying to keep small. Recommended rule: a worker's outcome is recorded only if the row is still `processing` *under that worker's claim*, applied as a single atomic conditional update. Whichever of the worker and the reaper commits first wins, and the reaper only acts on rows whose lease has expired. Add scenarios for "lease expired, reaper not yet run, worker reports sent → sent" and "reaper committed first → stays unknown". The adversary's "superseded claim" scenario can't happen under this state machine (a reclaim requires the earlier attempt to have already recorded `pending`), so drop it.

15. **Unclassifiable provider response → `unknown` (Adversary stronger).** Main lists timeout and dropped connection only. A `200` with an unparseable body, or an unexpected status code, has to land somewhere, and under at-most-once the only safe place is `unknown`.

16. **Timeout < lease checked at startup (Adversary stronger).** The proposal calls this an operational constraint that, if violated, silently loses sends. Failing at startup turns a silent loss into a deploy failure. Adopt it.

17. **`last_error` content (Adversary stronger, blocking).** Main records "the error" from the provider and then requires that it not contain the recipient or option values. Free-form provider text often echoes the recipient, and you can't reliably scrub every option value out of arbitrary text. The only testable form of Main's own rule is the adversary's: `last_error` is a service-defined category with a fixed message and no raw provider text. Raw provider detail, if it's wanted, goes to logs under the same PHI rules.

18. **GET excludes the recipient explicitly (Adversary stronger, minor).** Main excludes options and rendered content but not the recipient. Its field list implies the exclusion, but make it explicit.

19. **Malformed UUID: `422` (Main) vs `404` (Adversary).** Either is defensible. Main's `422` is what the stack produces by default and keeps "doesn't exist" separate from "can't parse". Keep Main.

20. **At-most-once framing (Main stronger).** Main's named requirement states the product guarantee in one place. Keep it as the anchor, and put the adversary's unclassifiable-response and timeout/lease scenarios under it.

## Merge recommendation

**notification-intake**: Keep Main's structure. Then:
- Keep `422` for key reuse with a different body, and require a distinct error code (e.g. `idempotency_key_reused`) in the error body.
- Add a requirement **Request equivalence is judged after normalization**, with the adversary's three scenarios. Decide whether `1` and `1.0` are equivalent and add a scenario for it.
- Add **Rejected requests do not consume the key** (adversary text and scenario).
- Add the adversary's scenarios "Replay of a terminal failure does not resubmit" and "Replay succeeds even if the template was since removed" to Main's replay requirement, and state that the idempotency lookup precedes template validation.
- Add the adversary's **Validation errors do not echo sensitive values**.
- Extend Main's recipient requirement with "no default region" and the adversary's `(415) 555-0100` scenario. Keep Main's phone-to-email scenario. Optionally add the offline email-validation scenario.
- Extend Main's concurrent scenario: concurrent same-key requests with *different* bodies produce one notification and one key-reuse rejection, with no `5xx`.

**notification-templates**: Keep Main (including duplicate detection and "API or worker" startup). Then:
- Add **Rendering is strict**, worded so that absent optional options must be guarded and an unguarded reference fails the render. Reconcile this with Main's "Optional option without a default" scenario (the guarded case passes) and add an "unguarded optional reference fails at intake dry-run" scenario.
- Add **Rendered subjects are single-line and non-empty** and **Rendered bodies are non-empty**.
- Add startup failure for an unsupported option type, the "one bad template blocks the deploy" scenario, and **Rendering is deterministic**.

**notification-lifecycle**: Keep Main as the base. Then:
- Rewrite **Stale workers cannot overwrite outcomes** around "still `processing` under this worker's claim, recorded by an atomic conditional update", with the two race scenarios from item 14. Keep Main's logging scenario. Do not import the adversary's superseded-claim scenario.
- Add "unclassifiable provider response → `unknown`" under **At-most-once delivery**.
- Add **Provider timeout must be shorter than the lease** (startup failure).
- Replace "error recorded" in Main's retry, at-most-once, and render-failure requirements with the adversary's **Last error is a sanitized classification**: category plus fixed message, no raw provider text, null until an unsuccessful attempt. Keep Main's GET scenario that `last_error` omits the phone number.
- Add "recipient" to the GET exclusion list. Keep `422` for a malformed UUID.

## Blocking before archive

1. **Define body equivalence for idempotent replay** (item 2). Without a definition, the key-reuse rejection can't be tested and implementations will differ.
2. **Rejected requests must not consume the key** (item 3). It is part of the caller contract the proposal states.
3. **Define the late-worker vs. reaper race** (item 14). "Still holds" is ambiguous in exactly the window the lease mechanism exists for.
4. **Remove raw provider text from `last_error`** (item 17). As written, Main's rule can't be met reliably and leaks PHI through `GET`.
5. **Resolve optional-absent vs. strict rendering** (item 9) before adopting strict rendering, so the merged spec doesn't contradict itself.
