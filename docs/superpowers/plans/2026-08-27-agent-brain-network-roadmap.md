# Agent Brain Network Delivery Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the durable Agent Brain, shared HTTP task contract, FAE/Admin integrations, and shared attachment substrate as four independently releasable TDD workstreams.

**Architecture:** Agent Platform owns identity, authorization, durable Brain state, actions, shared contract tests, and attachments. FAE and AI ADMIN remain independently deployed professional Agents that implement the same versioned HTTP task contract. Each workstream has an independent rollback boundary; Catalog delegation is enabled only after the corresponding contract suite passes.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, PostgreSQL, psycopg 3, pytest, React/TypeScript, MinIO/S3 API, ClamAV, Nginx, Docker Compose.

## Global Constraints

- Use Python `>=3.11`; never invoke the macOS system Python for project tests.
- Every production behavior follows RED → GREEN → REFACTOR; no production code before a failing test.
- Preserve `https://fae.orbbec.com.cn/`, `https://agent.orbbec.com.cn/office/`, and existing customer/Admin browser behavior.
- Platform Session cookies never cross into Brain task APIs; task calls use short-lived audience/task/scope-bound tokens.
- `action_digest` is RFC 8785 JCS SHA-256 over exactly `platform_task_id`, `action_seq`, `action_kind`, and `parameters`.
- Pending Actions are authorized only by the authenticated Conversation Owner; the model never receives a confirmation tool.
- `brain_task_event_cursors.delivered_seq` is the only model-delivery waterline; `brain_wait_subscriptions.cursors` is removed.
- HTTP event reads use finite JSON pages with `wait_seconds=0`; Brain Worker ticks never enter SSE or long polling.
- FAE/AI ADMIN delegation remains disabled until the pinned shared contract suite passes in that repository.
- Attachment objects are private; only `ready` objects may leave quarantine through Preview or Media Gateway.

---

## Workstream order

1. Execute [Platform Brain Core Implementation Plan](2026-08-27-agent-brain-platform-core-implementation.md).
2. Start [Shared Attachment Substrate Implementation Plan](2026-08-27-agent-attachment-substrate-implementation.md) after Platform migration 050 is frozen; it may run in parallel with FAE/Admin task work.
3. Execute [FAE Task Integration Implementation Plan](2026-08-27-fae-task-integration-implementation.md) after the Platform contract runner and HTTP adapter base are released.
4. Execute [AI ADMIN Task Integration Implementation Plan](2026-08-27-admin-task-integration-implementation.md) after the same contract runner release; first release is read-only.
5. Enable FAE and AI ADMIN in Catalog one at a time after contract, identity, deadline, and rollback acceptance.

## Cross-workstream release gates

- [ ] Platform P0 proves a real Catalog `capability_version=2` HR delegation without a Reference-only fake.
- [ ] Migration 049 proves event-before-Wait, one-waterline behavior, 40001 retry exhaustion, Reaper recovery, task-local protocol isolation, and forced+pending behavior.
- [ ] Migration 050 proves six Action outcomes and crash recovery after proposal, before confirmation, and after confirmation.
- [ ] `contracts/http_task_v1/` runs under Python 3.11+ in Platform, FAE, and AI ADMIN from the same pinned Commit/SHA-256.
- [ ] FAE and AI ADMIN enforce `capability_version`, Scope, Task Deadline, and terminal irreversibility themselves.
- [ ] Attachment A0-A2 passes before any new Agent advertises image/document capability; A3 passes before FAE advertises it.
- [ ] Production Catalog changes are separate commits from schema/application deploys and have explicit rollback commits.

## Final integrated acceptance

- [ ] One DingTalk employee is represented by one `internal_user_id` through Brain, VOC, FAE enterprise workspace, and AI ADMIN.
- [ ] A Brain Turn delegates concurrently to at least three real Agents and preserves real public events without mock progress.
- [ ] A VOC Action is proposed, confirmed, executed exactly once, and synthesized after a forced budget boundary.
- [ ] FAE answers one image/document task through the shared attachment grant without learning an Object Key.
- [ ] AI ADMIN answers one read-only service task; write methods remain unreachable in the first release.
- [ ] A local MetaBot Agent may be unavailable without hiding FAE/VOC/Admin or degrading unrelated workers.
- [ ] FAE public customers and `/office/?view=services` pass regression checks after all Platform releases.
