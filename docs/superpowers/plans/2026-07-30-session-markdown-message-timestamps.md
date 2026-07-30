# Session Markdown and Message Timestamps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every Session question and answer as safe Markdown and show separate, source-honest Asia/Shanghai timestamps for both messages across MetaBot, AI FAE, and AI ADMIN.

**Architecture:** Add exact message timestamps at the two source collectors, carry them through the existing daily full-snapshot synchronization, and expose a normalized four-field timestamp contract from `platform_read.turns`. MetaBot uses existing message events; legacy FAE/ADMIN rows use explicitly estimated fallbacks. The WebUI uses one safe Markdown component and one timestamp formatter so every Session source behaves consistently.

**Tech Stack:** PostgreSQL migrations and views, Python 3.12, FastAPI/Pydantic, psycopg 3, React 19, TypeScript, `react-markdown`, `remark-gfm`, pytest, Vitest, Vite.

## Global Constraints

- Preserve all existing source questions and answers exactly; never translate or rewrite message content.
- Display all message times in `Asia/Shanghai` to the second.
- Use only `exact`, `estimated`, or `unavailable` as timestamp status values.
- Never label a derived legacy timestamp as exact.
- Do not enable `rehype-raw` and do not use `dangerouslySetInnerHTML`.
- Keep `created_at` for backward compatibility and existing ordering/counting behavior.
- New database columns are nullable and every migration is idempotent.
- Preserve all pre-existing dirty files in all three repositories; stage only files listed by the active task.
- Deploy source migrations before deploying collectors that write the new fields.
- Do not change Feedback, Review, Trace, Evidence, Session counting, or Conversation counting semantics.

## File Map

### AI-FAE-Agent

- Create `migrations/003_message_timestamps.sql`: nullable source timestamp columns.
- Modify `src/storage/data_flywheel.py`: extend `ChatTurnRecord`.
- Modify `src/storage/postgres_data_flywheel.py`: persist both fields on insert and conflict update.
- Modify `src/api/routes.py`: capture request and completed-answer wall-clock times.
- Modify `tests/unit/test_data_flywheel_schema.py`: migration contract.
- Modify `tests/unit/test_data_flywheel_store.py`: fallback serialization contract.
- Modify `tests/unit/test_routes_data_flywheel.py`: precise capture behavior.

### AI-ADMIN-Agent

- Create `migrations/008_message_timestamps.sql`: nullable source timestamp columns.
- Modify `src/storage/data_flywheel.py`: extend `ChatTurnRecord` and JSONL payloads naturally through `asdict`.
- Modify `src/storage/postgres_data_flywheel.py`: persist both fields on insert and conflict update.
- Modify `src/admin_agent/orchestrator.py`: capture request and completed-answer wall-clock times.
- Modify `tests/unit/test_data_flywheel_schema.py`: migration contract.
- Modify `tests/unit/test_data_flywheel.py`: JSONL/fallback serialization contract.
- Modify `tests/unit/test_orchestrator.py`: precise capture behavior.

### AI-Agent-Platform backend

- Create `backend/migrations/004_session_message_timestamps.sql`: mirror columns and normalized read view.
- Create `backend/tests/test_message_timestamp_migration.py`: SQL contract and privilege checks.
- Modify `backend/app/sync_remote/importer.py`: accept the two fields from FAE/ADMIN bundles.
- Modify `backend/tests/test_sync_importer.py`: new and legacy bundle compatibility.
- Modify `backend/app/observability/models.py`: API timestamp fields and status type.
- Modify `backend/app/observability/repository.py`: map normalized timestamp fields.
- Modify `backend/tests/test_observability_repository.py`: exact/estimated/unavailable API mapping.

### AI-Agent-Platform WebUI

- Modify `webui/package.json` and `webui/package-lock.json`: add `react-markdown` and `remark-gfm`.
- Create `webui/src/components/MessageMarkdown.tsx`: safe shared message renderer.
- Create `webui/src/messageTime.ts`: timestamp validation and China-time formatting.
- Create `webui/src/messagePresentation.test.tsx`: Markdown security and timestamp presentation.
- Modify `webui/src/components/TurnCard.tsx`: use renderer and separate message times.
- Modify `webui/src/types.ts`: mirror the backend API contract.
- Modify `webui/src/styles.css`: scoped Markdown and message-time layout.
- Modify `webui/src/styles.test.ts`: responsive and overflow contract.

