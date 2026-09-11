# E deploy final review

Reviewed scope: E1c `6577898`, review fixes `c3dfdbf`, parent review comments, current public 102 interfaces, and inventory hardening `bbd4068`. This is a local code/document review only. No network, SSH, private configuration, production database, migration, container lifecycle, model call, or browser acceptance was performed.

## Prior deploy review closure

1. **Protected budget metadata: closed.** Preflight no longer renders the unconstrained `budget.id`; it reports only the validated profile file fingerprint. `test_budget_metadata_is_never_rendered_even_when_it_contains_secrets` injects an object with endpoint/credential sentinels and verifies neither is emitted.
2. **Migrator uid: closed.** The runbook now follows the existing bootstrap ownership model: migrator containers run as root to read root-owned mode-0600 host credentials. This documents an executable shape but does not authorize a migration.
3. **102 permission completeness: closed.** The database preflight reads catalog privileges through the app connection and reports four derived facts: app gate SELECT, app gate write, app admin-function EXECUTE, and maintenance admin-function EXECUTE. Complete means app has SELECT only, app has no INSERT/UPDATE/DELETE or any of the three function EXECUTEs, and the mapped maintenance role has all three. A disposable PostgreSQL regression revokes maintenance transition EXECUTE and observes `cutover_permissions_incomplete`.
4. **Drain continuation: closed.** Both directions now stop only new root admission. The owning lane continues claiming and recovering accepted/queued work plus derived parse/candidate work until terminal/cancel evidence.
5. **Preflight execution context: closed.** The runbook runs the shipped module from the immutable application image with the actual `/run/hr-agent-secrets`, `/run/secrets`, `/data/hr-knowledge`, and `/data/hr-work` paths mounted. Public-only checks use `--network none`; explicit database reads use only the internal network and read-only app DSN.
6. **Heredoc mechanics: closed.** The initialization example uses `docker run -i`, so Python receives stdin.
7. **Legacy-gate canary ordering: closed.** Production preactivation performs authenticated read-only configuration/knowledge reads and observes the Worker idle. It does not create cloud work while the 102 phase is `legacy`. A full public production canary is permitted only after an authorized transition to `cloud`; the runbook does not invent an unsafe preview topology.

The runbook names only existing authenticated HR reads: `GET /api/hr/agent/configuration` and `GET /api/hr/agent/knowledge?kind=method`. It explicitly states that no independent HR health endpoint exists.

## Independent cross-review of inventory `bbd4068`

The bounded registry remains aggregate-only: query projections contain counts and allowlisted state buckets; unknown database enum values collapse into one `unknown` bucket. Reference joins output only allowlisted resolution classes, use owner-aware joins, and do not select object identities. The exact legacy execution identity remains only `agent_id='hr-bot'`; other agent IDs are aggregated as `other` rather than guessed as HR.

The runtime opens `BEGIN READ ONLY`, verifies `transaction_read_only`, applies timeouts, isolates fixed queries with savepoints, and rolls back/closes in `finally`. Relation/column/SELECT prechecks distinguish missing, unreadable, and query failure; RLS visibility is reported as `scope_limited` instead of being called complete. The CLI rejects unsafe DSN/output paths and collapses caught errors to `inventory_failed` without exception text.

Fresh isolated PostgreSQL execution of `tests/test_hr_agent_inventory.py` returned 11 passed. The focused inventory Ruff command currently reports import-order/unused-import findings in the inventory-owned files; this is a quality cleanup item for that owner and does not change the read-only/privacy conclusion. No inventory output was used to claim zero in-flight work, complete P2 coverage, or production readiness. The inventory runbook itself lists substantial uncounted legacy assets, so its output is only an input to an authorized, owner-preserving handover plan.

## Remaining gates

- No production/private configuration or database was checked. The parent has only planned a bounded aggregate SELECT after inventory review; this review does not authorize or report it as executed.
- Release window, operational owner, HR channel/Feishu disposition, personal-material provider/privacy decision, and D7 product approval remain unknown.
- Full-candidate launch remains blocked because production assembly has no personal-processing authorizer. Public-only scope avoids conflating that policy with public configuration readiness but does not authorize personal data.
- Browser acceptance, real-model professional review, production canary, migration receipts, runtime container identities, and actual drain counts remain unverified.
- Concurrent data-agent metadata/column-permission work and parent Dockerfile/rollback changes visible after `bbd4068` were not independently approved here; they require their own final diff/tests.

VERDICT: the previously reported E1c deploy issues are closed in the owned CLI/runbook scope after the final two mechanics edits. Overall HR production cutover remains blocked on the explicit gates above and on integrated review of the other E changes.
