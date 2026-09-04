# HR Task Result Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistently reconcile completed HR task assistant output into real position context drafts and candidate analyses.

**Architecture:** Migration 071 owns exact completed-result discovery and an app-only leased ledger. A narrow Python repository decrypts claimed assistant content, while a reconciler maps the pinned task snapshot into existing idempotent domain services and records completion/failure.

**Tech Stack:** PostgreSQL/PLpgSQL, psycopg 3, Python dataclasses/protocols, ContentCodec, pytest.

## Global Constraints

- Base exactly `8665b4d4f5b98afd201e840a9835fb203150bf11`.
- Do not modify `main.py` or web UI files.
- Only completed, exact-owner HR task results may project.
- Never invent evidence, unknowns, or model provenance.
- Use explicit non-empty model version and SQL-verified `hr-bot` agent identity.
- Use deterministic request IDs and leased ledger replay for multi-instance/crash safety.
- A permanently bad result must not block later results.

---

### Task 1: Freeze the migration contract

**Files:**
- Create: `backend/control_migrations/071_hr_task_result_projection.sql`
- Create: `backend/tests/test_hr_task_result_projection_migration.py`

**Interfaces:**
- Produces: app-only `claim_hr_task_result_projection_v71`, `complete_hr_task_result_projection_v71`, `fail_hr_task_result_projection_v71`, and `release_hr_task_result_projection_v71` functions.

- [ ] Write static and real-database tests for the ledger schema, fixed search path, role checks, exact completed joins, unique assistant cardinality, grants, leasing, and failed-item advancement.
- [ ] Run the tests and confirm RED because migration 071 is absent.
- [ ] Implement the minimum migration and run the focused tests GREEN.

### Task 2: Implement the repository and projector contract

**Files:**
- Create: `backend/app/hr/task_result_projection.py`
- Create: `backend/tests/test_hr_task_result_projection.py`

**Interfaces:**
- Produces: `HrTaskResultProjectionRepository`, `HrTaskResultReconciler.reconcile_one() -> bool`, and `hr_task_result_projection_loop(...)`.
- Consumes: `ContentCodec`, `PositionIntelligenceService.create_draft`, and `CandidateService.add_analysis`.

- [ ] Write tests for required model provenance, full-text decrypt, five position mappings, two candidate mappings, attachment-to-document mapping, stable request IDs, replay, failure isolation, transient release, and async continuation.
- [ ] Run tests and confirm RED because the module is absent.
- [ ] Implement the smallest repository/reconciler/loop and run tests GREEN.

### Task 3: Prove real end-to-end projection

**Files:**
- Create: `backend/tests/test_hr_task_result_projection_database.py`
- Create: `.superpowers/sdd/r12-task-result-projection-report.md`

**Interfaces:**
- Exercises the public repository/reconciler API against migrations 069-071 and the existing domain services.

- [ ] Write PostgreSQL tests for position and candidate success, exact provenance and inputs, duplicate/crash replay, cross-scope and nonterminal rejection, app-only functions, and bad-result non-HOL behavior.
- [ ] Run tests and confirm RED until all required database behavior exists.
- [ ] Make only scoped corrections needed for GREEN.
- [ ] Run focused and HR regression tests, compileall, Ruff imports, and diff-check.
- [ ] Review the complete diff, write the report, and commit the clean branch.
