# Brain Task 4 Report: Encrypted Mission repository and strict protocol

## Status

Complete. The Agent Brain now has a strict, non-repairing decision parser and
an owner-scoped PostgreSQL Mission repository using the existing purpose-bound
`ContentCodec`.

## Protocol boundary

- `BrainDecision` accepts exactly `direct|delegate` with forbidden extra keys
  and mutually exclusive direct/delegate field shapes.
- `parse_brain_decision()` accepts exactly one unfenced, `json`-fenced, or
  plain-fenced JSON object and rejects prefixes, trailing content, multiple
  objects, duplicate members, unauthorized Agent IDs, malformed shapes, and
  output over 64 KiB.
- Malformed JSON, Pydantic errors, Unicode errors, and adversarial nesting all
  collapse to `BrainProtocolError("brain protocol invalid")`. No repair or
  fallback direct answer is attempted.

## Repository boundary

- Mission, message, task, run, and event IDs are generated with `uuid4()`
  inside repository methods. The client request UUID is used only as an
  owner-scoped idempotency key and never as read or authorization authority.
- Message, task objective, run input/output, and event payload ciphertext use
  AES-GCM subjects containing the Mission ID, row ID, and field purpose.
- Detail, list, event replay, phase recovery, and state-transition reads place
  `owner_internal_user_id` in the SQL predicate and lock the owned Mission
  before any decryption. Cross-owner access returns the same not-found error as
  a missing Mission and never invokes the codec on foreign ciphertext.
- Event sequence allocation is serialized by `SELECT ... FOR UPDATE` on the
  owner-scoped Mission and calculated in the same transaction as the insert.
- `create_run()` and `complete_run()` persist their timeline event in the same
  transaction. Invalid event inserts roll back the run/task/Mission transition.
- Phase creation is idempotent: a retry decrypts and compares the stored Agent,
  input, objective, and initial event, then returns the original server run ID;
  collisions fail closed. Optional expected Mission status/row-version inputs
  provide owner-locked compare-and-set transitions for Task 5.
- Terminal Missions reject all new work. Events tied to terminal runs are also
  rejected while an active parent Mission may continue.
- Key, tag, serialization, nesting, malformed ciphertext, and PostgreSQL
  failures collapse to stable non-secret repository errors.
- No management cross-user read or audit API was added.

## TDD evidence

Initial RED command:

```text
cd backend
.venv/bin/pytest tests/test_agent_brain_protocol.py tests/test_agent_brain_repository.py -q
```

Initial RED result: collection failed because `app.agent_brain.protocol` and
`app.agent_brain.repository` did not exist.

Independent review regressions were each observed RED before their fixes:

- adversarial JSON nesting escaped as `RecursionError`;
- a terminal run accepted a late `agent.progress` event;
- nested repository payload serialization escaped its stable error boundary;
- duplicate JSON members were accepted;
- phase retries duplicated planning runs or conflicted on professional runs;
- Mission status/row-version CAS arguments were absent.

Fresh focused GREEN result:

```text
44 passed in 1.14s
```

Fresh full backend verification:

```text
1968 passed, 1 skipped, 47 warnings in 100.69s
```

The warnings are existing Starlette/httpx cookie and TestClient deprecations.

## Independent review

The first review found four Important issues and one Minor issue: protocol and
repository recursion escapes, terminal-run event mutation, non-idempotent phase
creation/CAS readiness, and duplicate JSON members. All were reproduced and
fixed with focused regressions. Re-review found no remaining Critical or
Important findings and judged the repository ready for Task 5.

## Concerns and next boundary

Task 5 still owns the internal `FOR UPDATE SKIP LOCKED` scan/claim of pending
Missions and orchestration advancement. It can recover committed phase run IDs
through deterministic `create_run()` replay and use the expected status/version
arguments for compare-and-set. This Task did not add cross-user management
reads, orchestration, API routes, or migration changes.
