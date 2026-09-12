# Task1 fixture / D1 independent review

Reviewer: audit_counts, author of Task2 only; did not author these fixture/D1 changes. Read-only review of /tmp/hr-e-audit-fixtures-192a448.patch, planTask1, current named sources, fixtures/commands.tsv, README and raw log outcomes. Baseline192a448. No suites rerun, no source/fixture/index edits, no production/model/browser access.

## Verdict

No blocking defect found for current-schema fixture repair and conservative HR history isolation. Two limitations must remain explicit: the renamed provenance test no longer tests applying091 to an existing turn, and the new short NULL-history test does not independently exercise compaction at the size threshold. Neither should be described as migration-backfill or full summary-threshold coverage.

## General direct summary versus HR policy

The old test expected an HR direct follow-up to use shared summary, conflicting with the existing D1 policy. Changing the conversation to fae-bot and providing a matching synthetic capability isolates the generic non-HR direct path. The copied card keeps the routing shape used by the old test; this is an injected capability, not verification of deployed FAE configuration.

The advance_pending assertions, summary phase lookup, submitted summary result and exact phases == ["summary","direct"] assertion all remain. No summary-phase assertion was removed or loosened. The old implication that HR should receive shared summary is intentionally replaced by explicit HR current-input-only tests. The two contract-red logs record policy mismatch and initially missing synthetic capability; the final5-passed log supports the repaired setup.

## Migrations and authorization

Changed imports resolve to the existing tests/helpers/hr_direct_database.py. It uses hr_agent_database(cutover_phase="legacy"), applies root/hr_web/hr_agent migrations with the real owner-role migration runner and initializes the gate. Application repositories retain platform_control_app connections; administrative fixture setup is explicit.

ConversationRepository still calls record_turn_scope_locked -> platform_hr.record_turn_scope_v6. No no-op or monkeypatch bypass is introduced. Readiness observations use the real validator. Existing attachment owner, wrong-conversation, quota, replay, lock-race and atomic rollback assertions remain unchanged.

Do not overstate "real auth": existing API tests use FakeAuth/TestClient identity contexts, and ScriptedRelay remains a relay/model substitute. The changes preserve those boundaries and actual database enforcement; they do not verify production login or signed external transport.

The NULL-history fixture creates non-HR persisted turns and administratively reclassifies the conversation as HR, then explicitly asserts both hr_input_context values are NULL. It does not bypass record_turn_scope_v6 for new HR intake. This represents a historical data shape, not historical migration execution. Existing _complete infrastructure seeds a synthetic completed mission/event and projects it; this is context-history input, not real business-execution success evidence.

## NULL guard mutation proof

The actual build and build_direct outputs must contain only B's current message and no shared summary, both before and after storing a mixed A-user/A-assistant summary. Persisted NULL scope means is_hr_v6 is false while is_hr_agent is true.

The mutant replaces exactly one two-guard source fragment, checking source.count(original)==1. A temporary module is loaded via importlib; production source/module is untouched. sys.modules registration supports module execution/dataclasses and is removed in finally. Import execution occurs outside pytest.raises, so source matching, import or loader failures cannot masquerade as mutation detection.

Only AssertionError is caught during mutant assertions, not Exception. Inspected production context/repository paths contain no assert statements; operational/context errors use other types. On these exact sources, the unsummarized mutant exposes A history and fails the message assertion; after summary storage it fails summary-is-None. This adequately distinguishes the named guard mutation. Directly asserting the mutant's exact leaked content would further reduce hypothetical unrelated AssertionError ambiguity.

The assertion helper stops at the mutant's first failing build, so it does not independently prove mutation rejection by both entrypoints. Both use the same mutated _load guard and both original entrypoints are checked. The short fixture remains below compaction threshold, so compaction_candidate(None) is not a separate threshold mutation proof.

## Removed or changed coverage

- Removed pending-SQL setup/teardown and repeated result-recovery application are replaced by the deployed migration chain. No worker/progress leasing, cancellation, rollback or success assertions were removed.
- The missing-draft case now drops origin_route_epoch explicitly to represent unavailable current schema. Repository-error and no-turn-created assertions remain; the old partial pending-installation scenario is no longer represented.
- The provenance test retains origin == ("legacy_api_v1",0), legacy claiming and worker-adoption rejection assertions, but091 is now applied before the turn exists. It therefore loses the old upgrade/backfill temporal scenario and overlaps existing route-change provenance coverage. The renamed deployed-provenance test is accurate, but it must not support migration-backfill claims. Add a separate historical migration case only if that claim is required.

## Evidence

Four omitted files were attachment binding, summary, worker progress and knowledge context; the direct-worker fixture owner was also reproduced. RED outcomes match README:7failed/21passed;1failed/4passed;11errors;1failed/1error; owner4failed/4passed/22errors. GREEN logs report attachment28passed (10warnings), summary5passed, owner30passed, progress11passed, knowledge2passed and D1 17passed. commands.tsv maps commands; README records exits. Logs were read, not regenerated.

These results support local engineering coverage only, not production deployment, real model quality or professional HR acceptance.

## Reviewed fingerprints

- backend/tests/test_agent_brain_conversation_summary.py: 4f6a8128ab4522b4978eceae2992cb2b9edf5f5bd4f86e4eaeea20c40867fcea
- backend/tests/test_agent_brain_hr_history_isolation.py: 1947ea48f7b326f8a20669390ac812e2185cfeb8657f38a1f9c46eb97a0a1820
- backend/tests/test_conversation_attachment_binding.py: 9820cb2017665fd62c3b29d396240156151746276e61a21228b5a5998914829f
- backend/tests/test_hr_direct_worker.py: 0f47282220fc4281159d4efb450343bcc43ce9b167a9d264ac277175f139757d
- backend/tests/test_hr_direct_worker_progress.py: 37464b52442541c9345ca0b771620947d45055c8d852936e95e2bbdd881ab9af
- backend/tests/test_hr_knowledge_context.py: 72af20c013901d1fd57ce91ad5f114839b5e050e71e54a3613a40260684cf4bd
- backend/tests/helpers/hr_direct_database.py: 7053e542a1fe7c1ccdd47fd5166c15cc63791da9f1caff533d0d298796e3c69a
- backend/tests/hr_agent_support.py: 9ed9458af30ef76134546adffb92fd9db6202a61a0c600642d65c9838a6b8a53
- backend/tests/test_hr_turn_scope_v6_database.py: f12979714b4a4bda816e5f9938fdd1609d1749e738a96931449ed2cf7a0d0e80
- backend/app/agent_brain/conversation_context.py: c0cc97462f2321e6aa2c3df19b202a1942db7abd802a520b7885e0a435485bf3
- backend/app/agent_brain/conversation_repository.py: eadfbee00ecc5224c5b35a63d905ff2df589486925c54947fbd528bc5c0bc395
- backend/app/hr/turn_scope.py: 1de0bdf8e55ee3078dbb3c7bfe2a1e51f1aece6dcf1648d5015b0115df9b182d
- artifacts/2026-09-12-hr-e-audit-followup/fixtures/commands.tsv: a590fa2d756c4d71ec1e0b16b1d1bcdffc5b5d1b09d6ed9f894b5b8c5c0b49ba
- /tmp/hr-e-audit-fixtures-192a448.patch: ec8cf2fa2056816d38dfd9def46db601e108bb5f1e41e5096a6c026796efb04f
