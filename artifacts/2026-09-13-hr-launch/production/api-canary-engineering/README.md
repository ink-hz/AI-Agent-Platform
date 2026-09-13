# Public/synthetic production API canary — review before execution

`../api_canary.py` is an operator-run HTTP client, not a deployment or restart tool. It has **not been run against production**. No browser, DingTalk login exchange, session minting, SSH, Docker, global work enumeration, cleanup, cancellation, real employee/candidate material or external search is in this script.

The parent approved API-only launch acceptance. The script supplies a fixed, explicitly fictional organization/job with a per-run UUID marker. It uploads that UTF-8 text through the actual attachment API, resolves the processed exact material reference, requests a saved `research` result, then submits an explicit revision request through the same work's `/inputs`. A successful canary requires real saved result refs, the same result IDs with different immutable revision UUIDs, the next input revision, and idempotent replay responses. This is a narrow synthetic product canary, not full candidate authorization or professional quality certification.

## Credentials and invocation

Use the backend virtualenv (httpx is already a dependency). Supply an **existing** authenticated owner session in an independent absolute, regular, non-symlink 0600 JSON file. Never commit that file or paste its contents into logs. All five fields are required:

```json
{
  "owner_id": "<actual internal owner UUID>",
  "session_cookie": "<existing __Host-platform_session token VALUE, without cookie name>",
  "csrf": "<matching existing session CSRF token>",
  "public_origin": "https://<actual public hostname>",
  "api_base_url": "https://<same actual public hostname>"
}
```

Both URLs must be identical HTTPS origins; do not append `/api` or a path. The script verifies TLS, ignores environment proxy settings, and refuses redirects. It sends the real session cookie, Origin, CSRF and generated UUID idempotency keys. `/api/v1/account` must report the configured owner UUID, current `platform_owner` role and `hard_stale_read_only=false`. Only that whitelist is recorded; raw account JSON (including its CSRF token and personal fields) is never persisted.

Run only after operator review and credential availability, using absolute paths:

```sh
backend/.venv/bin/python artifacts/2026-09-13-hr-launch/production/api_canary.py run --private-config /absolute/private/canary.json --evidence-dir /absolute/private/new-canary-run
```

`run` requires a new evidence directory. The directory is 0700 and the atomic ledger is 0600. The credential file is not copied or hashed into evidence. The ledger contains owner UUID, synthetic content/results, authenticated API limits, deployment/configuration/runtime/knowledge identities, operation UUIDs, HTTP statuses and request IDs. Keep it private even though its supplied scenario is synthetic.

Audited `/api/v1/manage/hr-readiness` must be ready in the `cloud` phase with new admission enabled. The configuration endpoint must expose positive integer limits no larger than **32 calls / 600000 total tokens / 900 active seconds**. A profile ID alone is rejected. This is authenticated API evidence of work limits, not independent proof of provider timeout, output-token settings or actual consumption. The two turns share one work and its budget; there is no budget extension. The ledger's overall client deadline is 900 wall-clock seconds and is preserved on resume. HTTP requests are bounded by 20 seconds or the remaining deadline; polling sleeps at most 2 seconds. There are no unbounded retries.

## Response loss and failure

Every mutation's UUID, payload/hash and sending state are written before sending. The **upload initialization endpoint currently does not implement server idempotency**: the UUID header is a tracing marker only. A lost/invalid response leaves `outcome_unknown`, even if the server accepted the upload. Resume does not create another attachment or scan other resources to guess the missing UUID. Stop and reconcile using operator-side evidence. Known upload/content/completion receipts are reused; uncertain attachment operations also stop conservatively.

Work and input endpoints implement idempotency. Resume sends their original UUID/body, permitting recovery of an accepted request without creating another work or input. Completed runs on resume only verify/read their saved work. Identity and deployment/configuration/knowledge changes block resume before mutations. A server-side work can outlive a stopped client; timeout is **not cancellation**. Its server budget still applies. Failure evidence retains only this run's known IDs; any later operator cancellation or cleanup must independently restrict itself to those IDs. The script performs neither automatically.

```sh
backend/.venv/bin/python artifacts/2026-09-13-hr-launch/production/api_canary.py resume --private-config /absolute/private/canary.json --evidence-dir /absolute/private/existing-canary-run
```

## External Worker restart observation

Add `--pause-at-running` to run/resume to stop after observing the first work actually `running`. The script saves the running work, checkpoint/budget, messages/results and identities, returns `paused_for_external_restart`, and exits. The operator can then perform a separately authorized restart of the owned HR Worker and continue the same ledger. If the work completes before a running observation, the ledger records `restart_window_missed` and continues the product canary; that is not recovery evidence.

```sh
backend/.venv/bin/python artifacts/2026-09-13-hr-launch/production/api_canary.py observe --private-config /absolute/private/canary.json --evidence-dir /absolute/private/existing-canary-run --label after_external_restart
```

`observe` performs GETs only against the own ledger's work and returned result refs, after account/readiness/configuration validation. Labels are `manual`, `before_external_restart`, or `after_external_restart`. The original overall deadline still applies. `running_window_observed` reports an API observation only; no script field claims a restart passed. Actual PID/start identity, restart command and worker recovery logs must be attached separately by the operator. No shared API restart is requested or performed.

## Engineering evidence

- `red-1`: two failing tests because the script was absent; exact test bytes preserved.
- `red-2`: real local auth/PG fixture reaches the missing `run` method; 2 failed / 2 passed, exact source bytes.
- `boundaries-1`: 8 passed covering genuine local upload/work acceptance followed by injected response loss, same-work persistence, running pause/missed window, and missing budgets/deadline.
- `red-3-work-replay`: explicit initial-work replay assertion exposed only one submit; fixed by actually issuing the same UUID/body again.
- `green-final-1`: authoritative final command/output, exact script/test bytes, hashes and per-test HTTP ledgers. Only this receipt's final source hash is the final tested implementation.

Local integration uses real DingTalkWebAuth/WebSessionRepository issuance and verification, real current authorization/AgentUseAuthorization, owner audit, CSRF/Origin middleware, disposable PostgreSQL, attachment processor, exact refs and actual claimed HR runtime/result writes. Only the external login exchange, local object store, model provider (`ScriptModel`) and explicitly named transport-loss injection are substituted. The metadata-only missing-budget unit test uses MockTransport and is not presented as real HTTP/PG evidence. Production acceptance, real-model quality and actual process restart are **not tested here**. TestClient timeout deprecation warnings are disclosed in the log; production httpx requests do carry the timeout.
