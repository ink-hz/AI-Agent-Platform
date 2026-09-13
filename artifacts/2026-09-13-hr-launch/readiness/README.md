# HR dynamic readiness receipt

Owned implementation based on merge `482a43fc34123df73c4c85a46acd63f7139e343f`. Root concurrently owns policy/config/preflight/migration105; this receipt does not claim that a source-only checkout of this child commit includes those uncommitted dependencies. Each run records the exact owned source bytes, SHA-256, restricted baseline patch, argv, cwd, timestamps and exit code. Untracked new files are retained in `sources/`, not represented by `git diff` until staged. No old evidence was rewritten.

## Release gate interface

- Owner session GET `/api/v1/manage/hr-readiness`; successful mandatory privileged-read audit precedes checking. Returns 200 iff `ready`, otherwise 503; `Cache-Control: no-store`. Missing/failed audit denies with fixed safe detail. Non-owner 403 and unauthenticated 401. Existing `/api/health` is unchanged.
- Worker `python -m app.hr_agent.worker healthcheck` prints JSON, exit 0 iff actual Worker is ready; exit 1 otherwise. Compose invokes this every 15s with timeout 8s, start period 30s and 2 retries. Worker now mounts `PLATFORM_HR_AGENT_RELEASE_POLICY_FILE=/run/hr-agent-secrets/hr-release-policy.json`; root owns the matching API overlay configuration.
- Both return `release_sha`, `configuration_sha256` (existing loaded settings revision), `runtime_identity_sha256` (loaded HR file-content hashes and provider credential identity), `knowledge_sha256`, `knowledge_release`, `phase`, `new_admission_enabled`, `checked_at`, `blockers`. Compare all five identity fields across API and Worker and against intended release/config/knowledge before release; require both `ready=true`. Require `phase=cloud` and `new_admission_enabled=true` only for active cloud admission. A healthy legacy standby legitimately returns `ready=true`, `new_admission_enabled=false`.
- Dynamic checks re-read current configuration/profile/key/credential files against loaded settings and initial file identities, reconstruct/check the current knowledge release, and run the current `check_schema_ready` floor/privileges plus a present recognized cutover singleton in a read-only transaction. Root's migration105 floor is inherited from config, not duplicated. Optional release policy is covered by the generic HR `_FILE` identity check. Diagnostics expose fixed codes, never raw dependency exceptions or configuration paths/content.
- SQL check applies 500ms lock timeout, per-statement <=1500ms and aggregate query budget 3s; production connection factory uses connect_timeout=1. These are database query/connect budgets, not a guarantee against an arbitrarily hung filesystem/kernel. No schema migration, model request, configuration reload or shared API restart is performed by readiness.
- Worker proof is an actual same-UID 0600 Unix socket plus 0600 identity file with PID/start timestamp/random nonce. The CLI validates the live process and matching report. Progress is refreshed only after main-loop claim/poll (including valid inactive-lane rejection), or a successful real active lease renewal. The socket thread never refreshes progress. Missing/dead/stale/wrong-instance/config drift/DB failure denies. Active main-work model calls remain healthy through real renewals. Long candidate/parsing operations lacking such a validated lease heartbeat can conservatively become unready; no synthetic ticker certifies them.
- A service that failed initial repository assembly remains unready even if a new probe can subsequently connect. The readiness endpoint reports that failure; it does not repair assembly by restarting the shared API. Dynamic S3/model-provider canaries and actual rollout remain root-owned release checks.

## Retained evidence

| Run | Outcome |
|---|---|
| `api-red` | Recorder root-path error before pytest; empty log preserved, no test result claimed |
| `api-red-2` | 7 failed, missing readiness implementation |
| `worker-red` | 6 failed, missing actual Worker health implementation |
| `first-green` | 13 passed |
| `long-process-green` | 2 passed |
| `heartbeat-mutation-red` | 1 expected failure after removing only successful-renewal health progress; exact mutation source retained and then restored |
| `final-focused` | 20 passed |
| `persisted-owner` | 1 fixture failure: seeded user's last-confirmed directory generation absent, real login rejected |
| `persisted-owner-2` | 1 passed after correcting directory fixture identity; production authentication unchanged |
| `final-focused-2` | 23 passed, 3 existing TestClient per-request-cookie deprecation warnings |
| `final-delta` | 3 passed after test-only `lambda: {}` → `dict` lint fix and requested Worker policy environment addition |
| `lint-before` | 2 import-order diagnostics on new tests |
| `lint-final` | 1 introduced test-only PIE807 diagnostic |
| `lint-final-2` | baseline 12/current 12 diagnostics; no introduced diagnostics, exit 0 (not a full-project green lint claim) |

API and process tests use disposable real PostgreSQL and own child processes. The persisted-owner HTTP test uses real DingTalkWebAuth session issuance, signed/hashed token validation, current database owner role, authorization middleware and SystemHealthAuditWriter persistence. Only external DingTalk exchange is replaced by a local identity callback. Earlier route-focused test uses the established FakeAuth fixture and in-memory audit spy; it is not claimed as equivalent proof. Long Worker test uses a local SSE provider substitute; it is not real external model/quality acceptance. No browser, production deployment, shared-service restart or external business call was performed by this child.
