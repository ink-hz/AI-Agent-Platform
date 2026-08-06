# Platform Manual User Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every historical and future MetaBot Session and Turn in AI Agent Platform display the Flywheel owner-confirmed user name.

**Architecture:** Preserve the currently deployed Session and Turn views as internal base views, then recreate the public view names as compatibility wrappers. Each wrapper resolves its sender `user_id`, joins `flywheel_identity.resolved_user_names`, applies owner-confirmed/Feishu names without exposing union-ID fallbacks, and preserves the exact existing column contract.

**Tech Stack:** PostgreSQL 17 views, Python 3.11/pytest static migration tests, FastAPI read repository, React WebUI consuming unchanged APIs.

## Global Constraints

- Apply to every MetaBot historical and future Session and Turn, not one Session.
- Preserve FAE and ADMIN rows unchanged because they do not share Flywheel `user_id` identity.
- Never update `external_identities.display_name`, messages, conversations, or manual aliases.
- Never expose a `name_source = 'union_id'` fallback in normal Platform output.
- Preserve all existing `platform_read.sessions` and `platform_read.turns` columns, order, and types.
- Do not modify the existing unrelated dirty health normalizer files or local configuration.
- Production migration must be repeatable and must not require a Platform restart or frontend rebuild.

---

### Task 1: Lock the Full-Data Name Resolution Contract

**Files:**
- Create: `backend/tests/test_manual_user_names_migration.py`
- Expected later implementation: `backend/migrations/006_manual_user_names.sql`

**Interfaces:**
- Consumes: migration SQL text.
- Produces: regression checks for Session coverage, Turn coverage, union-ID privacy, compatibility, and permissions.

- [ ] **Step 1: Write the failing migration contract test**

Create this test module:

```python
from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations/006_manual_user_names.sql"


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_migration_wraps_all_metabot_sessions_and_turns() -> None:
    sql = migration_sql()
    assert "alter view platform_read.sessions rename to sessions_raw_identity" in sql
    assert "alter view platform_read.turns rename to turns_raw_identity" in sql
    assert "create or replace view platform_read.sessions as" in sql
    assert "create or replace view platform_read.turns as" in sql
    assert sql.count("flywheel_identity.resolved_user_names") == 2
    assert "latest_sender" in sql
    assert "turn_sender" in sql


def test_migration_never_exposes_union_id_fallbacks() -> None:
    sql = migration_sql()
    assert sql.count("name_source in ('manual', 'feishu')") == 2
    assert "preferred_name" in sql


def test_migration_preserves_non_metabot_and_timestamp_contracts() -> None:
    sql = migration_sql()
    assert sql.count("source_kind = 'metabot'") >= 4
    for column in (
        "question_at", "answer_at", "question_time_status", "answer_time_status"
    ):
        assert column in sql


def test_migration_restores_owner_and_read_permissions() -> None:
    sql = migration_sql()
    assert "alter view platform_read.sessions owner to flywheel_owner" in sql
    assert "alter view platform_read.turns owner to flywheel_owner" in sql
    assert "grant select on platform_read.sessions, platform_read.turns to flywheel_analyst" in sql
    assert "revoke all on platform_read.sessions_raw_identity" in sql
    assert "revoke all on platform_read.turns_raw_identity" in sql
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_manual_user_names_migration.py -q
```

Expected: four failures caused by missing `migrations/006_manual_user_names.sql`.

### Task 2: Add the Repeatable Platform Read-Layer Migration

**Files:**
- Create: `backend/migrations/006_manual_user_names.sql`
- Modify: `README.md`

**Interfaces:**
- Consumes: existing `platform_read.sessions`, `platform_read.turns`, `flywheel_analytics.messages`, and `flywheel_identity.resolved_user_names`.
- Produces: unchanged public view schemas with corrected `primary_sender_name` and `sender_name` for every MetaBot record.

- [ ] **Step 1: Preserve the deployed base views exactly once**

Use idempotent catalog guards:

```sql
\set ON_ERROR_STOP on

do $$
begin
  if to_regclass('platform_read.sessions_raw_identity') is null then
    alter view platform_read.sessions rename to sessions_raw_identity;
  end if;
  if to_regclass('platform_read.turns_raw_identity') is null then
    alter view platform_read.turns rename to turns_raw_identity;
  end if;
end
$$;
```

- [ ] **Step 2: Recreate the public Session view with all 19 columns**

Build `latest_sender` from the newest non-null user message per conversation. Join a MetaBot base row by `native_id = conversation_id::text`, then join `resolved_user_names` by `user_id`. Calculate `effective_name` as:

