# SDK 原生需求管理入口 Implementation Plan

> **For agentic workers:** Inline execution in this session; keep production changes sequential and preserve the full platform Compose overlay.

**Goal:** Make ZenTao the usable SDK requirements workspace and make `/sdk/` its clear entry.

**Architecture:** ZenTao owns the standard lifecycle. AI-Agent-Platform links to its isolated HTTPS origin. The former platform requirement and candidate evidence endpoints are disabled in native mode because their review and task links cannot represent native ZenTao records. A pinned, patched ZenTao image disables the known comment authorization flaw.

**Tech Stack:** React, FastAPI, Nginx, ZenTao OSS 22.6, Docker Compose, MariaDB.

## Global Constraints

- GitLab development integration stays off.
- ZenTao upstream remains bound to `127.0.0.1:18131`.
- Production API updates preserve every current Compose overlay and non-API container.
- Browser page acceptance is performed by the user under `AGENTS.md`; the agent verifies HTTP/API paths.

## Tasks

### 1. SDK entry

- [x] Add a component test that requires a ZenTao entry without loading the annual CSV or old platform demand data.
- [x] Run the test red, then render a dedicated native entry.
- [x] Build the web UI and run focused component tests.
- [x] Add a production mode that returns HTTP 410 for old requirement, review, task, CSV and candidate APIs, and preserve its setting through release rendering.

### 2. Isolated ZenTao web service

- [x] Test the Nginx template's separate origin and loopback upstream.
- [x] Add the HTTPS server template and block the unused `apilogin` path.
- [x] Verify rendered Nginx config with `nginx -t` on the target host using the current certificate for syntax validation only.
- [ ] After DNS resolves, issue the certificate and verify external 302 login and static assets.

### 3. ZenTao security and identity

- [x] Add a pinned derivative image build that verifies the controller hash and disables `editComment`.
- [x] Build the image, lint patched PHP, back up ZenTao data, and restart only ZenTao.
- [x] Provision a named employee account for the first SDK user and verify product ACL.
- [x] Exercise requirement → review → task → linked test case with distinct test records; no hardware test execution claimed.

### 4. Platform rollout

- [x] Run focused backend and frontend regression tests.
- [ ] Merge to master, build the release from the exact master SHA, and update only the platform API using all existing Compose overlays.
- [ ] Verify `/sdk/`, authentication and business APIs from HTTP; record production state and browser acceptance limitation.
