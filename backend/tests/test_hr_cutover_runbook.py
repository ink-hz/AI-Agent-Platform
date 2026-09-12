"""Run the documented maintenance Python against disposable PostgreSQL.

Only secret-file resolution is substituted; SQL, roles, locks and transactions
are real. No Docker command or production connection is executed.
"""
import re
from pathlib import Path
from time import monotonic
from uuid import uuid4

import psycopg
import pytest
from app import local_secrets
from app.hr_agent.cutover import ADVISORY_LOCK_KEY
from tests.hr_agent_support import hr_agent_database

RUNBOOK = Path(__file__).parents[2] / "docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md"


@pytest.fixture(scope="module")
def database():
    with hr_agent_database(cutover_phase="legacy") as db:
        yield db


def documented_script(action):
    scripts = re.findall(r"python - <<'PY'\n(.*?)\nPY", RUNBOOK.read_text(), re.DOTALL)
    matches = [script for script in scripts if f"# HR_CUTOVER_{action.upper()}\n" in script]
    assert len(matches) == 1, f"missing unique executable {action} maintenance command"
    return compile(matches[0], str(RUNBOOK), "exec")


def run_documented(action, database, monkeypatch, request_id, target="draining_legacy"):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    values = conninfo_to_dict(database.dsn)
    values["user"] = "platform_control_maintenance"
    dsn = make_conninfo(**values)

    def secret(path):
        assert path == "/run/control-secrets/control-maintenance-database-url"
        return dsn

    monkeypatch.setattr(local_secrets, "read_secret_file", secret)
    monkeypatch.setenv("HR_CUTOVER_REQUEST_ID", str(request_id))
    monkeypatch.setenv("HR_CUTOVER_TARGET_PHASE", target)
    exec(documented_script(action), {"__name__": "__main__"})  # noqa: S102 - executes the checked-in runbook against the test database


def test_documented_count_runs_real_query_in_read_only_bounded_transaction(database, monkeypatch, capsys):
    original = psycopg.connect
    observed = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def transaction(self):
            return self.connection.transaction()

        def execute(self, query, *args, **kwargs):
            if "hr_execution_cutover_counts_v102()" in query:
                observed.append(tuple(self.connection.execute(
                    "select current_user, current_setting('transaction_read_only'), "
                    "current_setting('lock_timeout'), current_setting('statement_timeout')"
                ).fetchone()))
            return self.connection.execute(query, *args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Connection(original(*a, **k)))
    run_documented("count", database, monkeypatch, uuid4())
    assert observed == [("platform_control_maintenance", "on", "2s", "3s")]
    assert '"legacy_nonterminal": 0' in capsys.readouterr().out


@pytest.mark.parametrize("action", ["initialize", "transition"])
def test_documented_mutation_lock_timeout_leaves_gate_and_operation_unchanged(database, monkeypatch, action):
    request_id = uuid4()
    with database.admin_connection() as blocker:
        before = blocker.execute("select phase,epoch,row_version from platform_control.hr_execution_cutover").fetchone()
        blocker.execute("select pg_advisory_xact_lock_shared(%s)", (ADVISORY_LOCK_KEY,))
        started = monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            run_documented(action, database, monkeypatch, request_id)
        assert monotonic() - started < 10
        assert blocker.execute("select phase,epoch,row_version from platform_control.hr_execution_cutover").fetchone() == before
        assert blocker.execute(
            "select count(*) from platform_control.hr_execution_cutover_operations where request_id=%s", (request_id,)
        ).fetchone() == (0,)


def test_documented_count_lock_timeout_is_error_not_zero(database, monkeypatch, capsys):
    with database.admin_connection() as blocker:
        blocker.execute("lock table platform_control.execution_jobs in access exclusive mode")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            run_documented("count", database, monkeypatch, uuid4())
    assert capsys.readouterr().out == ""


def test_documented_initialize_replay_prints_original_receipt(database, monkeypatch, capsys):
    import json

    with database.admin_connection() as observer:
        request_id, epoch = observer.execute(
            "select request_id,result_epoch from platform_control.hr_execution_cutover_operations "
            "where target_phase='legacy'"
        ).fetchone()
    run_documented("initialize", database, monkeypatch, request_id)
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["phase"] == "legacy"
    assert receipt["epoch"] == epoch
    assert receipt["transition_request_id"] == str(request_id)


def test_documented_transition_executes_once_and_replay_preserves_epoch(database, monkeypatch, capsys):
    request_id = uuid4()
    with database.admin_connection() as observer:
        before = observer.execute("select epoch from platform_control.hr_execution_cutover").fetchone()[0]
    run_documented("transition", database, monkeypatch, request_id)
    run_documented("transition", database, monkeypatch, request_id)
    with database.admin_connection() as observer:
        assert observer.execute("select phase,epoch from platform_control.hr_execution_cutover").fetchone() == ("draining_legacy", before + 1)
        assert observer.execute(
            "select count(*) from platform_control.hr_execution_cutover_operations where request_id=%s", (request_id,)
        ).fetchone() == (1,)
    assert capsys.readouterr().out.count('"phase": "draining_legacy"') == 2



