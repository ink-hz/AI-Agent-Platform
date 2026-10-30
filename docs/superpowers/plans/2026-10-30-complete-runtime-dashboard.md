# Complete Runtime Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore truthful, complete runtime/usage/source coverage for seven registered business applications and separate independent Agent services, configured channel Agents and legacy streams.
**Architecture:** Dedicated readonly dashboard projection and one cached aggregation feed the new overview endpoint and UI. VOC consumer visibility is repaired at its owner provision boundary; native HR uses existing control DB metadata without decrypting private content.
**Tech Stack:** FastAPI/Pydantic, psycopg/PostgreSQL RLS, httpx, React/TypeScript/Vitest.

## Global Constraints

- Spec: docs/superpowers/specs/2026-10-30-complete-runtime-dashboard-design.md is binding; approved by user's “开始修复，完整的数据看板” following the concrete diagnosis/recommendation.
- Use /Users/neo/.codex/worktrees/0791/AI-Agent-Platform branch fix/runtime-dashboard-20261030 from88a37685; preserve all other work and private evidence.
- No model calls, production business writes, browser, sibling-source import, Agent execution changes, SSH/Nginx changes, broad RLS bypass or fake replies. Parent alone performs cloud operations.
- Exact current inventory: 7 business applications = 5 Agent services + SDK/manufacturing workspaces; configured directory has10 unique Agents (5independent,5channel) at baseline; historical HR is a separate stream, not anotherAgent or a claim of whole-company inventory.
- Missing source/observation is unknown/null, never fake0/healthy. Distinguish session/input/reply-record/delivery/result and explicit windows.
- Backend/API/real temporary PostgreSQL first; frontend last; preserve first failure evidence and bounded incremental verification. Do not rerun unrelated suites.

## Task 1: Repair VOC consumer visibility and source completeness

**Files:** deploy/cloud/provision-agent-sync.py; backend/app/cloud_sync/sources.py (and narrowly related adapter helpers if required); backend/tests/test_cloud_sync_deployment.py; backend/tests/test_cloud_sync_sources_postgres.py; new backend/tests/test_voc_observation_contract.py as needed; deploy/cloud/runbooks/cloud-agent-sync.md.
**Consumes:** Existing SourceProfile/DatabaseAgentSource.read_snapshot(), source_role_sql(kind,password), CloudSyncWorker/CloudSyncStore and replica usage consumers.
**Produces:** Idempotent least-privilege SELECT RLS policy for linked VOC receipts and read-time completeness check; stable SourceReadError on incomplete capability, old snapshot preserved.

- [ ] Write failing real PG test with actual inbound/jobs/outbox types, source_inbound unique FK, enabled RLS and existing actor INSERT-only policy; generated source role sees inputs but not deliveries before repair. Observe exact production-class failure, save red output.
```python
assert reader_inputs == 2
assert reader_sent == 0  # baseline defect under actual restricted role
# New contract after provisioning must be reader_sent==1; second input pending.
```
- [ ] Implement owner-scoped policy installation in source_role_sql('voc',...) within existing transaction. Preserve 4-column grants, NOINHERIT/NOBYPASSRLS, other-role RLS policies and unrelated grants; repeated install has same effective policy. No unrelated source credential rotation.
- [ ] Implement same-snapshot capability/constraint check before VOC_QUERY; missing or restrictive observation policy must produce stable SourceReadError before publish. Inspect actual catalog rather than returning fabricated source data.
- [ ] Add true producer→restricted reader→projector→encrypted store→count consumer tests: sent/pending/failed, unlinked receipt excluded, unknown delivery stays unknown, duplicate/FK rejection, visibility failure keeps previous snapshot, role cannot write/read unrelated columns, employee actor isolation unaffected.
- [ ] Run covering tests once per meaningful change and retain first red/PG logs; start fixture-owned local initdb via existing observability_database. No production SQL by implementer. Document scoped release operation and rollback of only owned policy.
- [ ] Self-review, commit scoped files, write report with exact commands/counts/versions/evidence and return for independent task review.

## Task 2: Implement authoritative dashboard API, native adapters and bounded aggregation