---

### Task 1: Capture exact timestamps in AI FAE

**Files:**
- Create: `/Users/neo/Developer/work/AI-FAE-Agent/migrations/003_message_timestamps.sql`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/storage/data_flywheel.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/storage/postgres_data_flywheel.py`
- Modify: `/Users/neo/Developer/work/AI-FAE-Agent/src/api/routes.py`
- Test: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_data_flywheel_schema.py`
- Test: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_data_flywheel_store.py`
- Test: `/Users/neo/Developer/work/AI-FAE-Agent/tests/unit/test_routes_data_flywheel.py`

**Interfaces:**
- Consumes: existing `ChatTurnRecord`, SSE request lifecycle, and PostgreSQL `chat_turns`.
- Produces: `ChatTurnRecord.question_at: datetime | None` and `answer_at: datetime | None`, stored as `timestamptz`.

- [ ] **Step 1: Write failing migration, serialization, and route tests**

Add this migration contract to `test_data_flywheel_schema.py`:

```python
MESSAGE_TIME_MIGRATION = Path("migrations/003_message_timestamps.sql")


def test_message_timestamp_migration_is_additive_and_idempotent():
    sql = MESSAGE_TIME_MIGRATION.read_text(encoding="utf-8").lower()
    assert "alter table chat_turns" in sql
    assert "add column if not exists question_at timestamptz" in sql
    assert "add column if not exists answer_at timestamptz" in sql
```

Extend `_turn()` in `test_data_flywheel_store.py` with two fixed timezone-aware datetimes and assert the JSONL fallback payload contains their ISO-compatible serialized values.

In `test_routes_data_flywheel.py`, reuse `RecordingStore`, execute one completed stream, and assert:

```python
record = store.turns[0]
assert record.question_at is not None
assert record.answer_at is not None
assert record.question_at.tzinfo is not None
assert record.answer_at.tzinfo is not None
assert record.question_at <= record.answer_at
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_data_flywheel_schema.py \
  tests/unit/test_data_flywheel_store.py \
  tests/unit/test_routes_data_flywheel.py -q
```

Expected: failures for the missing migration and missing `question_at` / `answer_at` attributes.

- [ ] **Step 3: Add the source migration and typed fields**

Create `003_message_timestamps.sql`:

```sql
alter table chat_turns
  add column if not exists question_at timestamptz null,
  add column if not exists answer_at timestamptz null;
```

Extend `ChatTurnRecord` after `duration_ms`:

```python
question_at: datetime | None = None
answer_at: datetime | None = None
```

- [ ] **Step 4: Persist both fields**

Add `question_at, answer_at` to the PostgreSQL INSERT column list, add two `%s` values, pass `record.question_at` and `record.answer_at`, and add these conflict updates:

```sql
question_at = coalesce(excluded.question_at, chat_turns.question_at),
answer_at = coalesce(excluded.answer_at, chat_turns.answer_at),
```

The `coalesce` preserves exact timestamps during replay of an older fallback record.

- [ ] **Step 5: Capture wall-clock event times in the route**

Import `datetime, timezone`. Capture `question_at = datetime.now(timezone.utc)` immediately before the request begins execution. Inside `persist_done_turn`, capture `answer_at = datetime.now(timezone.utc)` immediately before constructing `ChatTurnRecord`, then pass both fields.

- [ ] **Step 6: Run focused and full FAE tests**

Run the focused command from Step 2, then:

```bash
.venv/bin/python -m pytest tests/unit -q
```

Expected: all tests pass with no new warnings.

- [ ] **Step 7: Commit only FAE timestamp files**

```bash
git add migrations/003_message_timestamps.sql \
  src/storage/data_flywheel.py src/storage/postgres_data_flywheel.py src/api/routes.py \
  tests/unit/test_data_flywheel_schema.py tests/unit/test_data_flywheel_store.py \
  tests/unit/test_routes_data_flywheel.py
