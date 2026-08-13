# Task 6 implementation report

## Outcome

Implemented the Release 1 DingTalk Web authentication boundary without changing
production, Nginx, FAE, real DingTalk configuration, or any operating-system
credential store.

The candidate now has:

- five-minute, environment-bound, one-time login attempts;
- QR OAuth with S256 PKCE and exact `openid corpid` scope;
- backend-only DingTalk code exchange and active-member resolution;
- opaque server-side Web Sessions with database-time 8-hour idle and 24-hour
  absolute expiry;
- atomic directory locking and current-generation membership revalidation in
  the same transaction that consumes an attempt and issues a Session;
- production and preview Cookies with exact secure attributes and paths;
- a second HttpOnly CSRF Cookie so the account bootstrap can recover the raw
  CSRF value after a QR callback navigation, while PostgreSQL stores only the
  purpose-separated HMAC digest;
- exact public route and build-manifest allowlists;
- authentication-before-authorization handling and Origin plus constant-time
  CSRF checks for mutations;
- minimal public health and fail-closed, owner-only audited system health; and
- unchanged `identity_mode=disabled` behavior.

## Database boundary

Migration `015_secure_web_sessions.sql` introduces versioned state, PKCE,
Session-token, and CSRF hashes and narrow `SECURITY DEFINER` functions. Direct
login-attempt and Web-Session DML is revoked from runtime roles. Production and
preview grants are mutually exclusive, and `PUBLIC` has no execute permission.

The issuance function acquires
`platform_control.lock_dingtalk_identity_directory()`, locks the claimed
attempt, selects the current complete generation, and rechecks the locally
valid user and active current-generation member before insertion. A deterministic
concurrency test proves that a directory departure promotion wins before a
waiting Session issuer and causes issuance to return no Session.

The superseded direct-DML methods on `ControlRepository` now fail closed;
`WebSessionRepository` is the only application facade for migration 015.

## Public boundary

Unauthenticated access is limited to the exact method/path pairs in the design.
Static files are public only when both their filename and the Vite build
manifest authorize them; a hash-looking file merely present on disk is denied.
Public health returns only `{ "status": "ok" }`. Every other route receives a
backend Session check.

The middleware ignores client-supplied forwarding headers when evaluating the
canonical Origin. Source-address derivation and state-first rate limiting stay
in Task 7, where the plan assigns them.

## TDD evidence

Initial RED runs:

- API/security/static tests: 19 failed, 2 passed before routes and middleware;
- PostgreSQL security tests: 4 failed, 12 passed before migration 015 and the
  narrow repository;
- PKCE provider-body test: 1 failed before `codeVerifier` support;
- build-manifest test: 1 failed before manifest-bound serving;
- account CSRF bootstrap regression: 1 failed because `/account` returned an
  empty token after QR navigation.

Final GREEN runs on the completed tree:

- backend full suite: 1,023 passed, 1 skipped;
- frontend full suite: 29 files and 169 tests passed;
- frontend production build: succeeded, including `.vite/manifest.json` and
  hashed CSS/JS outputs;
- no-Keychain runtime test: 2 passed;
- Python `compileall`: passed;
- `git diff --check`: passed;
- targeted scan for the provided DingTalk identifiers, private-key markers,
  and Keychain invocation patterns in added lines: clean.

Warnings are limited to existing Starlette/httpx deprecations and test-client
per-request Cookie deprecations; there are no test failures.

## Deferred by the approved plan

- Task 7 owns state-first login throttling, coarse NAT-aware IP ceilings,
  trusted-proxy source derivation, and concurrency limits.
- Task 10 owns the hard-stale privileged read-only reauthentication exception.
- Task 12 owns the complete member/viewer/owner route authorization matrix.
- Task 13 owns the final login/account UI and role-aware shell.
- No real DingTalk request or production deployment was performed in this task.
