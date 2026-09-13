# Production API Canary Implementation Plan

**Goal:** Reviewable, bounded public/synthetic HTTP canary without production execution.
**Architecture:** One standalone script and one focused test module. A private operation ledger binds owner, deployment fingerprints, UUID requests and created resources; run/resume mutate only those resources and observe is read-only.
**Tech Stack:** Python/httpx; real local PostgreSQL, platform HTTP authorization/session issuance and worker runtime in engineering tests.

Parent approved inline implementation. Only external login exchange, model provider (ScriptModel engineering only), local object storage and transport-loss injection are substituted. No browser, production call, shell restart, credential minting in script, or global resource enumeration.

- [x] Write tests rejecting missing/insecure config and proving upload response loss cannot trigger another initialization; record RED with exact source bytes.
- [x] Implement strict absolute0600 config, UUID operation journal written before sends, bounded run/resume/observe; fixed synthetic text only, upload original/text exact refs, real saved results and same-work input replay.
- [x] Add real identity/HTTP/PG integration for owner/CSRF/Origin/readiness audit and result revision persistence. Retain failure evidence and no false restart proof.
- [x] Run only focused engineering tests and lint; save commands, statuses and source hashes. Document public-only and no real model/production validation; commit only owned files.
