# FAE DingTalk website personal login implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Implement the selected DingTalk website personal-login protocol and reuse verified-site-ID/FAE-only account mapping.

**Architecture:** One explicit SNS external adapter, separate from internal enterprise OAuth, with a new state namespace. Preserve existing generic identity, persistence and consumer contracts.

**Tech Stack:** Python/httpx/HMAC-SHA256, FastAPI, existing Postgres partner identity and launch services.

## Global Constraints

- No SMS/email/password authentication, no automatic protocol fallback/retry, no fabricated identity or real-user success.
- Internal enterprise OAuth and in-client login remain unchanged.
- New external state prefixfae-dt-sns-v1.; legacy fae-dt-v1. remains reserved and rejected externally before provider exchange, never falls into enterprise cookie branch.
- External uses only signed personal code exchange; never corpId, enterprise userId/membership or directory API.
- External credentials require explicit separate settings; equality to internal AppKey does not prove or disprove SNS eligibility. No implicit credential fallback.
- Preserve nonce, one-use state, TTL, verification lease, revocation, encrypted stable subject mapping, FAE-only grant and no Platform session.
- No signatures, codes, credentials or personal data in HTTP/debug logs or error bodies. Only allowlisted numeric/enum failure diagnostics.
- FAE public access remains enabled under separate owner authorization; no FAE runtime/model changes or production evals/browser automation.

## Task 1: Signed SNS external adapter and callback composition

**Files:**
- Modify: `backend/app/control_plane/dingtalk_external_provider.py`
- Modify: `backend/app/control_plane/partner_provider.py`, `routes_auth.py`, `routes_partner.py`
- Modify: `backend/app/main.py` (remove only equality rejection; explicit profile still mandatory)
- Modify if needed: `backend/app/control_plane/dingtalk.py` for shared log-query redaction
- Modify: inactive Development `backend/app/control_plane/external_dingtalk_probe.py` callback contract
- Modify corresponding external provider, integration, registration config and Dev probe tests; relevant auth tests referencing old state namespace
- Modify: `docs/runbooks/2026-10-23-fae-external-dingtalk.md` with current protocol and honest acceptance state

**Interfaces:**
- `DingTalkExternalIdentityProvider` kinddingtalk_external keeps begin_auth/finish_auth/check_subject signature and lease behavior.
- Export `EXTERNAL_DINGTALK_STATE_PREFIX="fae-dt-sns-v1."` and a reserved-prefix tuple containing new and legacy prefixes for routes_auth dispatch.
- Config external fields remain partner_dingtalk_client_id / partner_dingtalk_client_secret_file.

- [x] RED: new/revised authorization test expects HTTPS oapi.dingtalk.com/connect/qrconnect with appid,response_type=code,scope=snsapi_login,state,redirect_uri only. Existing modern adapter must fail before implementation. Preserve failure evidence.
- [x] Implement SNS provider using one signed POST, no user-token/profile API. Timestamp from validated injectable clock in milliseconds, HMAC-SHA256 timestamp with secret key, Base64, httpx params encodes once. Body tmp_auth_code. Reference official signature doc; independent OpenSSL test vector uses timestamp1546084445901/keytestappSecret (controller prints expected literal before dispatch).

```python
signature = base64.b64encode(hmac.new(secret.encode(), timestamp.encode(), hashlib.sha256).digest()).decode()
# POST params accessKey/client_id, timestamp, signature; json tmp_auth_code
```

- [x] Validate strict integererrcode0 and nested unionid/nick; preserve redacted failure behavior, no retries, no token retention, and existing identity proof lease/cache. Add sanitized numeric/enum error diagnostics if needed for real trial; never raw body or exception URL. Ensure HTTPX debug/info logs redact signature query; existing DingTalk shared logging filter can be extended/reused, with caplog tests.
- [x] New namespace in broker; routes_auth must dispatch both new and legacy reserved external prefixes before enterprise cookie handling. Broker only accepts new prefix. External route requirescode, rejectsauthCode/duplicates/conflicts/error ambiguities before provider exchange. All state/nonce constraints remain; verify legacy callbacks return401 even with a valid enterprise cookie and make no provider/directory call.
- [x] Factory still rejects missing explicit credentials and uses private secret reader. Replace same-client rejection regression with explicit same-client SNS profile accepted; still prove enterprise-only config is rejected. No dynamic vendor eligibility inference at startup.
- [x] Adapt dormant Dev probe parser to new prefix and code protocol; keep it Development-only and stopped. Update mocked transport, callback, count/signature and public-only profile tests without claiming live eligibility.
- [x] Update full disposable Postgres callback→personal identity→stable siteUUID→FAE-only grant→launch flow to one signed HTTP exchange; re-login same identity stable, cross-account/revoke/replay/no Platform session and no legacy enterprise fallback retained.
- [x] Run focused tests for external provider, integration, registration config, Dev probe, auth API, broker and owner release gate. Find exact files withrg. Use `/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python`, PYTHONPATH=backend. Disposable PG and mocked provider only; no production calls.
- [x] Update runbook with selected protocol, independent credential profile, unresolved app eligibility and pending real external trial. Self-review, commit and write /private/tmp/fae-website-login-implementation-report.md with exact RED/GREEN, command/counts, SHA and limitations.

## Controller integration/release

- [x] Independent task/final security review before master integration and rollout.
- [x] Prepare explicit external credential settings from owner-controlled config only; preserve original private file, all other settings and role/mount/keyring boundaries. Record chosen registration and unresolved real eligibility. No new registration is silently invented.
- [x] Merge reviewed branch into clean master; build exact mergedmasterSHA. Publish API with current resolved Compose overlays preserved, shared locks, image/config parity guards, health and rollback. Do not use bare deploy.sh that drops overlays. No schema migration or FAE release.
- [x] Check internal auth composition, public FAE still accessible and English/vision unchanged, external startSNS/new namespace/fixed callback/secure nonce, invalid/legacy callbacks rejected, signed exchange diagnostics and other container/Nginx state preserved. No production model call.
- [x] Provide canonical external login entry for one real external-user scan; pending until user completes browser/client step. Retain truthful pending status and failed historical modern flow. No claim complete before real callback and FAE-only mapping is observed.


## Real-user acceptance remains pending

The engineering changes, master integration and optional API-only trial rollout are done:
exactd0dea5d3, healthy/restart0, no rollback. Source/API/config/static checks passed;
FAEpublic2636f73 and existing enterprise login preserved. The completed “provide entry”
step means the user received https://fae.orbbec.com.cn/partner/login . It does not mean
an external account has authenticated successfully.

- [ ] A genuinely external user completes SNS scan/callback.
- [ ] Observe verified stable siteUUID and FAE-only session for that real user.

No first-user success, current registration eligibility or normal rollout acceptance is
claimed without that evidence. Historical modern-OAuth external failure remains failed.