git commit -m "feat: record exact FAE message timestamps"
```

---

### Task 2: Capture exact timestamps in AI ADMIN

**Files:**
- Create: `/Users/neo/Developer/work/AI-ADMIN-Agent/migrations/008_message_timestamps.sql`
- Modify: `/Users/neo/Developer/work/AI-ADMIN-Agent/src/storage/data_flywheel.py`
- Modify: `/Users/neo/Developer/work/AI-ADMIN-Agent/src/storage/postgres_data_flywheel.py`
- Modify: `/Users/neo/Developer/work/AI-ADMIN-Agent/src/admin_agent/orchestrator.py`
- Test: `/Users/neo/Developer/work/AI-ADMIN-Agent/tests/unit/test_data_flywheel_schema.py`
- Test: `/Users/neo/Developer/work/AI-ADMIN-Agent/tests/unit/test_data_flywheel.py`
- Test: `/Users/neo/Developer/work/AI-ADMIN-Agent/tests/unit/test_orchestrator.py`

**Interfaces:**
- Consumes: existing Admin `ChatTurnRecord`, `handle_stream`, and `admin_chat_turns`.
- Produces: the same nullable exact timestamp fields as Task 1.

- [ ] **Step 1: Write failing migration, JSONL, and orchestrator tests**

Add a migration test equivalent to Task 1 but targeting `admin_chat_turns` and `migrations/008_message_timestamps.sql`. Add fixed timezone-aware values to the JSONL record test and assert they serialize. Extend `test_orchestrator_delegates_answer_to_controller` or the existing stream persistence test:

```python
record = store.chat_turns[0]
assert record.question_at is not None
assert record.answer_at is not None
assert record.question_at.tzinfo is not None
assert record.answer_at.tzinfo is not None
assert record.question_at <= record.answer_at
```

- [ ] **Step 2: Run focused Admin tests and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/unit/test_data_flywheel_schema.py \
  tests/unit/test_data_flywheel.py \
  tests/unit/test_orchestrator.py -q
```

Expected: missing migration and missing timestamp attributes.

- [ ] **Step 3: Add migration and typed fields**

Create:

```sql
alter table admin_chat_turns
  add column if not exists question_at timestamptz null,
  add column if not exists answer_at timestamptz null;
```

Add the same two nullable `datetime` fields to the Admin `ChatTurnRecord`.

- [ ] **Step 4: Persist fields without erasing prior exact values**

Add both columns and parameters to `PostgresDataFlywheelStore.record_chat_turn` and use:

```sql
question_at = coalesce(excluded.question_at, admin_chat_turns.question_at),
answer_at = coalesce(excluded.answer_at, admin_chat_turns.answer_at),
```

- [ ] **Step 5: Capture timestamps in `handle_stream`**

Import `datetime, timezone`. Capture `question_at` before controller/fast-QA execution and `answer_at` after the final answer is ready but before `record_chat_turn`. Pass both into the record for normal and fast-QA paths because they converge on the same persistence block.

- [ ] **Step 6: Run focused and full Admin tests**

Run Step 2, then:

```bash
.venv/bin/python -m pytest tests/unit -q
```

Expected: all tests pass; existing release evidence files remain unchanged.

- [ ] **Step 7: Commit only Admin timestamp files**

```bash
git add migrations/008_message_timestamps.sql \
  src/storage/data_flywheel.py src/storage/postgres_data_flywheel.py \
  src/admin_agent/orchestrator.py tests/unit/test_data_flywheel_schema.py \
  tests/unit/test_data_flywheel.py tests/unit/test_orchestrator.py
git commit -m "feat: record exact ADMIN message timestamps"
```

---

### Task 3: Normalize timestamps in Platform PostgreSQL and synchronization

**Files:**
- Create: `backend/migrations/004_session_message_timestamps.sql`
- Create: `backend/tests/test_message_timestamp_migration.py`
- Modify: `backend/app/sync_remote/importer.py`
- Modify: `backend/tests/test_sync_importer.py`

**Interfaces:**
- Consumes: Task 1/2 nullable source fields and existing MetaBot role messages.
- Produces: `platform_read.turns.question_at`, `answer_at`, `question_time_status`, and `answer_time_status`.

- [ ] **Step 1: Write failing migration contract tests**

Create `test_message_timestamp_migration.py` and assert the migration:

```python
from pathlib import Path

MIGRATION = Path(__file__).parents[1] / "migrations/004_session_message_timestamps.sql"


def sql_text() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_mirror_tables_accept_source_message_timestamps():
    sql = sql_text()
    assert "alter table platform_source_fae.chat_turns" in sql
    assert "alter table platform_source_admin.chat_turns" in sql
    assert sql.count("add column if not exists question_at timestamptz") == 2
    assert sql.count("add column if not exists answer_at timestamptz") == 2


def test_metabot_view_keeps_separate_role_times():
    sql = sql_text()
    assert "min(m.occurred_at) filter (where m.role = 'user') as question_at" in sql
    assert "max(m.occurred_at) filter (where m.role = 'assistant') as answer_at" in sql


def test_legacy_remote_times_are_explicitly_estimated():
    sql = sql_text()
    assert "t.created_at - (t.duration_ms * interval '1 millisecond')" in sql
    assert "'estimated'::text" in sql
    assert "'unavailable'::text" in sql


def test_read_privileges_survive_view_replacement():
    sql = sql_text()
    assert "alter view platform_read.turns owner to flywheel_owner" in sql
    assert "grant select on platform_read.turns to flywheel_analyst" in sql
```

- [ ] **Step 2: Write failing importer compatibility tests**

In `test_sync_importer.py`, normalize one FAE row with both fields and one legacy ADMIN row without them:

```python
assert exact.values["question_at"] == "2026-07-30T08:00:00+00:00"
assert exact.values["answer_at"] == "2026-07-30T08:00:05+00:00"
assert legacy.values["question_at"] is None
assert legacy.values["answer_at"] is None
```

- [ ] **Step 3: Run Platform backend focused tests and verify RED**

```bash
cd backend
.venv/bin/pytest -q \
  tests/test_message_timestamp_migration.py \
  tests/test_sync_importer.py
```

Expected: missing migration and importer values.

- [ ] **Step 4: Add mirror columns and replace the canonical view**

Start the migration with four idempotent ALTER statements. Recreate `platform_read.turns` using the current sender-identity view as the complete column baseline, with these additional CTE fields:

```sql
min(m.occurred_at) filter (where m.role = 'user') as question_at,
max(m.occurred_at) filter (where m.role = 'assistant') as answer_at
```

For MetaBot select:

```sql
mm.question_at,
mm.answer_at,
case when mm.question_at is null then 'unavailable' else 'exact' end::text,
case when mm.answer_at is null then 'unavailable' else 'exact' end::text
```

For both remote sources select:

```sql
coalesce(
  t.question_at,
  case when t.duration_ms is not null and t.duration_ms >= 0
       then t.created_at - (t.duration_ms * interval '1 millisecond') end
) as question_at,
coalesce(t.answer_at, t.created_at) as answer_at,
case when t.question_at is not null then 'exact'
     when t.duration_ms is not null and t.duration_ms >= 0 then 'estimated'
     else 'unavailable' end::text as question_time_status,
case when t.answer_at is not null then 'exact'
     when t.created_at is not null then 'estimated'
     else 'unavailable' end::text as answer_time_status
```

Keep the existing `created_at`, identity fields, ownership, and grants unchanged.

- [ ] **Step 5: Extend both importer column allowlists**

Add `"question_at", "answer_at"` immediately after `"created_at"` in FAE and ADMIN turn tuples. `normalize_row` already uses `row.get`, which supplies `None` for legacy bundles.

- [ ] **Step 6: Run focused tests and commit**

Run Step 3 and expect PASS, then:

```bash
git add backend/migrations/004_session_message_timestamps.sql \
  backend/tests/test_message_timestamp_migration.py \
  backend/app/sync_remote/importer.py backend/tests/test_sync_importer.py
git commit -m "feat: normalize Session message timestamps"
```

---

### Task 4: Expose the normalized timestamp API

**Files:**
- Modify: `backend/app/observability/models.py`
- Modify: `backend/app/observability/repository.py`
- Modify: `backend/tests/test_observability_repository.py`

**Interfaces:**
- Consumes: Task 3 view fields.
- Produces: serialized `TurnDetail` fields with `MessageTimeStatus = Literal["exact", "estimated", "unavailable"]`.

- [ ] **Step 1: Write failing repository tests**

Extend a `get_session` fixture row with exact message times and assert both values/statuses survive. Add a second parametrized case for estimated and unavailable values. Assertions must target the returned Pydantic model, not raw fake rows.

- [ ] **Step 2: Run the repository tests and verify RED**

```bash
cd backend
.venv/bin/pytest -q tests/test_observability_repository.py
```

