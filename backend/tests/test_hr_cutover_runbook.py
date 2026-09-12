"""Run the documented maintenance Python against disposable PostgreSQL.

Only secret-file resolution is substituted; SQL, roles, locks and transactions
are real. No Docker command or production connection is executed.
"""
import os
import re
import shlex
import subprocess
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


def run_documented(action, database, monkeypatch, request_id):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    values = conninfo_to_dict(database.dsn)
    values["user"] = "platform_control_maintenance"
    dsn = make_conninfo(**values)

    def secret(path):
        assert path == "/run/control-secrets/control-maintenance-database-url"
        return dsn

    monkeypatch.setattr(local_secrets, "read_secret_file", secret)
    monkeypatch.setenv("HR_CUTOVER_REQUEST_ID", str(request_id))
    monkeypatch.setenv("HR_CUTOVER_TARGET_PHASE", "draining_legacy")
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

        def execute(self, query, *args, **kwargs):
            if "hr_execution_cutover_counts_v102()" in query:
                observed.append(tuple(self.connection.execute(
                    "select current_user, current_setting('transaction_read_only'), "
                    "current_setting('lock_timeout'), current_setting('statement_timeout')"
                ).fetchone()))
            return self.connection.execute(query, *args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Connection(original(*a, **k)))
    run_documented("count", database, monkeypatch, uuid4())
    assert observed == [("platform_control_maintenance", "on", "2s", "30s")]
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


@pytest.mark.parametrize("scenario", ["restore_failure", "state_failure", "peers_changed", "success"])
def test_documented_legacy_restore_saves_only_after_instance_and_peer_checks(tmp_path, scenario):
    blocks = re.findall(r"```bash\n(.*?)```", RUNBOOK.read_text(), re.DOTALL)
    snippet = next(block for block in blocks if "# HR_LEGACY_RESTORE" in block)
    snippet = snippet.replace('sudo -n -H -u agentops /bin/bash "$OPS_PM2"', "mock_pm2")
    snippet = snippet.replace(
        "hr_restore_evidence=/Users/agentops/AgentRuntime/release-evidence/APPROVED_RESTORE",
        "hr_restore_evidence=" + shlex.quote(str(tmp_path)),
    )
    assert "sudo " not in snippet
    functions = r'''
mock_pm2() {
  printf '%s\n' "$1" >> "$MOCK_ROOT/calls"
  case "$1" in
    restore-one)
      test "$2" = /Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs || return 98
      test "$3" = metabot-hr && test "$4" = online || return 99
      test "$MOCK_SCENARIO" != restore_failure || return 2
      touch "$MOCK_ROOT/restored" ;;
    state-one)
      if test "$MOCK_SCENARIO" = state_failure; then printf 'stopped\n'; else printf 'online\n'; fi ;;
    snapshot-except)
      if test "$MOCK_SCENARIO" = peers_changed && test -f "$MOCK_ROOT/restored";
      then printf '{"peer":"changed"}\n'; else printf '{}\n'; fi ;;
    save) touch "$MOCK_ROOT/saved" ;;
    *) return 99 ;;
  esac
}
'''
    result = subprocess.run(
        ["bash", "-c", functions + snippet],
        env={**os.environ, "MOCK_ROOT": str(tmp_path), "MOCK_SCENARIO": scenario},
        capture_output=True, text=True, timeout=3, check=False,
    )
    assert (result.returncode == 0) == (scenario == "success")
    assert (tmp_path / "saved").exists() == (scenario == "success")
