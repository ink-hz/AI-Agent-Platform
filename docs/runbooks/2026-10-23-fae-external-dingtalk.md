# FAE external DingTalk integration

## Current deployed optional trial (2026-10-23)

API runtime `d0dea5d3a048158e112b04ee731d3e2f4af881ad` is deployed and healthy,
restart0. Canonical trial entry: https://fae.orbbec.com.cn/partner/login . External start
now redirects to official oapi QR/SNS website login with new state namespace, fixed
callback and secure browser nonce. This is an **optional trial**, not completed real
external-user acceptance. One genuinely external DingTalk user must still scan and
complete callback → verified UUID → FAE-only session. FAE remains public2636f73.

## Current selected protocol and acceptance boundary (2026-10-23)

The external `dingtalk_external` adapter now deliberately selects the official historical
website SNS protocol: personal DingTalk scan → verified provider identity → stable site
UUID (`partner_operator`) → FAE-only grant and launch. Internal enterprise authentication
keeps its existing modern OAuth client and configuration. There is no SMS, email,
password login or runtime protocol fallback. The currently public FAE policy remains
as authorized by the owner (anonymous-enabled FAE change `2636f73`); this batch neither
deploys FAE nor restores mandatory FAE login.

The previous real external modern-OAuth attempt **failed** with
“自建应用不支持跨组织授权”, before a verified external callback. That result remains
failed. The newly selected SNS registration's eligibility and real external login are
**unproven/pending a real external-user scan and callback**. Local mocked HTTP tests,
302 redirects and application-secret availability do not establish this acceptance.

Official primary references, checked by the controller on 2026-10-23:

- [Website QR login](https://open.dingtalk.com/document/orgapp/scan-qr-code-to-log-on-to-third-party-websites)
  describes website login and leaves handling external users to the website.
- [SNS temporary authorization exchange](https://open.dingtalk.com/document/orgapp/obtain-the-user-information-based-on-the-sns-temporary-authorization)
  defines `sns/getuserinfo_bycode` and `user_info.unionid` / `nick`.
- [Personal-login signature](https://open.dingtalk.com/document/personalapp/signature-calculation-for-logon-free-scenarios-1)
  defines HMAC-SHA256 of the millisecond timestamp with AppSecret, Base64 and one URL encoding.

These historical docs and the current API application-type table conflict about eligible
registrations. Do not infer that any internal AppKey supports external users. The
controller's one deliberately invalid-code diagnostic returned HTTP 200 / integer
`errcode=40078` (nonexistent temporary code), with no user info or provisioning. This
proves neither valid signature/credentials nor external registration eligibility or a
successful login. No real provider call is part of this implementation batch.

## Explicit credential configuration

The external factory requires both settings, independently of enterprise configuration:

```text
PLATFORM_PARTNER_DINGTALK_CLIENT_ID=<explicit SNS registration candidate>
PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE=/run/secrets/partner-dingtalk-client-secret
```

There is no copying or fallback from `PLATFORM_DINGTALK_APP_KEY` or its secret. An
explicit external Client ID may equal the internal value; equality is neither a startup
rejection nor a qualification claim. The secret uses the existing private-file reader:
absolute regular service-owned mode-0600 file, no symlinks or group-readable files.
Inline `PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET` is rejected. Preserve the original
owner-controlled secret file when preparing an external mounted copy.

Partner encryption and lookup keyrings remain purpose-separated from each other,
authentication and content keys. The existing content keyring and control app DSN remain
required, independently of Brain or execution-relay flags. The secret preparation helper
creates/validates separate partner keys without rotating or overwriting existing files:

```sh
python3 deploy/cloud/prepare-fae-partner-secrets.py /absolute/private/secret-directory
```

Provider kind, identity enablement and the digest-pinned owner rollout authorization
remain required. The v2 owner authorization permits rollout while real-account acceptance
is pending; the old two-account activation prerequisite is historical. The controller
must first review this branch, prepare explicit external settings, integrate the exact
reviewed commit and use the transactional API release with all current Compose overlays.
No bare deployment that drops overlays, schema migration, or FAE release is needed.

## Authentication and verification

`GET /partner-auth/start` creates a `fae-dt-sns-v1.` state and a random
`__Host-fae-dt-nonce` cookie (Secure, HttpOnly, SameSite=Lax, five-minute TTL). Only keyed
digests are persisted. The authorization redirect is exactly
`https://oapi.dingtalk.com/connect/qrconnect` with `appid`, `response_type=code`,
`scope=snsapi_login`, state, and fixed
`https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback` redirect URI.

The shared callback reserves both `fae-dt-sns-v1.` and legacy `fae-dt-v1.` before checking
an enterprise cookie. The broker only accepts the new prefix. Legacy callbacks fail
closed without provider exchange or enterprise/directory fallback, even with a valid
enterprise session. The SNS callback requires exactly `state` and `code`; `authCode`
(including equal dual aliases), duplicates, unknown/error fields, malformed values and
ambiguous payloads are rejected before state consumption/provider HTTP. Missing/wrong
nonce, expired state or replay cannot exchange a provider code.

The adapter performs one POST to `https://oapi.dingtalk.com/sns/getuserinfo_bycode`:
query `accessKey`, millisecond `timestamp`, `signature`; JSON `tmp_auth_code`. Signature
is `Base64(HMAC-SHA256(key=AppSecret, data=timestamp))`, passed unescaped to HTTPX params
for exactly one URL encoding. Independent OpenSSL test vector:
`testappSecret` / `1546084445901` → `HCbG3xNE3vzhO+u7qCUL1jS5hsu2n5r2cFhnTrtyDAE=`.
There is no token/profile second hop, enterprise member lookup, retry or token storage.

Only HTTP 200, strict integer `errcode=0`, object `user_info`, and valid `unionid` / `nick`
produce a verified subject. Failures expose only `partner_identity_unavailable` to the
caller. Server telemetry is a fixed failure reason enum with integer `http_status` and
bounded integer `provider_errcode` (`-1` when unavailable/invalid), never provider body,
exception URL, code, signature, secret or PII. Shared HTTPX/HTTPCore INFO/DEBUG filters
redact the signature query, and request context also redacts secret/code/signature.
Do not enable ingress/access logging of callback query strings for a real trial.

The stable provider subject remains `sha256(registration ID) + ':' + unionid`.
Successful verification creates/reuses one random site UUID, encrypted provider identity
and nickname, FAE-only grant and strict `fae_external_authenticated` audit transaction.
Concurrent logins reuse the subject. Revoked identity/grant or suspended
subject/operator/realm fail closed; login never restores permissions or moves identities
between realms. Registration changes do not automatically merge historical subjects.
External login never issues a Platform web session or internal employee identity.

The callback memory proof remains 60 seconds and durable verified-at lease eight hours.
Expired leases require login; local revocation is still checked and FAE validation cache
remains capped at 60 seconds. Database schema, migrations and FAE consumer kind remain
unchanged. The existing additive migration 123 is already part of the earlier deployment;
this protocol selection needs no new migration.

## Local verification and next real trial

Focused tests use only mocked DingTalk transport and disposable PostgreSQL. They cover
the exact signature/encoding, strict malformed/error payloads and log redaction; callback
nonce/one-use state/legacy dispatch with valid enterprise cookie; identity provisioning,
FAE launch, stable UUID, account isolation, revocation, audit, durable lease and release
gates. They do not validate real external registration eligibility.

The Development-only standalone probe has the same SNS code/new-prefix contract,
retains preview-only DSN/private-key checks and stays stopped. No probe was started,
production called, browser opened or deployment performed by this implementation batch.

After reviewed API-only rollout, the controller will provide the canonical external
login entry for the user to complete a real scan. Acceptance requires an actual SNS
callback and verified stable partner UUID with restricted FAE launch; inspect sanitized
exchange diagnostics on failure. Record this new trial separately from the failed
modern-OAuth attempt. Page HTTP 200 or authorization HTTP 302 alone is insufficient.

## Owner trace lookup

An authenticated Platform owner with a valid CSRF token can request:

```text
POST /api/v1/manage/partners/subjects/{subject_id}/external-identity-detail
{"reason":"customer identity trace","request_id":"<new UUID>"}
```

The database verifies the active owner and app caller, writes
`fae_external_identity_read`, then returns the selected encrypted identity and nickname
for application decryption. The no-store response contains stable subject ID,
registration digest, unionId, nickname, verification timestamp and revocation timestamp.
Ordinary external callback and launch responses contain no unionId or provider secrets.

## Original acceptance plan (historical, before owner-authorized rollout)

Local tests use disposable PostgreSQL and a mocked DingTalk HTTP transport. They do not
prove the current AppKey permits external accounts. The original plan required
completing the release evidence gate with two distinct real external accounts,
stable repeated identity, account isolation, revocation and replay checks. Production
migration/deployment and real account verification are separate release work. The owner
later superseded the two-account activation prerequisite as recorded below; that
historical plan is not a new approval gate for this candidate.

## 2026-10-23 engineering review status (historical, before real external failure)

Provider, canonical launch, provisioning, and encrypted subject reference tasks have
passed their independent code reviews. Final composition review found no Critical
or Important issues. Reported focused test runs: provider234, canonical402,
provisioning904, subject reference167 (overlapping runs, not a combined count).
Two real external accounts and current AppKey eligibility remain untested; no
production evidence file or activation was created. Required lookup reason currently
selects a fixed protected audit purpose; arbitrary reason text is not retained.

## Owner-authorized production release (historical, 2026-10-23 before real external failure)

The user explicitly authorized direct rollout: “我觉得可以直接上线，然后内外部登录扫码的区别是什么”. This supersedes the earlier requirement to complete two real accounts before activation. A separate strict v2 owner authorization is pinned; v1 real-account evidence remains unchanged. V2 declares real_account_acceptance=pending and contains no invented real-account pass flags. See the owner-authorized rollout plan for the exact six-field contract.

Production API now runs72d636e3559dffbb53850fda677ae170e2c53e5d; FAE runs releaseai-fae-agent-20261023032048 / d96909283a5887980605b84933895b5b17e99db3. Identical owner-release copies use SHA2569426e1e9504004ba45a20e15362d26330997a7b76c9a773d2a8a0b69b367c486; API root:10001 mode0640, root-running FAE root:root mode0600. The archive records authorization, engineering review and pending real accounts.

Production control DB was backed up, then additive123 applied under exact production DB/baseline122 guards, advisory transaction lock and owner role; no existing role grants changed. New purpose-separated production partner keys differ from Development. The API transaction preserved all30 current Compose inputs and latest SDKf1b47ba3 runtime configuration, allowed only image/release SHA/six partner settings, unchanged mounts/command and other containers (FAE excluded as its own authorized standard release). API healthy, restart0, no rollback.

The temporary Development callback maps/start route were removed only after verifying the current file still matched the saved candidate hash. Actual full Nginx config validation and reload passed with temp owners/permissions unchanged; only the Dev probe container was stopped. Encrypted preview test data and backup are retained.

Verification: independent gate review passed163 Platform and87 FAE cases. FAE standard release passed3609 Python tests,3 existing exclusions,278 frontend tests and TypeScript/Vite; release verification hashes/containers/ingress/logs passed. Public capabilities nowexactly {"partner_login_available":true}; /partner/login302 to production /partner-auth/start; production start302 to DingTalk personalopenid only, fixed callback and secure browser nonce. Missing-nonce callback401 before Provider exchange, enterprise invalid-state callback unchanged, anonymous FAE history/review/chat401, production health200. Nginx and both MetaBot services active. No production model/eval request was sent.

Real external-account first login, repeated identity, two-account isolation and real revocation/replay acceptance remain pending; engineering/public auth smoke is not represented as real-user acceptance. Canonical workspace remains https://fae.orbbec.com.cn/ .


## Staged modern-OAuth repair review (historical; superseded by SNS selection)

Credential isolation043040d3 and modern callback compatibility0bc0f1e1 passed separate
task spec/quality gates and an independent final composition review. No Critical or
Important implementation defect was found. Controller verification on exact code
`0bc0f1e14d9b3e6d291c84543b7f97188f8ca47d` ran registration config, Provider and disposable
PostgreSQL integration:120 passed in3.15s, zero warnings/failures/skips. Earlier reported
runs overlap and must not be summed. Vendor HTTP remains mocked.

This candidate is suitable to retain/push as an isolated work-in-progress branch, not
to merge into shared master or deploy with the current missing settings. Real external
login remains failed/unresolved pending an eligible application registration, matching
protocol and credentials, then real authorization verification. No production code or
configuration changed in this repair batch; working enterprise login configuration is
preserved. User console application-type evidence is still outstanding.


## Website SNS trial release evidence

- Implementer focused666 passed (139 inherited cookie warnings); controller provider/
  integration/config/broker191 passed in4.73s with no warnings. Runs overlap. Independent
  task/quality gates passed; final security verdict permits optional engineering trial,
  while normal external-login acceptance remains pending.
- Code integrated into master and released exactd0dea5d3. Concurrent uncommitted VOC
  observability work in the original checkout was preserved and excluded from the image.
- Backend-only image inherits the verified production81d553 image and copies exactly8
  changed Python files from Gitd0dea5d3; each file hash and all66 unchanged frontend
  file hashes verified. Requirements,frontend,tools,migrations/runtimeconfig paths had
  no Git differences from81d553. ImageIDsha256:7e7dcb84698a22780f96809d652192b70a2fcbc4ac0debaf40e6782f4279c77e,
  parentIDsha256:9cf4afa1fa6a1ebd5728979f2ba89f94ac47b64b234436631fdda22d8d6c8adc;
  parent layer prefix verified. Build uses tags; transaction explicitly checks IDs before/
  after cutover and on rollback rather than claiming tag immutability.
- Controlled transaction preserved32 existing Compose inputs and appended one API overlay.
  Allowed only APIimage/releaseSHA/two explicit SNS credential settings. Command/mounts/
  other environment/service configuration unchanged; APIhealthy/restart0/no rollback.
  Existing ClientIDdinggtdockkfve0jstjl and existing private secret file were explicitly
  selected as a registration candidate; no secret value copied, no eligibility inferred.
- Production non-model checks passed: newSNS start302; missing nonce, old state namespace
  and modern authCode callback401 before provider exchange; internal login shell200 and
  existing modern enterprise login-start200; publicFAEhealth200/unchanged2636f73 and
  authentication_requiredfalse, guest invalid-chat422, Review/authenticated histories401,
  partner handoff302. English/vision settings and shared Nginx files unchanged.
- Initial end-of-check comparison to the early container snapshot detected a separate
  ai-daq-fae-backend release at10:07:48UTC, before this API cutover. Confirmed it was
  independent and already present at the transaction baseline; all other containers were
  unchanged DURING our transaction. The corrected checker records that distinction; no
  application patch or model retry was used to make the checker pass.
- Private snapshot/source manifest/image checks/373806110-byte control backup and final
  acceptance reside under `/opt/orbbec-agent-platform/private/fae-website-login-20261023T094611Z-1eb679d4/`.
  Transaction receipt is under `fae-website-d0dea5d3a048-d916629b/` in the same private root.
- No schema migration, FAE rollout, real provider code exchange or production model/eval
  occurred during deployment acceptance. The earlier one deliberately invalid-code
  diagnosis is retained separately and is not a successful credential/identity test.

Real external SNS scan and completed FAE session remain pending user confirmation. The
owner was given the canonical entry to perform this required physical-account check.