Expected: `TurnDetail` has no timestamp/status attributes.

- [ ] **Step 3: Extend model and mapping**

Add:

```python
MessageTimeStatus = Literal["exact", "estimated", "unavailable"]
```

and to `TurnDetail`:

```python
question_at: datetime | None = None
answer_at: datetime | None = None
question_time_status: MessageTimeStatus = "unavailable"
answer_time_status: MessageTimeStatus = "unavailable"
```

Map the four row values in `_turn_detail`, using `row.get(...)` and `or "unavailable"` so the API remains compatible during rolling migration.

- [ ] **Step 4: Run backend tests and commit**

```bash
cd backend
.venv/bin/pytest -q tests/test_observability_repository.py
.venv/bin/pytest -q
cd ..
git add backend/app/observability/models.py backend/app/observability/repository.py \
  backend/tests/test_observability_repository.py
git commit -m "feat: expose Session message times"
```

---

### Task 5: Add safe Markdown rendering

**Files:**
- Modify: `webui/package.json`
- Modify: `webui/package-lock.json`
- Create: `webui/src/components/MessageMarkdown.tsx`
- Create: `webui/src/messagePresentation.test.tsx`
- Modify: `webui/src/components/TurnCard.tsx`

**Interfaces:**
- Consumes: raw source question/answer strings.
- Produces: `<MessageMarkdown content: string>` with GFM and no raw HTML execution.

- [ ] **Step 1: Write failing renderer tests**

Create `messagePresentation.test.tsx` with server-render assertions for headings, lists, tables, fenced code, and raw HTML. The security case must assert the literal script text remains non-executable and the markup contains no `<script>` element:

```tsx
const html = renderToStaticMarkup(
  <MessageMarkdown content={'## 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n<script>alert("x")</script>'} />,
);
expect(html).toContain("<h2>标题</h2>");
expect(html).toContain("<table>");
expect(html).not.toContain("<script>");
```

- [ ] **Step 2: Run the new test and verify RED**

```bash
cd webui
npm test -- src/messagePresentation.test.tsx
```

Expected: import failure because `MessageMarkdown` does not exist.

- [ ] **Step 3: Install only the approved Markdown dependencies**

```bash
npm install react-markdown@^10.1.0 remark-gfm@^4.0.1
```

Do not install `rehype-raw`.

- [ ] **Step 4: Implement the shared renderer**

Create:

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MessageMarkdown({ content }: { content: string }) {
  return <div className="message-markdown">
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ node: _node, ...props }) => <a {...props} rel="noreferrer noopener" />,
      }}
    >{content}</ReactMarkdown>
  </div>;
}
```

Replace the question and answer `<p>` elements in `TurnCard` with this component while keeping current empty-content copy outside the Markdown renderer.

- [ ] **Step 5: Run focused tests and commit**

```bash
npm test -- src/messagePresentation.test.tsx src/trace.test.tsx
cd ..
git add webui/package.json webui/package-lock.json \
  webui/src/components/MessageMarkdown.tsx webui/src/components/TurnCard.tsx \
  webui/src/messagePresentation.test.tsx
git commit -m "feat: render Session messages as safe Markdown"
```

---

### Task 6: Display separate message timestamps and responsive Markdown styles

**Files:**
- Create: `webui/src/messageTime.ts`
- Modify: `webui/src/messagePresentation.test.tsx`
- Modify: `webui/src/components/MessageMarkdown.tsx`
- Modify: `webui/src/components/TurnCard.tsx`
- Modify: `webui/src/types.ts`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes: Task 4 API fields.
- Produces: `formatMessageTime(value, status): { label: string; dateTime?: string }` and two independent `<time>` displays.

- [ ] **Step 1: Write failing formatter and TurnCard tests**

Add tests for exact, estimated, missing, invalid, and timezone conversion:

```ts
expect(formatMessageTime("2026-07-29T07:28:32Z", "exact").label)
  .toBe("7月29日 15:28:32");
expect(formatMessageTime("2026-07-29T07:28:32Z", "estimated").label)
  .toBe("约 7月29日 15:28:32");
expect(formatMessageTime(null, "unavailable").label).toBe("时间未记录");
expect(formatMessageTime("invalid", "exact").label).toBe("时间未记录");
```

Render a Turn whose question and answer differ by five seconds and assert both labels and both `datetime` values occur.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
cd webui
npm test -- src/messagePresentation.test.tsx src/trace.test.tsx
```

