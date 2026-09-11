# D1 context review fixes

Baseline: `b974a87318955f14c21a26bbcad386dc8ea93ad1`

Scope: local code and disposable PostgreSQL tests only. No production, model,
HTTP, browser, or deployment action was performed.

## Controlled exception mapping

Merge commit `aa5390619f412f284e4a19cddbb0d0b83a689685` changed the shared parent
behavior in `ConversationContextBuilder.build_direct`: both parents mapped the
generic repository/crypto/validation/PostgreSQL branch to
`ConversationContextError`, while the merge result used a bare `raise`.

`01-red-build-direct-controlled-errors.log` records the required RED result:
raw `ValueError`, `KeyError`, and `psycopg.DataError` escaped (`3 failed`). The
one-line mapping restoration is recorded green in
`02-green-build-direct-controlled-errors.log` (`3 passed`).

`05-direct-adapter-controlled-error-green.log` additionally exercises
`DirectMissionAdapter.prepare` through `DirectWorker._prepare`. A private
`KeyError` from `_load` is mapped at `build_direct` and caught as the controlled
`context_unavailable` decision (`4 passed`, including the three mapping cases).

## D1 coverage restored and strengthened

The original D1 file collected 12 cases:

- 4 candidate-switch cases: 2 builders x summary absent/present.
- 6 unbound/adversarial cases: 3 current inputs x summary absent/present.
- 1 long HR history/compaction case.
- 1 non-HR history/summary case.

The integrated baseline collected only 6 cases: 4 candidate-switch cases with
empty attachment scopes, plus 2 unbound cases. It lost attachment-level
candidate isolation, the explicit-comparison adversarial input, long HR
history/compaction coverage, and non-HR summary preservation.

The revised D1 file collects 16 cases:

- 8 candidate-switch cases: persisted v6/v7 scope x `build`/`build_direct` x
  summary absent/present. Candidate A and B each have a distinct ready
  attachment, exact persisted scope identity, and an actual per-turn
  `platform_attachments.bindings(kind='turn_input')` row. Only B's attachment
  and current text reach B's context.
- 6 unbound cases: candidate request, general position request, and adversarial
  explicit `compare A/B` text x summary absent/present. Each case checks both
  builders and `compaction_candidate`; the comparison wording does not unlock
  history. The comparison case uses a persisted v7 scope, while the other two
  use v6.
- 1 long-HR case: persisted v7 current turn, long prior user/assistant history,
  and a mixed stored summary. Both builders return only the current turn and
  compaction remains `None`.
- 1 non-HR case: recent history remains available and a stored shared summary
  continues to replace its covered exchange.

`04-d1-restored-coverage-green.log` records `16 passed`. The final combined
context regression is `06-context-targeted-regression.log`: `26 passed`.

`03-d1-restored-coverage-first-run.log` records the first attachment-fixture
failure. The scope/binding path succeeded, but an inherited candidate fixture
used intentionally undecipherable placeholder attachment metadata. The final
test uses active turn bindings without projecting message-input metadata, so it
tests persisted attachment authority without claiming attachment byte/content
acceptance.
