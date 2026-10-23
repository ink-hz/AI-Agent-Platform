# Existing-session DingTalk login repair

> Execute using test-driven-development and requesting-code-review. Authorized by the user's internal login failure report.

**Goal:** Restore FAE internal entry for browsers which already hold a Platform session.

**Architecture:** Keep Origin, CSRF, enterprise membership and session checks intact. GET /login reuses a verified active session and redirects only to a validated local return path (default /account). Cached login screens recover the existing CSRF value from the protected account bootstrap before QR/in-client mutations; a 401 is the only anonymous result. Account failures and missing CSRF fail explicitly.

**Evidence:** Oct 23 newest Nginx login start responses: 200 then 403/37 bytes twice; only one pending QR attempt in DB, no failed exchange. 37 bytes matches the existing CSRF denial. Valid-cookie login currently always renders the login shell; frontend auth mutations omit CSRF. In-client and QR previously completed successfully today. No claim yet about the user's precise browser step.

**Alternatives:** Disabling mutation CSRF would weaken shared security. Changing only the FAE link would leave cached and in-client login broken. Fix both login entry and frontend credential propagation.

- [ ] Regression: valid-cookie GET /login resumes /fae/ and encoded conversation paths; revoked/unknown cookies retain login; malformed/duplicate return paths do not redirect.
- [ ] Regression: QR and in-client mutations include bootstrapped CSRF for an active session, proceed without CSRF only after 401, stop on other account failures.
- [ ] Implement minimal server redirect and shared frontend login headers; existing middleware unchanged.
- [ ] Run affected API/frontend suites and production build, independent review.
- [ ] Merge reviewed commit into clean current master, exact-SHA API-only transaction preserving all current Compose overlays and other containers; public probes and user scan acceptance remain distinct.