**Files:** new backend/app/runtime_dashboard/{__init__,models,repository,native,health,service,routes}.py (split only where useful); backend/app/main.py; backend/app/control_plane/authorization.py; new protected management catalog projection if existing use-filter hides configured inventory; targeted repository metadata helper if needed; new backend/tests/test_runtime_dashboard*.py; exact HR architecture/workflow observational notes.
**Consumes:** Task1 source completeness; replica source states and encrypted rows; control app DSN; existing VOC safe health client; SDK catalog/manufacturing bridge and account context. Source research in main checkout tmp/runtime-data-audit-20261030/*research.md is evidence, not an instruction to change external repos.
**Produces:** DashboardOverview schema_version1 at GET /api/dashboard/overview?days=7|30, a fixture JSON consumed by frontend and complete source/runtime/usage distinction defined in spec.

- [ ] Add failing HTTP/unit/temporary PG tests for independent/channel/legacy partition, HR native inputs vs old feishu, empty complete vs unavailable, unauthorized roles/no-store, exact seven inventory, time windows and native metric units.
```python
assert data['schema_version'] == 1
assert data['inventory']['applications'] == 7
assert data['inventory']['agent_services'] == 5
assert data['inventory']['configured_agents'] == 10
assert data['inventory']['channel_agents'] == 5
assert set(a['kind'] for a in data['applications']) == {'agent','workspace'}
assert all(a['id'] != 'marketing-inbound-bot' for a in data['applications'])
```
- [ ] Implement Pydantic DTOs from spec, stable identity inventory and readonly replica metadata/hash reader. Cache only numeric per-session contributions keyed by key+payload hash; decrypt changed rows only, discard content immediately; max10000, TTL30s, single-flight and removal of deleted rows on complete snapshot.
- [ ] Implement native HR metadata SQL on real current control DB: threads/work/inputs, distinct assistant-per-input (partial is not full success), work state/answer_state/results; readonly repeatable-read with5s statement timeout. No sealed/body/object fields or HR key access; old hr-bot/metabot remains legacy only; configuredMarketing channels are not implicitly stopped.
- [ ] Provide explicit trusted per-application health probes/callbacks, individual checked_at/coverage and source failure isolation; no SSH polling or broad startup restoration. Native ready version only when reported; remove static dates from authoritative dashboard. Integrate SDK immutable catalog and manufacturing existing readonly identity-bound summary as workspace metrics, do not fabricate conversation counts or bypass access.
- [ ] Add owner/admin-only complete configured Agent metadata catalog (GET /api/dashboard/catalog) if existing use-filter cannot satisfy full inventory; return original safe card metadata plus can_use computed through existing authorization, never grant use or include secrets. Ordinary members retain their original filtered catalog.
- [ ] Build one generated_at/windows snapshot; current totals/trend/last24h/ranking derive same contributions and units. Isolate source errors/stale values; no legacy brief healthy assertion. Preserve old fleet/operations endpoints for their other consumers, homepage uses new API only.
- [ ] Wire real route classification and explicit owner/admin authorization; no member/viewer global read, no secrets/HR content/owner IDs. Application-specific business summary retains original actor/project boundaries; per-user cache key if needed.
- [ ] Run real PG tests and actual FastAPI identity/middleware contracts, source failure/time boundary/cache read count/concurrent single-flight tests. Export valid fixture backend/tests/fixtures/runtime_dashboard_v1.json and API schema/type evidence for frontend; report any genuinely unsupported metric as null with reason, never false completeness.
- [ ] Measure representative first/second snapshot elapsed and decrypt/query counts; self-review, commit, report exact interface and test evidence for independent review.

## Task 3: Supply safe host-loopback runtime observations

**Files:** new deploy/cloud/collect-runtime-observations.py; new deploy/cloud/systemd/orbbec-platform-runtime-observer.service and .timer; tests/backend collector/reader coverage (reuse Task2 health.py HostObservationReader);; scoped runbook.
**Consumes:** Task2 RuntimeEvidence and configured host observations path /run/platform-runtime/snapshot.json; actual HR/admin only listen on host127.0.0.1:8012/8011.
**Produces:** Per-service safe current health observations without exposing host services, credentials or Docker socket.

- [ ] Add failing tests with local real HTTP producer and atomic JSON consumer: HR ready true/release_sha, admin statusok, timeout/refused/HTTPerror/invalidJSON, source expiry/future timestamp, missing/corrupt file, one source fail while other healthy. Full health bodies/PII/unknown keys never appear in output.
- [ ] Implement standard-library bounded collector with fixed app/loopback target contract, no redirects, short total deadline, no retries, sanitized stable errors; stdout/status contain only metadata. Write same-directory temporary file, chmod0644, fsync and atomic replace; reject unsafe output symlink/directory. No business/model/SSH/Docker/secret reads.
- [ ] Add dedicated systemd DynamicUser oneshot/timer every30s, StateDirectory=orbbec-platform-runtime, output /var/lib/orbbec-platform-runtime/snapshot.json, hardening and runtime limit; do not modify existing HR/admin services or listen/firewall. Test unit/collector invocation contract and local process failure/next cycle explicit behavior.
- [ ] Verify Task2 readonly app reader with90s per-itemTTL, independent timestamps/state and true reported version only. Keep directFAE/DAQ/VOC probes separate, no global timestamp substitution or fallback topublic404. No writes from platform process.
- [ ] Test collector→file→DTO pipeline, self-review/commit/report for independent review. Parent alone performs any production setup. Report real local process vs fixtures and any unexecuted production checks.

## Task 4: Render complete factual dashboard

**Files:** webui/src/pages/OverviewPage.tsx; new webui/src/runtimeDashboard{Types,Api,Ui}.tsx/.ts and tests as appropriate; webui/src/platform/WebCatalog.tsx; webui/src/pages/AgentUseDirectoryPage.tsx; relevant CSS; update affected existing overview/directory tests without deleting unrelated coverage.
**Consumes:** Task2 exact DTO and JSON fixture. No old fetchFleetOverview/fetchOperationsBrief in new homepage.
**Produces:** Seven-application dashboard with explicit current/history partitions, coverage and unknown semantics; 7/30day selector and consistent trend/24hour summaries.

- [ ] Add failing component/HTTP-adapter tests using Task2 fixture: seven items distinct kinds, no historical counts in current totals, HR native39 separate old369, VOC inputs shown even delivery unavailable, missing health/data unknown not0/healthy.
```tsx
expect(screen.getByText('数据覆盖')).toBeTruthy();
expect(screen.queryByText('当前关键运行检查均正常。')).toBeNull();
expect(screen.getByText('MetaBot / 飞书 Agent')).toBeTruthy();
expect(screen.getByText('历史来源')).toBeTruthy();
```
- [ ] Implement UI with coverage header, current7d/30d use, same-source last24h and trend/ranking, per-application health/data/source time/version evidence and unit-labelled usage/business metrics. History collapsible and explicitly excluded; workspace metrics not conversations. Do not expose HR private summaries, restore old assistant, duplicate runtime menu or undertake unrelated navigation redesign.
- [ ] Honor newest directory requirement: show complete authorized metadata inventory split Web vs MetaBot/Feishu, actual counts from de-duplicated cards; management complete catalog differs from use permission, open links only can_use+validWebEntry, channel cards have no online launcher. Provider preserves full catalog and filtered apps; sidebar/panorama remain filtered. HR Web projection wins over oldadapter_kind, no duplicateHR row. Add tests for owner/member/change identity/revoke/errors/all channel counts and no unauthorized use links.
- [ ] Wire abortable polling with error/stale labels and no rapid retries, maintain account identity boundaries and unmount cleanup; use accessible native controls and existing style tokens. Respect per-item access before emitting links.
- [ ] Run only affected Vitest groups and npm run build; preserve failures and mock boundaries, browser user-owned. Self-review and commit, write report for independent task review.

## Task 5: Integrate, verify and publish accurate release

**Files:** HR总体架构设计.md; HR_Agent工作流.md; new docs/operations/2026-10-30-complete-runtime-dashboard.md; source runbook; private evidence/scripts under main tmp only.
**Consumes:** All reviewed commits, exact deployment source/compose/security input identities, real PG and frontend evidence.
**Produces:** Reviewed mainline change and concrete deployable artifact; publish within user's existing release/fix authorization only after evidence passes, explicit accurate version and boundaries.

- [ ] Run focused composed API/source/PG/UI regression once for integrated changes, inspect complete diff and independent whole-branch architecture/quality review, fix all Critical/Important findings with covering tests. Do not repeat passing unrelated navigation/Agent/model suites.
- [ ] Fresh fetch, preserve concurrent commits/untracked material; merge and push normally, candidate from exact merged code. No reset/force push/whole-directory replacement or sibling work cleanup.
- [ ] Parent prepares reviewed scoped API/static build from actual live immutable image with full existing Compose chain and old assets retained, plus narrowly owned VOC policy change and the single explicitly reviewed readonly runtime-observation bind and exact new FAE health URL only when absent. If health targets/config require new values, validate actual reachability/identity first and record changed inputs explicitly; never weaken runtime policy to deploy.
- [ ] Extend scoped release control only if necessary with an exact single-readonly-bind plus absent-only canonical FAE health URL addition contract and meaningful reject/rollback tests; preserve every other parity/HostConfig check. Parent prepares the dedicated observer service/file first, verifies ownership/permissions and fresh metadata, records new units separately from existing services.
- [ ] Produce preapply source/policy/snapshot/backup/other-service evidence, independent release review, one controlled apply/owned policy operation. Unknown result/failure stops and is inspected, no silent retry/rollback. No production business/model calls.
- [ ] Readonly production check exact bytes/runtime version, new dashboard through real available authorization, native HR39 baseline vs current source metadata, VOC linked delivery distinct counts before/after, seven inventory with per-source coverage, no history contamination, source failure semantics where safe. Without a real authenticated employee session, report authorized HTTPNE separately and verify actual runtime reader with aggregate-only evidence, not fake cookies or DB success writes.
- [ ] Record passed/failed/NE by API/PG/component/production/browser; retain private hash archive including first failures and stale candidates. User owns final browser acceptance. Mainline integration and actual deployed identity separately recorded; active Compose input roots retained.
