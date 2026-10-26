# Comparison review: tasks

Main: `openspec/changes/add-notification-service/tasks.md` (8 groups, 33 tracked tasks plus workflow follow-up).
Adversary: `openspec/changes/add-notification-service/adversarial/artifacts/tasks.md` (8 groups, 28 tracked tasks).

Both were derived from the reconciled specs and design. The adversary never saw the main version before this review.

## Grading bar

- Every task states how to verify completion (schema instruction).
- Each group lands its own tests and docs; no testing or docs pile-up in a final group (schema instruction).
- Ordering follows dependency.
- Tasks fit one session.
- Every spec scenario and every reconciled design decision (including the deferred items from the specs and design reviews) maps to a task.
- Checkbox mechanics are correct, and workflow follow-up is kept as plain bullets.

## Coverage comparison

Both versions cover the same skeleton: scaffold and settings, schema, template registry, intake, status, provider boundary, worker, and end-to-end checks. Both pick up the design-review deferrals:

| Deferred item | Main | Adversary |
|---|---|---|
| Stricter settings check and defaults (1.2) | 1.2, tests both failing cases | 1.2, tests four cases |
| Partial index and ordering | 2.1, 7.1 | 2.1, 7.1 |
| `ON CONFLICT DO NOTHING RETURNING` | 4.6 | 5.4 |
| Overall-deadline wrapper | 6.3 | 4.2 |
| Reaper sets `updated_at` | 7.3 asserts it | 7.2 names it, no assertion |
| Graceful shutdown | 7.6 with a signal test | 7.4 with a signal test |
| Claim sort of RETURNING | 7.1 | 7.1 |

Main is the more granular of the two. Adversary has several tasks that bundle two or three concerns.

Main covers these and the adversary does not:

- **Index usage.** 2.2 asserts via `EXPLAIN` that the claim predicate uses the partial index.
- **Key header edges.** 4.3 tests 255 accepted and 256 rejected as separate cases.
- **Public-id exposure.** 4.7 tests that no response contains the internal integer id. Adversary only asserts the public response shape.
- **Caller-facing docs.** 5.2 documents the API, including the key-reuse code and the guidance on `failed` and `unknown`, and runs the `curl` examples.
- **Operations docs.** 7.6 documents worker settings and start command.
- **Both processes refuse an invalid template dir.** 8.2 tests API and worker startup. Adversary tests registry build failure only.
- **Phase-timeout sum and drip response.** 6.3 tests that the sum of derived phase timeouts never exceeds the deadline, that a drip-feeding server is cut off, and that connect timeout stays transient. Adversary's 4.2 only tests a slow stub and an unclassified exception.
- **Unknown replay at the system level.** 8.1 asserts provider call count stays at one after replaying an `unknown` key.
- **Strict validation.** 8.3 runs `openspec validate --strict`.

The adversary covers these and main does not:

- **Table-driven HTTP classification test (adversary 4.4).** Design's risks section promises "each adapter has a table-driven classification test". Main has no task for it. Main's 6.3 touches only connect timeout and the deadline guard. Rows for 429/5xx, 4xx, DNS failure, refused connection, read timeout, dropped-after-write, and unparseable 2xx are untested in main. This is the highest-value gap because misclassification is the central at-most-once risk.
- **Log hygiene test (adversary 7.5).** The design context states recipients and options "must not appear in logs", and the discard path logs provider message ids. Main only checks the discard log contents in 7.2. No task verifies that a provider echoing a phone number never reaches logs.
- **Status endpoint leak test with a provider-echoed recipient.** The lifecycle spec has the scenario "Last error omits sensitive values". Main's 6.1 tests the exception constructor and 5.1 only checks absence of recipient and options in a normal body. Nothing runs the full path of a rejected-with-echo provider response through to `GET`. Adversary 6.1 includes the case.
- **Transition table test (adversary 2.2).** An exhaustive (from, to) test against the spec's allowed set. Main verifies terminal immutability only incidentally (7.2) and nothing covers "pending cannot skip processing". The proposed database trigger is speculative and should not be adopted; the test is worth having.
- **Repetition for race tests.** Adversary 5.5 and 7.1 run the threaded tests repeatedly to expose flakiness. Main runs them once.
- **Real template directory in CI.** Adversary 3.5 loads the repository's real `templates/` directory in a test. Main 3.4 loads samples but 8.2 only tests a bad directory.
- **Scenario-to-test walk.** Adversary 8.3 lists every spec scenario with no test. Vague as written, but the idea is sound as a closing check.

## Divergences and judgment

1. **Docs and test placement (main stronger).** Main puts docs in the group that owns the work (3.4, 5.2, 7.6, 1.3 README note). Adversary puts the whole README in 8.2 and has no API docs at all, the exact pattern the schema forbids, and it omits caller guidance that the proposal calls out. Keep main's placement.
2. **Task granularity (main stronger).** Main splits intake into seven tasks (4.1 to 4.7) with one verify each. Adversary's 5.4 bundles ordering, insert, response shape, and 12 scenarios into one task, which is not completable in one session or cleanly checkable. Keep main's split.
3. **Verification quality (mixed).** Main's verification clauses name concrete scenarios, and are better than adversary's on index, key edges, and deadline. Adversary's are better on classification, log hygiene, and flake resistance. Merge.
4. **Group 2 content (main stronger on the index test, adversary on transitions).** Take main's `EXPLAIN` check and add the adversary's transition table test, but as a plain test over a transitions helper only. Do not add a database trigger; the compare-and-set updates in the worker already enforce transitions.
5. **Status endpoint placement (even).** Adversary's separate group 6 is slightly cleaner but main's grouping with the API docs works. No change.
6. **Final group (main stronger).** Main's group 8 is integration only. Adversary's 8.2 is docs, which breaks the rule. Keep main's group 8 and add 8.3-style scenario coverage walk as a check inside it.
7. **Bad adversary choices not to adopt.** Database trigger for terminal immutability; a controllable clock helper that edits row timestamps in 1.3 (main's 7.3 already controls `locked_at` directly); looping a test 50 times as a task requirement (use it as a one-off verification step, not a permanent task).

## Merge recommendation

Per task block, keep main as the base and make these edits:

- **Group 1:** no change.
- **Group 2:** extend 2.2 with a table-driven test of every (from, to) pair against the allowed transitions in the lifecycle spec, using a small transitions helper. No trigger.
- **Group 3:** extend 3.4 with a test that loads the repository's real `templates/` directory, so a bad committed template fails CI.
- **Group 4:** no structural change. Add to 4.6 that the threaded tests run repeated (for example 20 iterations) to detect flakes.
- **Group 5:** extend 5.1 with a case where a failed notification's provider error contained the recipient number and the `GET` body, including `last_error`, does not contain it.
- **Group 6:** add a new task 6.4, a table-driven classification test through httpx mock transports (429 and 5xx, connect timeout, DNS failure, refused connection => transient; non-429 4xx => permanent; read timeout, dropped after write, unparseable 2xx => unknown). This implements the design risk mitigation.
- **Group 7:** add a task, or extend 7.2, so that logs captured during a run with a provider that echoes a phone number contain neither it nor any option value, while including UUID and provider message id on discards. Do not add to 7.3, already complete.
- **Group 8:** extend 8.3 to also walk every spec scenario and list any with no test, and keep `--strict`.

No blocking cross-cutting item. The main version is archivable without these, but the missing classification test (6.4) is the one item that should not be waived, because the at-most-once guarantee depends on it and the design commits to it.
