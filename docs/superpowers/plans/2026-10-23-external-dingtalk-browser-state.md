# External DingTalk browser-state repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Complete valid external SNS login without pending QR pages invalidating each other.
**Architecture:** Per-attempt browser nonce cookies + explicit safe browser error recovery.
Identity proofs, five-minute DB attempts, provisioning and authorization stay unchanged.
**Tech Stack:** FastAPI, Python, pytest/httpx, disposable PostgreSQL, Docker.

## Global Constraints

Follow docs/superpowers/specs/2026-10-23-external-dingtalk-browser-state-design.md verbatim.
Only verified external SNS identities can receive FAE-only virtual identities.
No missing-cookie bypass, anonymous/enterprise fallback, protocol switch or callback retry.
No model calls, production evals, Nginx edits, DB migrations or computer use.
Preserve current production31f7de2f VOC and all unrelated code/assets/settings/services.

### Task 1: Pending-state isolation and safe recovery

Files: backend/app/control_plane/partner_provider.py (typed start metadata if needed),
backend/app/control_plane/routes_partner.py, a focused browser helper module only if useful,
backend/tests/test_external_dingtalk_integration.py, focused browser/state regression tests.

- [ ] Write failing HTTP regression with two /partner-auth/start requests in the same
  TestClient cookie jar; completing the first state must reach provider once and launch
  canonical FAE; completing second must also work independently. Observe failure first.
- [ ] Implement per-attempt cookie namespace derived from validated state, secure flags
  and300second TTL; callback lookup/delete only matching cookie. Test stranger/missing/
  mismatched/duplicate/legacy/replay/expired cases still fail before provider as appropriate.
- [ ] Enforce3 pending cookies before creating another DB attempt; explicit429 response
  and unchanged existing cookies/attempts. Completed attempts free their own slot.
- [ ] HTML Accept receives safe401 recovery for state failures and429 pending-limit;
  API Accept retains JSON codes. Fixed retry URL https://fae.orbbec.com.cn/partner/login,
  return URL https://fae.orbbec.com.cn/. No dynamic HTML/token/query reflection.
- [ ] Add safe enumerated rejection logging without state/code/nonce/PII and verify
  malicious callback strings do not reach response/logs. Preserve response security headers.
- [ ] Run focused auth tests including real disposable PostgreSQL cases, no live provider;
  self-review and commit exact files, report TDD and scope/risks to controller.

### Task 2: Review, controlled API-only rollout and evidence

- [ ] Independent spec/security review of full patch and acceptance evidence.
- [ ] Snapshot CURRENT image/source/Compose/health/env/mounts and unrelated resources;
  never assume olderd0 remains deployed. Build reviewed file-only layer atop exact current
  image and verify all unrelated filesystem/frontend hashes are retained.
- [ ] Test actual API process in isolated configuration, no provider credentials/calls;
  validate pending-state cookies and HTML/API negative callback behavior.
- [ ] Under existing locks, snapshot DB/config, append minimal API image/release overlay,
  replace only Platform API with health/rollback checks; do not change FAE or Nginx.
- [ ] Production non-provider checks: two starts get distinct scoped cookies, callbacks
  with missing/wrong cookies fail before provider and safely recover, internal entry,
  canonical FAE/old-entry retirement and unaffected resource fingerprints preserved.
- [ ] Record source/image/receipt, merge reviewed repair into current main while preserving
  others' SDK/VOC changes; request one fresh real-user scan and distinguish pending result.
