# FAE Partner Provider probe and disabled-release runbook

Status: foundation only. Production partner login remains disabled until a later provider-specific design,
implementation, two-operator acceptance and approved release. The Reference Provider is development-only and
can never satisfy this runbook.

## Safety boundary

- Run the real-account probe only in Dev with two distinct partner operator accounts.
- Never place credentials, raw provider subjects, callback codes, state values, names or tokens in the evidence
  document, shell history, repository, logs or report.
- Do not change FAE public chat, Platform enterprise launch, `/office/*` or `/admin/*` while collecting evidence.
- Do not restart unrelated Agents. The rollout and rollback affect only Partner start/callback routing and
  Partner bindings.
- Before any change, record container ID, image ID, StartedAt, RestartCount and configuration hashes for Platform,
  FAE and AI ADMIN; record HTTP status and a content hash for public FAE, enterprise FAE launch, `/office/` and
  Platform `/admin/`.

## Dev provider probe

The candidate Provider must prove all of the following with real accounts:

1. A stable subject is returned for the same operator across repeated login and is not based on a shared name,
   phone number or browser field.
2. Two distinct operators produce two distinct stable subjects.
3. Active/revoked status is available, or local revocation removes access within the documented bound.
4. Shared password authentication is forbidden.
5. OAuth state binding and callback replay are rejected server-side.

Archive the sanitized probe evidence outside the repository, calculate its lowercase SHA-256, and record only
that digest in the release document. Two real operator accounts are not a production rollout: they are input to
provider selection and to the later provider-specific plan.

## Create the evidence release

Keep the archived source as a root-owned `0600` regular file outside the release tree. For the non-root Platform
API container, install a separate read-only service copy such as
`/opt/orbbec-agent-platform/private/platform-api/fae-partner-provider.release.json`, owned by
`root:10001` with mode `0640`, and bind-mount it read-only at the absolute path configured inside the container.
The service copy must not be a symlink and must be byte-for-byte identical to the archived source. This avoids
granting the API process write access while still making the evidence readable by its configured UID/GID
`10001:10001`. Its exact closed shape is defined by
`deploy/cloud/fae-partner-provider.release.schema.json`; all five verification flags must be `true`, the Provider
kind must be a registered non-Reference Provider, and `dev_real_account_tested_at` must be RFC3339 and no older
than 180 days.

Calculate the release-file digest without printing its contents:

```bash
sha256sum /opt/orbbec-agent-platform/private/fae-partner-provider.release.json
```

Only a later provider-specific release may set these production variables:

```text
PLATFORM_PARTNER_IDENTITY_ENABLED=1
PLATFORM_PARTNER_PROVIDER_KIND=<registered-non-reference-kind>
PLATFORM_PARTNER_PROVIDER_RELEASE_FILE=<absolute-read-only-service-copy>
PLATFORM_PARTNER_PROVIDER_RELEASE_SHA256=<lowercase-release-file-sha-256>
```

This foundation release keeps all four values disabled/empty in `deploy/cloud/compose.yaml`.

The provider-specific release must give FAE the same `PLATFORM_PARTNER_PROVIDER_KIND`, the same release-file
digest, and a byte-identical read-only service copy at FAE's explicit container path. FAE rejects a release whose
Provider kind differs from its configured kind; Platform remains the authority that verifies the kind is actually
registered for production. FAE must also have `PLATFORM_IDENTITY_ENABLED=true`; valid evidence without the
authenticated identity/session bottom remains unavailable with reason `partner_identity_required`. Never enable
the FAE login control independently of the Platform gate.

## Read-only validation

Inside the candidate Platform API container, run only the read-only gate:

```bash
python -m app.control_plane.partner_release gate
```

Before a provider-specific release, the exact acceptable output is:

```text
PARTNER_PROVIDER_CONFIG_VALID=true
PARTNER_LOGIN_EXPECTED=false
PARTNER_PROVIDER_KIND=none
PARTNER_RELEASE_REASON=partner_identity_disabled
```

An enabled gate with missing, malformed, stale, insecure, wrong-owner, over-permissive, digest-mismatched,
unregistered or Reference Provider evidence is a release failure. It must not be converted into a preparing page
or a partially available login route.

## Foundation acceptance

Run the normal Platform acceptance transaction. Its evidence must contain these exact lines:

```text
PARTNER_PROVIDER_CONFIG_VALID=true
PARTNER_LOGIN_EXPECTED=false
PUBLIC_FAE_CHAT_UNCHANGED=true
ENTERPRISE_FAE_LAUNCH_UNCHANGED=true
OFFICE_ROUTE_UNCHANGED=true
PLATFORM_ADMIN_ROUTE_UNCHANGED=true
```

Also confirm `https://fae.orbbec.com.cn/identity/capabilities` returns exactly
`{"partner_login_available":false}` and that the FAE page contains no Partner login control. Production safety
forbids sending evaluation questions to public FAE merely to prove invariance; use the pre/post container,
configuration and public-page hashes plus the existing enterprise launch smoke instead.

## Rollback

Rollback disables Partner start and callback routes, clears the four Partner release environment variables, and
revokes Partner Bindings. Revoke Partner Bindings through the owner-only Platform operation so the action is
audited. Do not delete partner organizations, operators, binding requests, audit rows or evidence metadata, and
do not restart unrelated Agents.

After rollback, rerun the read-only gate and all six invariant checks above. Public FAE, enterprise FAE launch,
`/office/*`, Platform `/admin/*`, FAE data and existing enterprise Sessions must remain available. If any invariant
fails, restore the exact pre-change Platform/FAE releases and Nginx configuration from the recorded backup; do
not improvise a second identity path.

## Provider-selection handoff

Real two-operator production acceptance is intentionally deferred. The next provider-specific design needs only:
candidate Provider name, stable per-operator subject, authorization flow, active/revoked status, two test
operators, application type and permissions, callback allowlist, and token lifetime/refresh behavior.
