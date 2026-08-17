# Account Bootstrap Resilience Design

## Context

The authenticated Web shell currently reloads `/api/v1/account` whenever the
route name changes. Every navigation therefore replaces the active page with a
full-screen identity verification state. The request has no client-side
deadline, and any transient network or gateway failure leaves a static generic
error page with no recovery action.

Production evidence confirms that account reads are normally fast: among the
most recent 96 requests, 88 returned 200 in at most 278 ms, seven returned 401,
and one returned a transient 502. The recurring visible interruption is caused
by the client state machine, not normal account-query latency.

## Scope

This change is limited to authenticated account bootstrap and its recovery UI.
It does not change DingTalk OAuth, server-side authorization, Session lifetime,
directory freshness policy, or deployment topology.

## Design

1. Bootstrap the account when entering the authenticated shell, not on every
   transition between authenticated product routes. A transition from the
   login route into the authenticated shell still triggers bootstrap.
2. Preserve the loaded account for normal navigation. Server-side middleware
   remains authoritative for every protected API request, so this client cache
   does not weaken authorization.
3. Give an account read a five-second deadline. Retry once after a short fixed
   delay only for a network failure, client timeout, HTTP 502, or HTTP 504.
   Do not retry 401, 403, malformed responses, or hard directory failures.
4. Keep the full-screen verification state only for initial bootstrap and an
   explicit retry. A failure page must offer `重新尝试` and `重新登录` actions.
5. Keep user-facing text concise. Do not expose provider identifiers, raw
   backend errors, tokens, or stack traces.

## Failure Semantics

- `401`: redirect to the login page.
- `403`: show the existing permission state.
- directory `503`: show the existing directory-unavailable state.
- exhausted transient or other unexpected failure: show the platform
  unavailable state with explicit recovery actions.
- retry starts a new bounded account bootstrap attempt without a full browser
  reload.

## Verification

- A route transition between authenticated pages makes no additional account
  request and never shows the identity verification shell.
- A transient 502 or network failure is retried once and can recover.
- A request exceeding five seconds is aborted and retried once.
- The unavailable page exposes both recovery actions, and `重新尝试` can recover
  in place.
- Existing 401, 403, 503, account-schema validation, and DingTalk login tests
  continue to pass.

## Non-goals

- Blue/green deployment or multiple API replicas.
- Persistent client-side account storage.
- Retrying mutations or weakening fail-closed authorization.
