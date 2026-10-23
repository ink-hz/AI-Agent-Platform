# FAE external DingTalk integration

The external adapter uses the existing modern `openid` OAuth protocol with a dedicated
external registration. Its factory requires `PLATFORM_PARTNER_DINGTALK_CLIENT_ID` and
`PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE`; it rejects the enterprise Client ID
and never falls back to enterprise credentials. The only launch destination is
`https://fae.orbbec.com.cn/`; external users receive no Platform web session.

## Current failure and candidate boundary (2026-10-23, after rollout)

A real external user received DingTalk's “自建应用不支持跨组织授权”. The released
factory reused the enterprise internal AppKey/AppSecret for external OAuth. The failure
is at the channel/identity registration boundary, before a successful external callback;
mock OAuth and public redirect checks did not establish cross-organization eligibility.
The real external login result is **failed**, not pending acceptance or passed.

The [official web-login tutorial](https://open.dingtalk.com/document/orgapp/tutorial-obtaining-user-personal-information)
explicitly states that enterprise internal applications do not support cross-organization
authorization. Its [official document body](https://icms-document.oss-cn-beijing.aliyuncs.com/zh-CN/dingtalk/orgapp/topics/tutorial-obtaining-user-personal-information.html)
was checked on 2026-10-23. The [application-type guide](https://open.dingtalk.com/document/orgapp/introduction-to-application-types)
is the reference for confirming which registration is available. A distinct Client ID
alone does not prove external eligibility. The available registration and its matching
protocol remain unconfirmed; historical SNS AppId/AppSecret credentials must not be
assumed compatible with this modern OAuth adapter. There is no protocol fallback.

This candidate separates credential configuration and accepts the documented matching
callback alias described below. It must remain on its isolated branch, without
integration into shared `master` or deployment, until eligible external
credentials and their matching protocol are confirmed. Current production has the
external provider enabled without these new settings; deploying this fail-startup
candidate would stop the shared API and disrupt internal login. The owner's direct
rollout authorization remains valid; this is an unresolved configuration/protocol
dependency, not a renewed approval requirement.

The later read-only production check observed API commit
`81d553b17752940d60be5c06b682093eb4088f85` healthy, five external login attempts that
day and zero verified external identities. No subject migration is needed or attempted.
Registration-specific subject namespaces must be preserved. Earlier deployment and
smoke evidence below is historical and does not establish real external login success.

## Prepared configuration

Migration `123_fae_external_dingtalk.sql` is additive to the existing control schema.
It adds browser-bound five-minute login attempts and app-only provisioning/detail
functions. Automatic grants have `grant_origin=external_oauth` and no fabricated owner.
The existing owner mutation and audit dispatcher contracts remain unchanged.

Run the helper as the secret-file owner, against the private directory already mounted
at `/run/secrets`:

```sh
python3 deploy/cloud/prepare-fae-partner-secrets.py /absolute/private/secret-directory
```

The helper creates separate 256-bit partner encryption and lookup keys, writes mode
0600 files, validates existing files, and never rotates or overwrites them. It prints
only a status marker. The existing content encryption keyring and control app DSN are
also required. No Brain or execution-relay flag is required for partner identity.

The prepared Compose file references:

- `PLATFORM_PARTNER_ENCRYPTION_KEYRING_FILE=/run/secrets/partner-encryption-keyring`
- `PLATFORM_PARTNER_HMAC_KEYRING_FILE=/run/secrets/partner-hmac-keyring`
- Existing `PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE` and control DSN.

Before this candidate can be integrated or deployed, supply the independent external
settings through deployment configuration (the existing Compose inputs do not yet
provide them):

```text
PLATFORM_PARTNER_DINGTALK_CLIENT_ID=<confirmed eligible external Client ID>
PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET_FILE=/run/secrets/partner-dingtalk-client-secret
```

Both are required by the external factory. The Client ID must differ from
`PLATFORM_DINGTALK_APP_KEY`. The secret must be an absolute, regular, service-owned
private file (prepare it with mode 0600), never a symlink or group-readable file.
`PLATFORM_PARTNER_DINGTALK_CLIENT_SECRET` is forbidden as an inline secret. Existing
enterprise authentication continues to use `PLATFORM_DINGTALK_APP_KEY` and
`PLATFORM_DINGTALK_APP_SECRET_FILE` independently.

Provider kind `dingtalk_external`, `PLATFORM_PARTNER_IDENTITY_ENABLED=1`, and the
digest-pinned release authorization remain required. Production was initially disabled
in the committed configuration and was subsequently enabled by the owner-authorized
rollout recorded below. Generating partner keys or passing local tests establishes no
real-account release evidence.

## Authentication and verification

`GET /partner-auth/start` creates state prefixed `fae-dt-v1.` plus a random
`__Host-fae-dt-nonce` cookie (Secure, HttpOnly, SameSite=Lax, five-minute TTL). Only keyed
digests are stored. The existing shared callback
`/api/v1/auth/dingtalk/callback` dispatches this namespace before the Platform cookie
branch. It requires `authCode`; duplicate parameters or `code` ambiguity fail closed.
A nonce mismatch cannot exchange the provider code. Callback failures never enter the
enterprise login path.

The official modern web-login tutorial linked above returns both `authCode` and `code`
with the same value. The external callback accepts an optional `code` only when it is
strictly equal to the mandatory validated `authCode`. Code-only input, conflicting
aliases, duplicate parameters, malformed values and provider errors remain rejected.
This is the existing modern OAuth contract, with no SNS adapter or protocol fallback.

The previous unconditional rejection of `code` was a distinct confirmed callback
contract mismatch. It occurs after authorization and does not explain the real
“自建应用不支持跨组织授权” error on DingTalk's authorization page. Repairing this
callback mismatch does not establish external application eligibility or a successful
real external login.

Local matching-contract regression: provider, disposable PostgreSQL integration and
enterprise authentication API suites passed 308 tests, including both callback variants
with and without the alias; the 15 registration/configuration tests also passed.
The full HTTP flow first rejects a conflicting pair with 401 and no provider HTTP,
then uses the same state successfully, exchanges the FAE launch code without issuing a
Platform session, and rejects callback replay with 401. DingTalk HTTP is mocked in these
tests; no real authorization, production call or deployment was performed.

Successful OAuth creates or reuses a stable partner subject inside one transaction,
including encrypted unionId and nickname, FAE grant and `fae_external_authenticated`
system audit. Concurrent logins reuse the subject. A revoked identity or grant,
suspended subject/operator/realm, or identity assigned to another realm is rejected.
No login restores permissions. The provider stores no access or refresh token.

Fresh callbacks have a 60-second proof; `verified_at` in the control database provides
an eight-hour verification lease across process restarts. Expired leases require a
new login. Local suspension/revocation checks remain independent of that lease and
FAE validation caching remains bounded by the existing 60-second cap.

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
