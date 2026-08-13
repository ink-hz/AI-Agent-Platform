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

## Review remediation

The first independent review found no Critical issue and four Important
issues. The follow-up change closes all four:

1. Vite now emits relative entry references, and a real Vite-build contract
   test loads the same `dist` through both `/login` and
   `/_preview/dingtalk-r1/login`, resolves every JS/CSS URL as a browser would,
   verifies preview-safe CSP, and retrieves each asset through the matching
   route.
2. The manifest and every public file are opened under a regular non-symlink
   root through descriptor-relative `openat` semantics with `O_NOFOLLOW`, then
   checked with `fstat`. Responses stream the already-opened inode instead of
   reopening a pathname. Tests cover a manifest symlink, final-asset symlink,
   intermediate-directory symlink, outside-content non-disclosure, and a path
   replacement after open.
3. Every identity-sensitive success, redirect, validation error,
   authentication failure, authorization failure, account response, and owner
   health response is forced to `Cache-Control: no-store` and
   `Pragma: no-cache` by the identity middleware.
4. The owner system-health endpoint now calls a shared live health builder
   after role enforcement and before the required audit commit. It reports
   validated release identity, deployment state, registry, runtime/local/remote
   Agents, replica state, and service availability. Public health remains the
   one-field liveness response.

Review-remediation RED evidence:

- real preview build test resolved assets to the root namespace;
- three static-boundary tests failed because a manifest symlink was accepted
  and descriptor-safe open helpers did not exist;
- the route-level symlink test returned `200` with outside file content;
- the cache-policy test found no policy on public health; and
- the detailed-health test had no `build` field.

Review-remediation GREEN evidence on the final follow-up tree:

- focused backend security/API/build suite: 59 passed;
- complete backend suite: 1,030 passed, 1 skipped;
- complete frontend suite: 29 files and 169 tests passed;
- production frontend build: succeeded with relative hashed JS/CSS references
  and a compatible `.vite/manifest.json`;
- no-Keychain runtime test: 2 passed;
- Python `compileall` and `git diff --check`: passed.
