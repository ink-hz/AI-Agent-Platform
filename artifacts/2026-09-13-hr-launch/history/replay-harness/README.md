# Historical-intent replay harness

Scope: frozen v2 H01/H03/H06/H13 only, newly synthetic text materials. The corpus is not rewritten. `recipe-corrections.json` records root-authorized deviations from the frozen execution recipe: same-work `/inputs` continuation, real UUID idempotency, actual pending question/revision, and no forced save on H01 receiving turns. The corpus remains a separately supplied artifact dependency, not part of this child source commit.

## Execution contract

The first request reads `/api/hr/agent/configuration` for its actual budget-profile ID and posts `/works` with `thread_id=null`. Later turns post `/works/{id}/inputs` with the preceding work's actual `expected_input_revision` and pending question UUID when waiting_user. Same work/thread, cumulative material/method refs and immediate previous saved result UUID/revision/SHA are retained. Missing required H03 results fail honestly; no assistant output is inserted as a fake saved result. Runtime tool dispatch is the only path that saves or revises results.

H01 turns1/2 may receive material without saving; only its final turn requires JD/JR. The current mechanical gate requires stored `jd` and `requirements` result kinds for H01 final and every H03 turn; later H03 result IDs must persist with different revision UUIDs. A real model that returns one combined document will retain that document in evidence but fail this stricter kind gate for root review. H06/H13 require a saved result. These gates do not judge professional answer quality.

H01 turns2/3, H03 turns1/4 and H06/H13 turn1 upload the exact `context_fixture` bytes using actual attachment initialization/content/completion HTTP endpoints. Actual AttachmentProcessor validation, text derivative and owner-scoped material lookup return exact material refs. Other turn-specific text is explicitly inline synthetic context. No reviewer `context-v1.txt` aggregate package is sent. `manifest.assets` version/SHA is verified before use; source URLs are rejected for this four-case scope. Material/upload and work API statuses are retained in per-turn `http_trace`.

Identity uses real DingTalkWebAuth/WebSessionRepository issuance and authentication, current persisted directory users and HR grants, AgentUseAuthorization, AuthorizationRepository/AuthorizationService and middleware CSRF/Origin/idempotency. The negative owner uses a second real authorized synthetic user. No work/result/model success rows are fabricated by SQL. The local directory/role/grant setup is synthetic fixture setup, not production identity synchronization. Only external DingTalk login exchange and object storage are substituted; default ScriptModel is separately explicit below. HR candidate intake/personal-processing authorizer is not opened.

The worker runner uses actual run_worker/run_work and real lease heartbeat. Each case has its own disposable database and work/knowledge directories; nothing is shared with D experiments. Per-turn messages/results/model attempts/tool receipts are read back through owner HTTP and owned repository persistence. Each turn is written before execution and again on completion/failure; completed prior turns survive later failures. Case failure returns a durable failure report and the real runner continues to the remaining cases within the same aggregate call cap. No automatic budget increase/retry run is added.

## Engineering versus real model

Default tests use ScriptModel only. Scripted read/save/revise calls exercise the actual pipeline; their text is explicitly engineering-only and is not a gold answer or professional-quality pass.

The explicit local long-conversation experiment uses 32 calls /1,200,000 conservative estimated tokens /1,800 active seconds per work; reserve2/32768/30. This is **not** verification of the production default32/600000/900 (production reserve2/32768/60). The first same-work600k attempt stopped in `waiting_budget` on H03 turn3 after17 charged calls/556048 estimated charged tokens: the next call could not fit reserved headroom. That failure is preserved in `same-work-green-attempt-1` and `scripted-same-work-1`. The fixture's initial24-call limit there was also raised explicitly to32 for the long-conversation experiment. No production profile was changed. If the explicit experiment budget is insufficient, the harness fails; it never raises it automatically or invokes budget extension.

A single BoundedPort is shared across the four real cases: maximum96 actual `stream` entries,300 seconds per call,16384 output tokens. Transport retries consume that shared counter. ConfiguredHttpModelPort is built only by the opt-in test after validating an independent absolute0600 profile and a fresh absolute evidence directory. The profile must identify the approved `claude-opus-5` / `anthropic_messages_sse` boundary. Only local in-memory timeout/budget overrides are made; the private profile is never rewritten. The effective configuration revision is recomputed from the actual provider/budget/diagnostic mappings so persisted work metadata does not retain the base fixture's obsolete identity.

Root must review before invoking the real-model command. This child did **not** invoke it:

```sh
HR_HISTORY_REAL_MODEL=1 \
HR_HISTORY_REAL_PROFILE_FILE=/absolute/private/independent-history-profile.json \
HR_HISTORY_EVIDENCE_DIR=/absolute/new/history-real-run \
backend/.venv/bin/python -m pytest \
  backend/tests/test_hr_agent_history_replay.py::test_opt_in_real_history_replay -q --tb=short
```

Use a new nonexistent evidence directory. Configuration evidence includes the profile file SHA, selected protocol/model and explicit limits, not credentials or profile contents. Each case records source HEAD, actual HR module hashes before/after, runner/builder hashes, corpus/manifest hash and actual knowledge metadata before/after. Production/default600k compatibility, real-provider quality, browser behavior and release certification are not claimed.

## Evidence history

Each command directory contains exact argv/cwd/exit/timestamps and owned source bytes with SHA-256. `record.py` does not snapshot private runtime directories. Case evidence contains only newly synthetic request/output material and sanitized profile identity.

- `red`:4 missing-harness failures.
- `green-attempt-1`:2 failed/6 passed/1 skipped; the engineering save fixture had invalid empty basis and therefore created no results. This correctly failed H01/H03 next-turn exact-result binding; those original per-case receipts were copied before temporary cleanup. This was not a successful replay.
- `green-attempt-2`:8 passed/1 skipped for the superseded new-work recipe; preserve as intermediate evidence only.
- `same-work-recipe-red`:1 failed/8 deselected, proves the new same-work HTTP expectations reject the old recipe.
- `same-work-green-attempt-1`:1 failed/7 passed/1 skipped; genuine600k engineering budget exhaustion described above.
- `same-work-green-attempt-2`:8 passed/1 skipped with explicit local long-work budget.
- `final-engineering-green`:11 passed/1 skipped; adds real waiting_user question conflict/replay and per-call cap denial.
- `effective-config-red`:1 expected failure/12 deselected; proves the initial fixture revision did not describe effective overrides.
- `final-engineering-green-2`: final result is read from its actual `output.log`/`command.json`; final receipt below records that result after completion.

No historical or intermediate evidence is replaced by a later green run. All skipped real-model tests are explicit opt-in skips, not passed model acceptance.

Final receipt: `final-engineering-green-2` = **12 passed,1 skipped in71.50s**; `final-lint` exits0, all checks passed. `final-receipt.json` records final case counts, work IDs, input revisions, actual result kinds, model-attempt counts and knowledge/configuration identities. No HR source module changed during any final scripted case.