Expected: formatter missing and `TurnDetail` missing fields.

- [ ] **Step 3: Add frontend types and formatter**

Add `MessageTimeStatus` and the four fields to `TurnDetail`. Implement `formatMessageTime` with `Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: "Asia/Shanghai" })`, returning unavailable for invalid dates and prefixing only estimated labels with `约 `.

- [ ] **Step 4: Place times in each message heading**

Refactor each `.message-block` to contain a `.message-label` with the existing role label and its own `<time>`. Keep sender identity below the user label and above the Markdown body. Do not reuse `created_at` as an invisible fallback in the component.

- [ ] **Step 5: Add scoped styles and style contracts**

Add styles for `.message-label`, `.message-time`, and `.message-markdown`. Ensure:

```css
.message-markdown { min-width: 0; overflow-wrap: anywhere; }
.message-markdown pre,
.message-markdown .table-scroll { max-width: 100%; overflow-x: auto; }
.message-markdown > :first-child { margin-top: 0; }
.message-markdown > :last-child { margin-bottom: 0; }
```

Style headings, paragraphs, lists, blockquotes, tables, links, inline code, and fenced code under `.message-markdown` only. Update `MessageMarkdown` with `table: ({ node: _node, ...props }) => <div className="table-scroll"><table {...props} /></div>` so overflow remains inside the message card. Add `styles.test.ts` assertions for scoped overflow and the mobile `.message-block` layout.

- [ ] **Step 6: Run WebUI focused and full verification**

```bash
npm test -- src/messagePresentation.test.tsx src/trace.test.tsx src/styles.test.ts
npm test -- --run
npm run build
```

Expected: all tests pass and Vite production build exits zero.

- [ ] **Step 7: Commit timestamp UI**

```bash
cd ..
git add webui/src/messageTime.ts webui/src/messagePresentation.test.tsx \
  webui/src/components/MessageMarkdown.tsx webui/src/components/TurnCard.tsx webui/src/types.ts \
  webui/src/styles.css webui/src/styles.test.ts
git commit -m "feat: show Session message timestamps"
```

---

### Task 7: Apply migrations, synchronize data, and verify real sources

**Files:**
- No new source files; this task applies already-reviewed migrations and uses existing deployment scripts.

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: upgraded local Platform DB, upgraded FAE/ADMIN source DBs, refreshed snapshots, and real API evidence.

- [ ] **Step 1: Back up and migrate AI FAE before collector deployment**

Use the existing release flow so migrations run lexically and `.env.production` remains external:

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
bash deploy/scripts/release_prod.sh --prepare-only
bash deploy/scripts/release_prod.sh
```

Require the script's backup, migration, smoke, and health gates to pass. Do not use manual file-by-file upload.

- [ ] **Step 2: Deploy AI ADMIN atomically**

Build one archive from the verified commit and transfer it:

```bash
cd /Users/neo/Developer/work/AI-ADMIN-Agent
admin_release="ai-admin-message-times-$(git rev-parse --short=12 HEAD)"
git archive --format=tar.gz --output="/tmp/${admin_release}.tar.gz" HEAD
scp -i /Users/neo/.ssh/orbbec_aliyun_ed25519 \
  "/tmp/${admin_release}.tar.gz" root@47.106.112.69:/tmp/
```

On the server, keep the three old processes running while files are staged, preserve `.env`, `.env.dingtalk`, `.venv`, `data`, and `backups`, then migrate before restart:

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 \
  root@47.106.112.69 "admin_release=${admin_release} bash -s" <<'REMOTE'
backup_dir="/opt/ai-admin-agent/backups/$(date +%Y%m%dT%H%M%S)-message-times-predeploy"
stage_dir="/opt/ai-admin-agent/releases/${admin_release}"
mkdir -p "$backup_dir" "$stage_dir"
rsync -a --exclude='.venv/' --exclude='.env' --exclude='.env.dingtalk' \
  --exclude='data/' --exclude='backups/' --exclude='releases/' \
  /opt/ai-admin-agent/ "$backup_dir/"
tar -xzf "/tmp/${admin_release}.tar.gz" -C "$stage_dir"
rsync -a --delete --exclude='.venv/' --exclude='.env' --exclude='.env.dingtalk' \
  --exclude='data/' --exclude='backups/' --exclude='releases/' \
  "$stage_dir/" /opt/ai-admin-agent/
set -a
. /opt/ai-admin-agent/.env
set +a
/opt/ai-admin-agent/.venv/bin/python - <<'PY'
import os
from pathlib import Path
import psycopg

sql = Path("/opt/ai-admin-agent/migrations/008_message_timestamps.sql").read_text()
with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    connection.execute(sql)
PY
systemctl restart ai-admin-agent ai-admin-job-worker ai-admin-dingtalk-bot
systemctl is-active ai-admin-agent ai-admin-job-worker ai-admin-dingtalk-bot
curl -fsS http://127.0.0.1:8011/admin/health
REMOTE
```

