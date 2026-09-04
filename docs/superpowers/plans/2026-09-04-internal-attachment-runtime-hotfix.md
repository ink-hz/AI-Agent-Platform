# Internal Attachment Runtime Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unblock the internal HR R1.2 attachment path without ClamAV and prevent transient FAE HTTP resets from rolling back a valid release.

**Architecture:** Add an explicit trusted-internal scanner that still streams every byte through the existing integrity check. Remove the ClamAV production container and give immutable FAE response checks bounded retries.

**Tech Stack:** Python, pytest, Docker Compose, Bash, curl.

## Global Constraints

- Preserve size, MIME, SHA-256, immutable-object, and sandbox boundaries.
- Unknown scan modes fail closed.
- Do not modify FAE, Nginx, MetaBot, or other applications.
- Follow the `/data` staging and root-disk release gates.

---

### Task 1: Trusted internal attachment processing

**Files:**
- Modify: `backend/app/attachments/scanner.py`
- Modify: `backend/app/attachments/worker_runtime.py`
- Test: `backend/tests/test_attachment_scanner.py`
- Test: `backend/tests/test_attachment_worker_runtime.py`

**Interfaces:**
- Produces: `TrustedInternalScanner.scan_stream(chunks, size) -> ScanResult`
- Consumes: `PLATFORM_ATTACHMENT_SCAN_MODE=trusted-internal`

- [x] Add failing tests proving all bytes are consumed, the result is clean, health does not require ClamAV, and unknown modes fail closed.
- [x] Run the focused tests and observe the expected failures.
- [x] Implement the minimal scanner selection and health behavior.
- [x] Run the focused tests and confirm they pass.

### Task 2: Production Compose and resilient release check

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/remote-stage.sh`
- Modify: `deploy/cloud/acceptance.sh`
- Test: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Consumes: `PLATFORM_ATTACHMENT_SCAN_MODE=trusted-internal`
- Produces: bounded curl retry with exact digest comparison.

- [x] Add failing deployment-contract assertions for removal of ClamAV and bounded FAE HTTP retries.
- [x] Run the focused deployment test and observe the expected failures.
- [x] Remove ClamAV from Compose/acceptance and add retry-only curl options.
- [x] Run the focused deployment test and confirm it passes.
- [ ] Run attachment, deployment, and local release gates before committing and deploying.
