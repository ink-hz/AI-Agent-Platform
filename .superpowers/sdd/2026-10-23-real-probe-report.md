# Development external DingTalk probe engineering report — 2026-10-23

Scope: standalone Development personal OAuth + preview control database automatic
provisioning. Base implementation began at `0472d5d4`. No production action, FAE
launch, Platform session, model request, fabricated owner, or gate evidence created.

Implemented `backend/app/control_plane/external_dingtalk_probe.py` using actual
PartnerRepository, PartnerService, PartnerProviderIdentityCodec, ContentCodec,
DingTalkExternalIdentityProvider, and PartnerAuthenticationBroker. The factory
requires explicit Development settings, private purpose keyrings with distinct
material, explicit preview app DSN, no ambient libpq variables, connected database
and role checks, and migration 123. Runtime exposes only health/start/callback.
Callbacks validate duplicate/conflicting/malformed query parameters before exchange,
use browser nonce and real persistent login attempts, clear nonce on terminal
callbacks, and return safe Chinese standalone success HTML or stable error codes.
All responses have no-store/no-referrer/CSP embedding restrictions. Auth traffic
has bounded per-socket-peer counters and two concurrent exchange slots; serve one
worker with access logs and proxy header rewriting disabled.

Safe report command prints only subject count and opaque subject UUID, completed
login count, and active/inactive local state. It never decrypts provider identities
or nicknames, modifies grants, or generates production release acceptance flags.

## Verification

Test command:

```sh
PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python \
  -m pytest -q backend/tests/test_external_dingtalk_probe.py
```

Result: **12 passed, 0 failed, 0 skipped in 2.18s**. Disposable local PostgreSQL
fixture creates isolated preview and production-named test databases. Tests exercise
the real preview repository/service/identity crypto/provider/broker; only DingTalk
HTTP transport is mocked. Two mocked identities map to distinct subjects; repeated
identity reuses its subject; real PostgreSQL rejects replay; malformed callbacks and
missing nonce do not exchange provider codes. Configuration rejection includes
production DSN, wrong environment, public secret file, shared key material, DSN
indirection and ambient libpq configuration. Also verified nonce attributes/clearing,
response privacy, security headers, source-based rate limiting, provider shutdown,
safe report and revoked identity state. Module compilation and `git diff --check`
passed. TDD observed missing factory failure before implementation and specific
active-state / ambient-libpq regression failures before their corrections.

Real DingTalk accounts: **not executed by this coding task**. Deployed Development
process/edge routing: **not executed by this coding task**. Independent composition
review and production acceptance: **pending**. Current AppKey external eligibility,
two real accounts, and FAE chat end to end are not claimed as verified.

## Independent review correction

Review found the combined public-secret/shared-key test used a DSN without an
explicit port, so it failed at DSN validation before either intended check. Split
it into two tests with explicit `:5432`, each proving `_build_service()` accepts
the unchanged baseline first. The public-secret test additionally proves the
private reader accepts the original secret before changing its mode; factory
AppSecret reading precedes the database connectivity check. The key-overlap test
calls `_build_service()` directly after injecting overlapping material, excluding
database connectivity errors from its assertion. No runtime change was needed.
The corrected complete focused suite passed **13 tests, 0 failed, 0 skipped in
2.48s**. The earlier 12-test result included that false positive and is superseded
by this corrected verification.