Expected: all three service states are `active` and health returns HTTP 200. If restart or health fails, rsync `backup_dir` back with the same excludes and restart all three services; the additive nullable migration does not require rollback.

- [ ] **Step 3: Apply the Platform migration twice**

Resolve the existing sync-writer database URL from Keychain without printing it and run:

```bash
platform_sync_database_url="$(security find-generic-password \
  -a neo -s platform-sync-database-url -w)"
psql "$platform_sync_database_url" -X -v ON_ERROR_STOP=1 \
  -f backend/migrations/004_session_message_timestamps.sql
psql "$platform_sync_database_url" -X -v ON_ERROR_STOP=1 \
  -f backend/migrations/004_session_message_timestamps.sql
```

Expected: both executions succeed.

- [ ] **Step 4: Run one FAE and one ADMIN synchronization**

```bash
cd backend
.venv/bin/python -m app.sync_remote.cli fae
.venv/bin/python -m app.sync_remote.cli admin
```

Expected: both return `status=succeeded`, validation counts remain clean, and no corrected-answer or linkage validation regresses.

- [ ] **Step 5: Query source coverage without exposing message content**

Run aggregate-only SQL proving:

```sql
select source_kind, question_time_status, answer_time_status, count(*)
from platform_read.turns
group by source_kind, question_time_status, answer_time_status
order by source_kind, question_time_status, answer_time_status;
```

Expected: MetaBot completed Turns are exact/exact and legacy FAE/ADMIN are estimated/estimated. If a genuine post-deploy FAE or ADMIN Turn already exists, verify it is exact/exact; otherwise verify the nullable source columns and collector tests without injecting a synthetic production conversation.

- [ ] **Step 6: Restart Platform and verify real API data**

```bash
launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform
curl -sS http://127.0.0.1:8000/api/health
```

Query the user-provided encoded Session API and assert every completed Turn has distinct `question_at` and `answer_at` with exact statuses. Query one FAE and one ADMIN Session and assert legacy statuses are estimated.

---

### Task 8: Final verification and repository delivery

**Files:**
- No additional files unless verification exposes a regression, in which case return to the owning task's RED/GREEN cycle.

**Interfaces:**
- Consumes: the complete implementation and deployed data path.
- Produces: verified commits and pushed branches without unrelated dirty files.

- [ ] **Step 1: Run all three project test gates**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest tests/unit -q

cd /Users/neo/Developer/work/AI-ADMIN-Agent
.venv/bin/python -m pytest tests/unit -q

cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/pytest -q

cd ../webui
npm test -- --run
npm run build
```

Expected: every command exits zero.

- [ ] **Step 2: Run security and content checks**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
rg -n 'dangerouslySetInnerHTML|rehype-raw' webui/src webui/package.json
```

Expected: no production matches. Verify the built JS contains `时间未记录` and does not contain raw source credentials or user identifiers.

- [ ] **Step 3: Verify repository isolation**

For each repository, compare `git status --short` against the baseline recorded before work. Confirm only pre-existing user-owned changes remain, and inspect each task commit with `git show --stat`.

- [ ] **Step 4: Push only approved branches**

Push the Platform `master` branch and the existing approved source branches without force-push. If either source repository's deployment branch differs from its working branch, stop and use its established release integration workflow rather than silently pushing to `master`.

- [ ] **Step 5: Report evidence**

Report commit hashes for all three repositories, exact test counts, migration/sync results, Platform health, and timestamp status counts. Include the working Session URL and state explicitly which historical times are estimated.
