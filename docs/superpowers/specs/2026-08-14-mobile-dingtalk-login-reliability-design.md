# Mobile DingTalk Login Reliability Design

**Date:** 2026-08-14

## Problem

Production evidence shows that mobile access is not reliably using the
DingTalk in-client login path:

- recent production login attempts contain successful `qr` attempts but no
  `in_client` attempts;
- the frontend checks `window.dd.requestAuthCode` only during its initial
  render, so a bridge that becomes available later is treated as unavailable;
- the access log contains a successful OAuth callback followed immediately by
  a second callback rejected as an already-consumed login attempt, leaving the
  mobile browser on a raw error response.

The API, directory, control database, and cloud replica remained healthy while
the failure occurred. The defect is confined to login entry and callback user
experience.

## Scope

This change will:

1. bundle the official `dingtalk-jsapi` package into the existing first-party
   frontend asset;
2. use the bundled API for DingTalk in-client authorization instead of relying
   on the timing of a global `window.dd` injection;
3. start in-client login automatically once per login-page load when the
   DingTalk API reports that it is available, while preserving a manual retry;
4. preserve QR login for ordinary browsers;
5. treat a duplicate callback carrying an already-valid Platform Session as a
   successful navigation to `/account`;
6. redirect other browser callback failures to `/login?error=1` instead of
   exposing a JSON error page.

This change will not alter DingTalk identity mapping, directory membership,
roles, authorization, Session lifetimes, CSRF, replica data, or FAE.

## Alternatives Considered

### Wait for `window.dd`

Polling or listening for a late global bridge would be a smaller source change,
but remains coupled to undocumented injection timing and gives inconsistent
results across DingTalk clients.

### Keep QR login and only change the callback error page

This improves the symptom but leaves mobile users on the browser-oriented OAuth
flow and does not deliver the required in-client passwordless entry.

### Selected: bundle the official SDK and harden callback recovery

Bundling keeps runtime scripts first-party under the existing self-only CSP,
provides a stable API surface, and removes the redirect flow from the normal
mobile path. Callback recovery remains necessary for browser QR retries.

## Data Flow

1. The unauthenticated login shell loads only hashed first-party assets.
2. The frontend asks the bundled DingTalk SDK whether it is running in a
   supported DingTalk client.
3. In a supported client, the frontend fetches public `client_id` and `corp_id`,
   requests a one-time authorization code, and POSTs that code to the existing
   `/api/v1/auth/dingtalk/in-client/exchange` endpoint.
4. The backend resolves the code, issues the existing secure Platform Cookies,
   and the frontend navigates to `/account`.
5. Outside DingTalk, the existing QR flow is unchanged.
6. If a browser revisits the OAuth callback after a Session was already issued,
   the backend validates the existing opaque Session Cookie and redirects to
   `/account`. Otherwise a failed callback redirects to `/login?error=1`.

No provider identifier, authorization code, AppSecret, or user token is stored
in browser storage or returned to application pages.

## Security

- The SDK is pinned in `package-lock.json` and bundled at build time; no remote
  JavaScript origin is added to CSP.
- Existing `HttpOnly`, `Secure`, `SameSite=Lax`, host-only Cookies remain
  unchanged.
- Callback recovery requires a server-validated Platform Session and does not
  make OAuth state reusable.
- Callback failures remain generic and non-cacheable.
- The in-client exchange continues through the existing origin, rate-limit,
  directory freshness, identity resolution, and Session issuance boundaries.

## Error Handling

- Unsupported or unavailable DingTalk JSAPI leaves the QR action available.
- An in-client failure stops automatic retry for that page load and exposes a
  manual retry plus the QR fallback.
- An authenticated duplicate callback recovers to `/account`.
- An unauthenticated, expired, malformed, or replayed callback returns to the
  login page with the existing generic error presentation.

## Verification

Tests must prove:

- the frontend uses the bundled SDK and does not require `window.dd`;
- in-client login exchanges a one-time code without browser storage;
- automatic login runs at most once per page load and leaves manual fallbacks;
- valid duplicate callbacks redirect to `/account` without reusing OAuth state;
- invalid callbacks redirect to `/login?error=1`, remain non-cacheable, and do
  not reflect state or code;
- browser QR login still works;
- the complete frontend and backend test suites pass;
- the production image contains only first-party login assets and passes the
  existing DingTalk production acceptance checks.

Production acceptance additionally requires one real mobile DingTalk login to
produce an `in_client` attempt and a usable `/account` page.
