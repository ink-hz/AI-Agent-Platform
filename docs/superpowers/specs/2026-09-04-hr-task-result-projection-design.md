# HR Task Result Projection Design

Date: 2026-09-04
Base: `8665b4d4f5b98afd201e840a9835fb203150bf11`

## Goal

Project the real terminal assistant output of explicit HR position and candidate tasks
into position context drafts and candidate analysis versions. The projector is an
app-role background reconciler that root integration can wire into the application
lifecycle; this branch does not modify `main.py` or the web UI.

## Durable boundary

Migration 071 adds an immutable-identity projection ledger with per-row processing
leases. App-role `SECURITY DEFINER` functions claim, complete, fail, and release one
projection at a time. They use a fixed `search_path`, verify `session_user`, revoke
PUBLIC access, and grant only function execution to the matching production or
preview app role. No role receives direct DML or SELECT on the new ledger.

The claim validates a single exact chain: owner, position, explicit task request,
task record, position-bound HR conversation, turn, mission, direct `hr-bot` run and
job, and completed assistant message. It accepts only completed turns and jobs. It
does not claim failed, cancelled, interrupted, active, missing, cross-owner,
cross-position, or ambiguous results.

Each task record has one ledger identity. `FOR UPDATE SKIP LOCKED` and a bounded
lease permit multiple application instances. A crash after the business projection
but before ledger completion safely replays the existing service operation with the
same deterministic request ID. Invalid/decryption/mapping failures become terminal
ledger failures so one bad item cannot block later rows. Transient repository errors
release the lease for retry.

For the seven explicit quick-task kinds, the existing task read model treats
terminal execution as `running` until this ledger reaches `completed`. A completed
ledger row exposes `completed`; a failed row exposes `failed` with the stable public
error `result_projection_failed`. This keeps the UI polling until the projected
draft or analysis is actually readable.

## Projection mapping

Position tasks map as follows:

- `jd` -> `jd`
- `jr` -> `jr`
- `talent_profile` -> `talent_profile`
- `sourcing_strategy` -> `sourcing_strategy`
- `position_interview_plan` -> `interview_standard`

The context draft stores `{module: {"text": assistant_text}}`, with the same full
text as the draft summary. It preserves the task record's base context, official
version, source conversation, source turn, output artifact version, source material
IDs, verified `hr-bot` agent identity, and explicitly configured model version.
Oversized or empty output fails projection; it is never truncated or invented.

Candidate tasks map `candidate_match` to `match` and
`candidate_interview_plan` to `candidate_interview_plan`. The analysis result is
`{"text": assistant_text}`. Evidence, unknowns, conflicts, and verification
questions are empty because the assistant text is provenance, not fabricated
structured evidence. Document IDs are derived by an exact one-to-one,
owner-and-candidate-scoped mapping from the task record's attachment IDs. Feedback
IDs are passed through unchanged, so reruns with a new task record form a new
immutable analysis version while old versions remain untouched.

## Provenance assumption

The current execution schema persists the verified agent identity but not a model
version. The reconciler therefore requires a non-empty `model_version` constructor
argument supplied by the future composition root from an explicit deployed runtime
contract/configuration. Missing provenance prevents reconciler construction. The
module does not guess a model value. Agent identity is returned by the exact SQL
join and must equal `hr-bot`.

## Tests

Unit tests cover mapping, full-text preservation, deterministic request IDs,
idempotent replay, invalid output isolation, transient release, and loop
continuation. Real PostgreSQL tests cover successful position/candidate projection,
duplicate claim/replay, crash recovery, exact owner/position/request/turn/message
scope, nonterminal exclusion, least-privilege grants, and a bad item followed by a
valid item without head-of-line blocking.