def test_held_exclusive_slow_count_times_out_before_non_hr_append_limit(monkeypatch):
    """Instrument only local count latency; execute real count/transition/append."""
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    from app.agent_brain.conversation_repository import ConversationRepository
    from test_agent_brain_conversation_repository import _codec

    with hr_agent_database(cutover_phase="legacy") as db:
        owner, request = uuid4(), uuid4()
        with db.admin_connection() as c:
            c.execute("insert into platform_control.internal_users(internal_user_id,display_name,status) values(%s,'Synthetic non-HR','active')", (owner,))
            c.execute("select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)", (uuid4(),))
            before = c.execute("select phase,epoch,row_version from platform_control.hr_execution_cutover").fetchone()
            c.execute("alter function platform_control.hr_execution_cutover_counts_v102() rename to audit_original_count")
            c.execute("""create function platform_control.hr_execution_cutover_counts_v102()
                returns table(legacy_nonterminal bigint,cloud_nonterminal bigint)
                language plpgsql security definer set search_path=pg_catalog,platform_control
                as $$ begin
                    return query select * from platform_control.audit_original_count();
                    perform pg_sleep(6);
                end $$""")
            c.execute("alter function platform_control.hr_execution_cutover_counts_v102() owner to platform_control_owner")
            c.execute("revoke all on function platform_control.hr_execution_cutover_counts_v102() from public")
            c.execute("grant execute on function platform_control.hr_execution_cutover_counts_v102() to platform_control_maintenance")
        repo = ConversationRepository(db.dsn, content_codec=_codec())
        shell = repo.ensure_direct_conversation_shell(owner, uuid4(), direct_agent_id="fae-bot", title="Non-HR continuity")
        with ThreadPoolExecutor(max_workers=2) as pool:
            transition = pool.submit(run_documented, "transition", db, monkeypatch, request, "cloud")
            # Observe the granted exclusive lock and actual in-function sleep;
            # waiting for another lock is not the risk reproduced here.
            deadline = monotonic() + 2
            with db.admin_connection() as observer:
                while monotonic() < deadline:
                    locked = observer.execute("""select exists(
                        select 1 from pg_locks l join pg_stat_activity a on a.pid=l.pid
                        where l.locktype='advisory' and l.mode='ExclusiveLock' and l.granted
                          and a.usename='platform_control_maintenance' and a.wait_event='PgSleep')""").fetchone()[0]
                    if locked:
                        break
                    sleep(.02)
                assert locked
            started = monotonic()
            append = pool.submit(repo.append_turn, owner, shell.conversation_id, uuid4(), "Non-HR append survives maintenance")
            with db.admin_connection() as observer:
                blocked = False
                deadline = monotonic() + 1
                while monotonic() < deadline:
                    blocked = observer.execute("""select exists(
                        select 1 from pg_locks l join pg_stat_activity a on a.pid=l.pid
                        where l.locktype='advisory' and l.mode='ShareLock' and not l.granted
                          and a.usename='platform_control_app'
                          and l.classid=%s and l.objid=%s)""",
                        (ADVISORY_LOCK_KEY >> 32, ADVISORY_LOCK_KEY & 0xffffffff),
                    ).fetchone()[0]
                    if blocked:
                        break
                    sleep(.02)
                assert blocked and not append.done()
            with pytest.raises(psycopg.errors.QueryCanceled):
                transition.result(timeout=8)
            result = append.result(timeout=8)
            elapsed = monotonic() - started
            assert elapsed < 5
            assert result.turn.conversation_id == shell.conversation_id
        with db.admin_connection() as c:
            assert c.execute("select phase,epoch,row_version from platform_control.hr_execution_cutover").fetchone() == before
            assert c.execute("select count(*) from platform_control.hr_execution_cutover_operations where request_id=%s", (request,)).fetchone() == (0,)
        from hashlib import sha256
        from json import dumps
        from os import environ

        if directory := environ.get("HR_OPERATIONS_MOCK_RECEIPT_DIR"):
            path = Path(directory) / "real-postgres-slow-count.json"
            with path.open("x") as stream:
                stream.write(dumps({
                    "kind": "real-disposable-postgresql-not-production",
                    "runbookSha256": sha256(RUNBOOK.read_bytes()).hexdigest(),
                    "action": "transition", "target": "cloud", "statementTimeout": "3s",
                    "exclusiveLockObserved": locked, "nonHrSharedLockWaitObserved": blocked,
                    "appendElapsedSeconds": elapsed, "exception": "QueryCanceled",
                    "phaseEpochRowVersionUnchanged": list(before), "operationReceiptsAdded": 0,
                    "latencyInstrumentation": "original count executed then pg_sleep(6) inside disposable wrapper",
                }, indent=2))


def test_current_runbook_defers_legacy_restore_and_protects_docker():
    source = RUNBOOK.read_text()
    assert "# HR_LEGACY_RESTORE" not in source
    assert "restore-one" not in "\n".join(re.findall(r"```bash\n(.*?)```", source, re.DOTALL))
    assert "单条SQL上限3秒" in source
    for block in re.findall(r"```bash\n(.*?)```", source, re.DOTALL):
        if "/usr/bin/docker run " in block:
            assert "--cap-drop ALL" in block
            assert "--security-opt no-new-privileges:true" in block


def test_current_transition_command_cannot_execute_deferred_legacy_restore(database, monkeypatch):
    with pytest.raises(ValueError, match="invalid HR cutover target phase"):
        run_documented("transition", database, monkeypatch, uuid4(), "legacy")
