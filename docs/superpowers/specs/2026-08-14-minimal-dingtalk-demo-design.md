# Minimal DingTalk Demo Deployment Design

Date: 2026-08-14
Status: approved direction; implementation requires final review of this written spec

## Goal

Publish a real DingTalk QR-login demonstration of Agent Platform quickly, without treating the demonstration as the production identity cutover.

Success means one to three explicitly approved Orbbec DingTalk members can open the isolated preview URL, scan with DingTalk, receive a server-side Platform Session, view the Agent directory and existing permitted demonstration data, sign out, and sign in again. Existing FAE and the current password-protected Platform root must continue working unchanged.

## Options considered

1. **Isolated DingTalk preview with an explicit member allowlist — selected.** Demonstrates the intended login experience while limiting identity and data exposure.
2. Existing Basic Auth only. Fastest, but it does not demonstrate DingTalk identity and was rejected for this demo.
3. Full enterprise DingTalk release. Includes directory events, department authorization, operational recovery and formal cutover, but is not required for the immediate demo.

## Published surface

- Demo URL: `https://agent.orbbec.com.cn/_preview/dingtalk-r1/`
- Existing `https://agent.orbbec.com.cn/` behavior remains protected by the current Basic Auth configuration.
- Existing `https://fae.orbbec.com.cn/` and IP-based FAE access remain unchanged.
- Preview uses a separate preview control database, preview Cookie path and preview cryptographic keys.
- The preview location is public only as required for the DingTalk callback; every non-public route remains protected by application authentication.
- No root-domain cutover, production database migration, or production member authorization occurs in this release.

## Identity scope

- QR login only. In-client login, department grants and all-employee access are excluded.
- Only one to three explicitly supplied stable DingTalk `userid` values may be bootstrapped.
- Stable IDs are supplied to an offline bootstrap command through a root-owned `0600` input file. They are never committed, placed in URLs, or written to normal logs.
- The bootstrap command resolves each member through DingTalk, verifies the configured corporation and active employee state, derives versioned HMAC values, encrypts provider identifiers, and creates one complete preview directory generation.
- Names are display-only and never select or bind an identity.
- Login still uses the existing exact corporate-plus-union proof and server-side Session checks. A non-allowlisted scan receives a generic denial and creates no user or Session.

## Included product behavior

- DingTalk QR start and callback with exact `openid corpid` scope.
- Five-minute, single-use login state and PKCE.
- Secure server-side Session, CSRF protection, logout and Session rotation.
- Exact preview route allowlist, trusted-proxy handling and current rate limits.
- Standard Agent directory and read-only demonstration pages already supported by the Platform.
- A visible `Demo Preview` banner so the environment cannot be mistaken for production.
- Minimal health and smoke checks for login start, callback rejection, authenticated account and existing root/FAE invariance.

## Explicit exclusions

- Periodic full-directory reconciliation and department closure.
- DingTalk Stream events and automatic departure handling.
- Department or all-employee Agent authorization.
- Management viewer rollout, production role administration and formal break-glass use.
- New Agent adapters, attachment changes, data migration, PITR work or WAL-pressure work.
- Production root cutover and removal of Basic Auth.

Because automatic departure handling is excluded, this preview has a short expiry and is shut down after the demo. Before each demo, the bootstrap command revalidates every allowlisted member. Sessions have the existing maximum 24-hour lifetime and can be revoked by deleting the preview release.

## Deployment and isolation

1. Build an immutable image from a clean reviewed commit.
2. Create a new preview database and apply control migrations there only.
3. Mount existing secret files read-only and create a dedicated preview rate-limit keyring.
4. Bootstrap only the approved DingTalk members into the preview generation.
5. Start preview API/sidecar workers on loopback-only ports with Uvicorn proxy-header parsing disabled.
6. Add only the `/_preview/dingtalk-r1/` Nginx locations. The callback location bypasses Basic Auth but remains governed by the exact application public allowlist and rate limits.
7. Run smoke tests from the host and an external client, then perform one real QR login with an approved account and one denial with an unapproved account.

No production deployment action is taken until the preflight proves the current root and FAE responses are unchanged and a rollback bundle exists.

## Failure and rollback

- Any missing secret, stale/mismatched DingTalk corporation, bootstrap mismatch, database error or dependency failure aborts before Nginx activation.
- A failed or ambiguous login never falls back to anonymous or Basic Auth identity.
- Rollback disables the preview Nginx location and stops/removes only the preview containers. The separate preview database is retained temporarily for diagnosis, then removed through the explicit cleanup command.
- Existing root Platform, FAE containers, databases and Nginx locations are not restarted.

## Acceptance checklist

- Approved account completes QR login and `/api/v1/account` returns the expected internal identity.
- Unapproved DingTalk account is denied without a Session.
- State replay, expired state, wrong corporation and direct protected-route access are denied.
- Preview Cookies use `Secure`, `HttpOnly`, `SameSite=Lax` and preview-only `Path`.
- Public health is minimal; authenticated pages and assets load under the preview prefix.
- `https://agent.orbbec.com.cn/` still returns the pre-existing Basic Auth challenge.
- `https://fae.orbbec.com.cn/` still returns its pre-existing healthy response.
- No public ports other than the existing 80/443 listeners are added.
- Rollback command is tested before the real QR acceptance step.

## Deferred path to production

The full Release 1 plan resumes after the demo. Tasks 8–16 remain the production gate for recurring directory synchronization, departure events, authorization, preview operations, recovery rehearsal and final root cutover. Demo acceptance does not mark those tasks complete.