```sql
case
  when base.source_kind = 'metabot'
    and resolved.name_source in ('manual', 'feishu')
    then resolved.preferred_name
  else base.primary_sender_name
end
```

Explicitly select, in order: `session_key`, `agent_id`, `source_kind`, `native_id`, `channel`, `title`, `user_identity`, `created_at`, `last_active_at`, `turn_count`, `feedback_count`, `review_count`, `latest_outcome`, `source_synced_at`, `details`, `participant_count`, calculated `primary_sender_name`, `primary_sender_department`, and calculated `sender_identity_status`.

- [ ] **Step 3: Recreate the public Turn view with all 25 columns**

Build `turn_sender` from the newest non-null user message per `turn_id`. Join a MetaBot base row by `native_id = turn_id::text`, resolve the name with the same `manual|feishu` rule, and explicitly preserve all columns through `answer_time_status`:

```sql
case
  when base.source_kind = 'metabot'
    and resolved.name_source in ('manual', 'feishu')
    then resolved.preferred_name
  else base.sender_name
end as effective_name
```

For MetaBot, recompute `sender_identity_status` from `effective_name` and `sender_department`; for FAE/ADMIN, preserve the base status unchanged.

- [ ] **Step 4: Reinstate ownership and least privilege**

```sql
alter view platform_read.sessions owner to flywheel_owner;
alter view platform_read.turns owner to flywheel_owner;
revoke all on platform_read.sessions_raw_identity,
  platform_read.turns_raw_identity from public, flywheel_ingest, flywheel_analyst;
revoke all on platform_read.sessions, platform_read.turns from public, flywheel_ingest;
grant select on platform_read.sessions, platform_read.turns to flywheel_analyst;
```

- [ ] **Step 5: Document the prerequisite and deployment command**

Add to `README.md` that Flywheel migration 013 must exist, then apply:

```bash
psql '<Platform owner PostgreSQL URL>' -v ON_ERROR_STOP=1 \
  -f backend/migrations/006_manual_user_names.sql
```

State that the migration changes all MetaBot historical/future Session and Turn display names immediately and needs no process restart.

- [ ] **Step 6: Run focused and complete backend tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_manual_user_names_migration.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit only this feature's files**

```bash
git add backend/migrations/006_manual_user_names.sql \
  backend/tests/test_manual_user_names_migration.py README.md \
  docs/superpowers/plans/2026-08-06-platform-manual-user-names.md
git commit -m "fix(platform): resolve all metabot user names"
```

### Task 3: Apply and Verify Every Production Record

**Files:**
- No additional tracked files.

**Interfaces:**
- Consumes: production Flywheel owner socket and Platform API on `127.0.0.1:8000`.
- Produces: corrected names on all existing and future MetaBot Session/Turn API records.

- [ ] **Step 1: Capture count-only baselines**

Record counts from `platform_read.sessions`, `platform_read.turns`, Flywheel messages, and conversations before migration. Record the number of distinct confirmed users and their matching Session/Turn rows without printing IDs or names.

- [ ] **Step 2: Apply migration 006 with the existing local owner socket**

```bash
psql -X -h /Users/neo/FlywheelData/socket -d flywheel \
  -v ON_ERROR_STOP=1 -f backend/migrations/006_manual_user_names.sql
```

Expected: both wrapper views are created and permissions restored. No service restart or frontend rebuild is performed.

- [ ] **Step 3: Verify all confirmed users, not only the reported Session**

Run count-only assertions requiring:

- every MetaBot Session whose latest sender has a manual alias returns that alias;
- every MetaBot Turn whose sender has a manual alias returns that alias;
- no public MetaBot name equals a resolved union-ID fallback;
- FAE/ADMIN rows match their base view names and statuses;
- all baseline row counts remain unchanged.

- [ ] **Step 4: Verify the reported API symptom directly**

Call:

```bash
curl -fsS \
  'http://127.0.0.1:8000/api/sessions/metabot%3Amarketing-prospecting-bot%3Ab9466ce5-5ab7-4dd3-93f5-887b2a25497d'
```

Assert `primary_sender_name` is the confirmed manual name and every returned Turn has the same `sender_name`. Return only booleans/counts in verification output.

- [ ] **Step 5: Final privacy and workspace verification**

Run Backend tests again, `git diff --check`, and scan the feature files for production Feishu IDs or employee names. Confirm the pre-existing dirty health files and local untracked configuration remain untouched.
