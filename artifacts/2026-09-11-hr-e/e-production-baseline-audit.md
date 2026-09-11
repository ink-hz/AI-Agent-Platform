# E production-baseline compatibility audit

Date: 2026-09-11
Scope: read-only Git and source audit; no production, network, credentials, database writes, or deployment.

## Baselines and conclusion

- Deployed release: `44de9209b77343facf3117fe3ab4d3450eaf2062`.
- Audited E head: `03e84954e77cc41cdf5638174f28c1a45b0ae46c`.
- Merge base: `6777d227a28c1548c6cdee480913b6f72818dde2`.
- The deployed release is **not** an ancestor of E. E and production are divergent release lines, so publishing the E tree unchanged would replace production behavior rather than advance it.
- Current production inventory evidence supplied by the release owner says migrations 094 and 095 are applied, three HR jobs have remained queued for more than 24 hours, and an HR worker was active within 15 minutes. No migration is authorized.

**Release blocker:** do not publish E unchanged. Build the release integration against the deployed commit and retain the deployed compatibility surface while adding Loop.

## Concrete regressions in the unchanged E tree

| Surface | Production implementation | E delta and impact | Classification |
|---|---|---|---|
| Old result reads | `backend/app/hr/tool_routes.py`, `backend/app/hr/tool_service.py`; `GET /api/v1/hr/conversations/{conversation_id}/results`, `GET /api/v1/hr/results/{result_id}` | Router/service are removed. New `/api/hr/agent/results` reads `platform_hr_agent.results` and is not a bridge to deployed v6/v7 results. Historical results become inaccessible. | Unsolicited lost data API |
| Company/topic intelligence | `backend/app/hr/company_intelligence.py`, `topic_catalog.py`, `topic_intelligence.py`; `/api/hr/panorama/topics`, `/companies`, company detail/jobs; corresponding `webui/src/components/hr/HrCompanyDetail.tsx`, `HrTopicWorkspace.tsx` and API/type files | Backend routes, authorization entries, clients, and pages are removed without equivalent compatibility routes. | Unsolicited lost API/UI |
| Knowledge reads | `backend/app/hr/reference_knowledge*.py`, `reference_knowledge_routes.py`; `GET /api/hr/knowledge`, `GET /api/hr/knowledge/{source_commit}/{resource_id}`; `HrKnowledgePanel*` | Old source-commit/resource reads and page are removed. Loop knowledge releases use a different contract. | Unsolicited lost data API/UI |
| Panorama reports | Production uses bundle identifiers; E authorization/routes use publication identifiers on overlapping report paths | Existing links and clients can fail or resolve a different identifier domain. | Compatibility regression requiring an alias/dual lookup |
| HR role/deliverable schema history | `backend/control_migrations/hr_web/094_hr_role_tools_v6.sql`, `095_hr_deliverable_inputs.sql` | Both migration sources are deleted although production reports them applied. This breaks release-history reconstruction and schema provenance; deleting files does not undo deployed rows. | Unsolicited migration-history loss |
| Old worker protocol | `contracts_v6.py`, `contracts_v7.py`, `frozen_command_v6.py`, `frozen_command_v7.py`, pending-worker SQL, `worker_hr_tools.py`, `worker_official_verification.py`; v6/v7 worker routes in `tool_routes.py` | Implementations and middleware recognition are removed while a production HR worker is active and queued HR work exists. Existing jobs may no longer progress or report results. | Intended execution exit applied before its prerequisites |
| Old runtime invariants | `backend/app/config.py`, `backend/app/main.py`, `role_package.py`, knowledge/role startup validation | E replaces old config and initialization. Retaining a draining old executor without these settings can leave a partially wired runtime. | Cutover regression |
| Authentication/authorization | `backend/app/control_plane/auth.py`, `authorization.py`, `middleware.py`; `webui` auth files | E removes old HR route grants and the exact `/hr/panorama` safe return while adding Loop routes. Old deep links can fail authorization or login return. | Unsolicited routing regression |

The v6/v7 mutation/execution implementation may be retired deliberately, but only after an explicit admission freeze, drain/cancel decision, and verification that no old worker or runnable work remains. Removing old read-only result, company, topic, and knowledge access is not part of execution retirement.

## Non-HR and shared-runtime exposure

The direct diff does not delete the ordinary non-HR route modules, but it changes shared composition in `backend/app/main.py`, `backend/app/config.py`, `backend/app/control_plane/auth.py`, `authorization.py`, and `middleware.py`. Execution-relay contract/core removals are also shared-runtime sensitive. These files must be merged from production with narrow Loop additions; taking E's versions wholesale is not a safe non-HR preservation strategy.

The read-only merge preview at `.superpowers/sdd/e-production-merge-preview.txt` records 18 conflicts, including `conversation_context.py`, config/auth/authorization, Web UI auth, `HrPositionIndex`, and a deleted test. `main.py` and compose auto-merge, but an automatic merge result still requires route and startup verification.

## Minimal integration strategy

1. Create the release integration from deployed `44de9209b77343facf3117fe3ab4d3450eaf2062`, or merge that commit into E while resolving every conflict in favor of production compatibility plus additive Loop behavior. Record both parents in release provenance.
2. Preserve migrations 094 and 095 byte-for-byte. Keep 096+ additions isolated and do not run migrations in this compatibility release without separate authorization and preflight approval.
3. Retain production company/topic/knowledge APIs and pages, authorization entries, and deep-link login returns. Add Loop routes alongside them.
4. Retain old result GET endpoints and their storage reader. Do not copy or remap old payloads automatically; provide dual read surfaces until a separately reviewed bridge or retention decision exists.
5. Retain v6/v7 worker callbacks and required configuration while the three queued jobs and active worker exist. Freeze new old-protocol admission separately. Remove mutation/execution hooks only in a later release after bounded inventory proves zero runnable/in-flight old HR work and the worker is stopped.
6. Preserve production non-HR shared composition and add only required Loop settings, middleware recognition, and routes. Verify other-bot execution-relay paths because shared contract/core files changed.
7. Treat bundle-to-publication resolution as a compatibility boundary: keep the existing bundle URL behavior and add the new identifier path or an explicit, ambiguity-safe alias.

## Release checks for the combined tree

- Assert the old result, knowledge, company, topic, and panorama endpoints are registered and return their normal authentication/authorization response rather than 404.
- Assert existing company/topic/knowledge and panorama deep links survive login return and render their production pages.
- Assert Loop `/api/hr/agent/*` and `/hr/agent` are additive and remain behind their intended permissions.
- Assert old worker callbacks remain callable only by their existing worker identity during drain; reject new old-protocol admissions at the intake boundary.
- Assert non-HR worker and API route inventories match production.
- Assert migration discovery retains 094/095 and sees later migrations without applying them.
- Run API-first regression against disposable data; do not infer compatibility from route names or migration counts alone.

## Limits

This audit did not execute production, inspect secrets, apply migrations, or build and test the combined integration tree. It establishes a source-level release blocker and a bounded integration target. It does not authorize old-data migration, queued-job cancellation, worker shutdown, schema changes, or deployment.
