# Task 1 fixture and D1 audit evidence

Baseline: `192a4489cc1a199259b4b10c591b0ee7b23420e7` in the existing isolated worktree. No production, model, browser, or external service was used. Every PostgreSQL test used a disposable local process.

## Omitted suites and RED results

The four audit-omitted suites were:

| Suite | Exit | Result | Raw log |
| --- | ---: | --- | --- |
| `test_conversation_attachment_binding.py` | 1 | 7 failed, 21 passed | `red-conversation-attachment-binding.log` |
| `test_agent_brain_conversation_summary.py` | 1 | 1 failed, 4 passed | `red-agent-brain-conversation-summary.log` |
| `test_hr_direct_worker_progress.py` | 1 | 11 errors | `red-hr-direct-worker-progress.log` |
| `test_hr_knowledge_context.py` | 1 | 1 failed, 1 error | `red-hr-knowledge-context.log` |

`test_hr_direct_worker.py` owns the fixtures imported by progress, which are re-exported again by knowledge context. It was therefore also reproduced: exit 1, 4 failed, 4 passed, 22 errors (`red-hr-direct-worker.log`). The fixture was an old partial draft chain and omitted deployed `hr_web/094_hr_role_tools_v6.sql`, including `hr_input_context` and `record_turn_scope_v6`.

After switching to the existing deployed-schema helper, the summary test exposed a real contract mismatch: an HR direct conversation is intentionally current-turn-only under D1 and should not enter the shared summary phase. `contract-red-agent-brain-conversation-summary.log` records that result (exit 1, 1 failed, 4 passed). The test was kept as a direct-agent summary test using a synthetic non-HR capability. The first synthetic identifier lacked a matching capability card; `contract-red-agent-brain-conversation-summary-capability.log` records that second RED (exit 1, 1 failed, 4 passed). Supplying the synthetic card fixed only test setup and retained the summary phase assertions.

An unrelated concurrent integration trial briefly encountered an incomplete Task 2 migration with a stray `CREATE OR REPLACE preserves...` line. It was not a baseline fixture run, no fixture conclusion relies on it, and the Task 2 owner corrected the file before the retained GREEN runs.

## GREEN results

| Suite | Exit | Result | Raw log |
| --- | ---: | --- | --- |
| `test_conversation_attachment_binding.py` | 0 | 28 passed, 10 warnings | `green-conversation-attachment-binding.log` |
| `test_agent_brain_conversation_summary.py` | 0 | 5 passed | `green-agent-brain-conversation-summary.log` |
| `test_hr_direct_worker.py` | 0 | 30 passed | `green-hr-direct-worker.log` |
| `test_hr_direct_worker_progress.py` | 0 | 11 passed | `green-hr-direct-worker-progress.log` |
| `test_hr_knowledge_context.py` | 0 | 2 passed | `green-hr-knowledge-context.log` |
| `test_agent_brain_hr_history_isolation.py` | 0 | 17 passed | `green-agent-brain-hr-history-isolation.log` |

The D1 addition persists two old HR turns with `hr_input_context IS NULL`, including A's user fact and assistant analysis. It verifies that B receives only B's current input before and after a shared mixed summary is stored. A temporary module copy narrows both history and summary guards from `is_hr_agent` to `is_hr_v6`; the same assertions fail for that mutant. The shared product source is never rewritten.

Exact commands are in `commands.tsv`; all listed RED/contract RED commands exited 1 and all GREEN commands exited 0. Source hashes are in `source-fingerprints.sha256`.

Transitive fixture consumers for final integration: `test_execution_readiness_v5.py`, `test_hr_cutover_dispatch_review.py`, `test_hr_direct_command_binding.py`, `test_hr_direct_material_transport.py`, `test_hr_knowledge_http.py`, and `test_hr_web_reliable_loop.py`, plus the files already listed above.
