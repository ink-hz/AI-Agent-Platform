# Development external DingTalk real-account probe

This narrow process validates personal DingTalk OAuth, stable identity mapping, and
actual preview control database automatic provisioning. It does not validate FAE
chat end to end, issue a Platform session, launch FAE, call a model, or enable
production external access. Engineering tests replace only DingTalk HTTP transport;
they do not prove current AppKey eligibility for two real external accounts.

## Explicit Development configuration

Run as the owner of the private secret files. Set `FAE_PROBE_ENVIRONMENT=development`
and every path below. Paths must be absolute, nonsymlink regular files, owned by the
process UID and mode 0600. No `PLATFORM_*` configuration or production release gate
files are read. Do not mount production database or keyring files.

| Variable | File content |
| --- | --- |
| `FAE_PROBE_DATABASE_URL_FILE` | Preview control app DSN: database `agent_platform_control_preview`, role `platform_control_app_preview` |
| `FAE_PROBE_APP_KEY_FILE` | Existing approved DingTalk AppKey |
| `FAE_PROBE_APP_SECRET_FILE` | Its AppSecret |
| `FAE_PROBE_AUTH_KEYRING_FILE` | IdentityKeyring JSON, purpose `fae-probe-auth` |
| `FAE_PROBE_PROVIDER_ENCRYPTION_KEYRING_FILE` | IdentityKeyring JSON, purpose `partner-provider-encryption` |
| `FAE_PROBE_PROVIDER_LOOKUP_KEYRING_FILE` | IdentityKeyring JSON, purpose `partner-provider-lookup-hmac`, `transition_versions: [1]` |
| `FAE_PROBE_CONTENT_KEYRING_FILE` | IdentityKeyring JSON, purpose `platform-content-encryption` |

Each keyring has `active_version: 1` and `keys: {"1": "<base64 of 32 random bytes>"}`.
Use four independently generated Development keys and retain them across restarts.
Never reuse production key material. Purpose separation and overlapping key material
are checked. DSNs require explicit host, port and password; libpq service/options
indirection and ambient `PG*` variables are rejected. Startup verifies
the connected database, role, and migration 123 function before constructing health.
Apply migration 123 through the standard preview migration transaction before startup.
An authorized owner maintenance equivalent must preserve the preview database
assertion, advisory migration lock, exact SQL/checksum ledger, and atomic commit;
do not change role memberships to work around a migrator failure.

Run the standalone factory from the backend directory, in a dedicated container or
process with only a loopback-published HTTP port, using exactly one worker:

```sh
python -m uvicorn app.control_plane.external_dingtalk_probe:create_app \
  --factory --host 0.0.0.0 --port 8089 --workers 1 \
  --no-access-log --no-proxy-headers
```

Do not enable proxy header rewriting. Limits use the actual ASGI socket peer and
ignore forwarded headers: 20 auth requests per minute per peer, at most 256 live
peer counters, and at most two concurrent callback exchanges without queuing.
The edge must also enforce limits. Never enable request URL/query or provider debug
logging. Application responses use no-store, no-referrer, and CSP with no content,
embedding, form, or base URI permission. The nonce cookie is Secure, HttpOnly,
SameSite=Lax, Path=/, with a five-minute lifetime; terminal callbacks clear it.

## Route and account checks

The only routes are `GET /health`, `GET /_dev/fae-external/start`, and
`GET /api/v1/auth/dingtalk/callback`. Health returns the static `{"ready": true}`
after factory checks; it is not continuous database or business health monitoring.
The shared public callback remains exactly:
`https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback`.

The deployment owner separately configures the edge to route **only** callback
states in namespace `fae-dt-v1.` to this Development process. Enterprise callbacks
must retain their existing destination. Do not broaden this rule or change the
provider allowlist. Remove the temporary edge routes after the test window.

