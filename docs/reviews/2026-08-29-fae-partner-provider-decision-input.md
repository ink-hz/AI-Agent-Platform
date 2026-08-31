# FAE Partner Provider decision input

Date: 2026-08-31
Decision state: production Partner login intentionally disabled

## What is already fixed

Agent Platform owns partner organizations, operators, identity mappings, FAE-only grants, revocation and audit.
FAE consumes only a 60-second single-use Launch Code and subsequent Binding validation. Partner operators never
receive a normal Platform Web Session and cannot enter Agent Brain, the Agent directory, `/admin/*` or
`/office/*`.

FAE capability is not tiered by identity: enterprise members and Partner operators use the same model, Loop,
tools, knowledge, sources, image/attachment limits and Feedback behavior. Session, history, attachment and
Feedback ownership remains private per generic `subject_id`.

No shared password, public signup, identity-by-name/phone, browser-supplied role, runtime Provider fallback or
Reference Provider is permitted in production.

## Information needed from the real partner environment

Provide only these decision facts; do not send secrets in chat or commit them to a repository:

1. Candidate Provider name and official product/API documentation.
2. Stable, per-operator, non-reassignable subject field returned after authentication.
3. Authorization flow: redirect, QR, embedded client, SSO or another server-verified flow.
4. Active/revoked status mechanism and the maximum time to detect removal.
5. Two distinct real operator test accounts that can be used in Dev acceptance.
6. Required application type and least-privilege permission package.
7. Exact callback allowlist rules, including whether Dev and production callbacks can coexist.
8. Access-token lifetime, refresh behavior, revocation behavior and rate limits.

Useful but non-blocking context: whether the same operator can belong to multiple partner organizations, whether
subjects can be reassigned after deletion, and whether status changes have events or require polling.

## Mandatory Dev probe

Before writing a Provider-specific implementation plan, the two real accounts must prove:

- repeated login returns the same stable subject for each operator;
- the two operators return distinct subjects;
- an inactive/revoked operator is rejected inside the documented bound;
- OAuth state/callback replay is rejected server-side;
- shared-password authentication is impossible;
- no credential, raw subject, callback code or token enters URLs, browser storage, application logs or evidence.

The sanitized evidence bundle is archived outside the repository. Only its lowercase SHA-256, probe timestamp,
Provider kind and five pass/fail flags enter the root-owned mode-0600 archive release document. Deployment must
produce byte-identical, read-only service copies: for the Platform API container this is `root:10001` mode `0640`
at an explicit bind-mounted path; FAE receives an equivalent service-readable copy at its own explicit path.

## Decision output expected

The next review must choose exactly one registered non-Reference Provider and produce a Provider-specific design
and TDD plan covering adapter implementation, callbacks, active-status checks, credentials, rate limits, event or
polling behavior, two-operator Dev acceptance, production rollout and rollback. It must also define one exact
Provider kind and release-evidence digest for both repositories and mount byte-identical evidence read-only at
explicit container paths in Platform and FAE; a host path visible to only one service is insufficient. Production
remains disabled until that release passes the Platform registration gate, the FAE kind-match gate and the FAE
authenticated identity/session bottom is enabled; there is no temporary shared-password or hidden fallback path.