For each of two distinct real external accounts, open the Development start link
in an isolated browser profile, authorize with DingTalk, and retain the short success
or stable error code. Repeat account A and correlate its existing subject with the
report below; account B must create a different subject. Verify nonce absence,
callback replay, and revocation through the existing legitimate owner operation
in Development. There is no probe revocation, owner-creation, or actor-spoofing API.
Preserve original failures instead of retrying until successful. The success page
contains no provider identifier, nickname, state, token, or subject UUID.

From the same private Development environment, print only the safe correlation view:

```sh
python -m app.control_plane.external_dingtalk_probe report
```

The JSON contains `subject_count` and `subjects` with opaque `subject_id` UUID,
`login_count` (completed authentication audit count), and `state` (`active` or
`inactive`, including local provider revocation and current FAE access). It never
decrypts identities or nicknames. Its read path uses the real PartnerService and
PartnerRepository, checks preview again, and does not write evidence or pass flags.
This view includes all external DingTalk subjects in that preview database; collect
a before/after view if the database already has other Development subjects.

Keep real-account observations and engineering results distinct. Two real-account
observations still require independent review under the existing production release
gate; this probe never generates that gate file or alters production evidence flags.

## Staging evidence, 2026-10-23

- Exact source `89173a83f6f607446245769a555f43eafce1ee8f` was independently reviewed, merged to clean tracked master and pushed to origin/master. Runtime uses that image in the dedicated `fae-external-probe-dev` container, published only at127.0.0.1:8089. Existing production Platform API remains16d8c5b8; FAE remains302d0a9.
-13 focused probe tests passed. Reviewer independently ran the earlier12, then the two corrected private-file/key-overlap tests. Merged auth/integration220 passed. No live Provider account or real model was used in these engineering tests.
- Preview database was backed up. Existing preview migrator could not SET ROLE because of pre-existing role membership; permissions were left unchanged. Authorized owner-maintenance applied exact123 SQL under preview-db assertion, baseline122 guard, advisory transaction lock, SET LOCAL ROLE platform_control_owner_preview, exact SHA ledger and commit. SQL SHA25665bb962ea3d1a13913415fd03bc01451b03a88b165b540d285ee5206685579a9. Production control database migration was not applied.
- Four distinct Development keys and a preview-only app DSN are in the dedicated read-only secret mount; approved existing DingTalk app credentials were copied inside the controlled host without printing them.
- Edge adds only exact Dev start and shared callback locations plus state/cookie maps and a rate zone. Non-external callbacks retain current upstream, complete Cookie and330s timeouts. External Dev callbacks receive only their nonce Cookie. Static protected business routes remain unchanged.
- Same host Nginx binary was tested in an isolated read-only Docker filesystem, no writable host mount; syntax passed and three request tests verified enterprise cookie preservation, Dev nonce-only cookies, and missing nonce. Controlled production validation used only the actual full configuration via nginx -t (no-c); shared temp directory owners/permissions unchanged, then reload succeeded. Candidate was independently reviewed.
- Dev health is healthy; all pre-existing running container IDs stayed identical. Public Dev start302 has onlyopenid scope, no corpid, exact registered callback and Secure/HttpOnly/SameSite=Lax nonce. Missing nonce callback401 before Provider; enterprise unknown state retains302/login?error=1. Main login200; production partner start404; FAE capability staysfalse and anonymous history401. Safe Dev report initially0 subjects.
- Public test link: https://agent.orbbec.com.cn/_dev/fae-external/start . This is Development auth verification only, not the FAE workspace. Two real external users, repeated identity, isolation, revocation/replay and release evidence remain pending. No production gate pass flags were written.

Private receipts, exact pre-change/candidate Nginx config, before123 backup and dedicated secrets are under `/opt/orbbec-agent-platform/private/fae-external-probe-dev/`. Remove only the reviewed Dev locations/maps after acceptance, comparing the latest full config against the saved baseline so unrelated later edits survive. Validate actual full config and preserve temp owners before reload. Stop only `fae-external-probe-dev`; retain encrypted evidence according to the test window. Do not replace a later shared config blindly with the backup.
