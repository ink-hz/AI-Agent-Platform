from __future__ import annotations

import os
from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
ACCEPTANCE = ROOT / "deploy/local-execution-worker/accept.sh"
ORCHESTRATOR = ROOT / "backend/app/execution_relay/acceptance_orchestrator.py"
RUNBOOK = ROOT / "docs/runbooks/agent-execution-relay.md"
CLOUD_RUNBOOK = ROOT / "docs/runbooks/cloud-platform.md"
README = ROOT / "README.md"
SUCCESS = (
    "AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 "
    "public_ports_added=0 duplicate_dispatches=0"
)


def _acceptance() -> str:
    assert ACCEPTANCE.is_file()
    return ACCEPTANCE.read_text(encoding="utf-8")


def _orchestrator() -> str:
    assert ORCHESTRATOR.is_file()
    return ORCHESTRATOR.read_text(encoding="utf-8")


def _runbook() -> str:
    assert RUNBOOK.is_file()
    return RUNBOOK.read_text(encoding="utf-8")


def test_acceptance_wrapper_is_executable_noninteractive_and_stable() -> None:
    script = _acceptance()
    assert os.access(ACCEPTANCE, os.X_OK)
    assert script.startswith("#!/bin/bash\nset -euo pipefail\numask 077\n")
    assert '[[ $# -eq 1 && "$1" == /* ]] || fail' in script
    assert '"$(/usr/bin/id -un)" == "agentops"' in script
    assert "app.execution_relay.acceptance_orchestrator" in script
    assert "source " not in script and "read -p" not in script and "set -x" not in script
    assert script.count(SUCCESS) == 1
    assert script.rstrip().endswith(f'echo "{SUCCESS}"')


def test_orchestrator_executes_all_ten_gates_without_boolean_evidence() -> None:
    source = _orchestrator().lower()
    for number in range(1, 11):
        assert f"gate {number:02d}" in source
    for required in (
        "cloud_api_healthy",
        "cloud_database_healthy",
        "worker_heartbeat_fresh",
        "public_ports_added",
        "registered_public_key_sha256",
        "hr-bot",
        "marketing-intelligence-bot",
        "duplicate_reupload",
        "completion_crash",
        "dispatching_crash",
        "lease_status",
        "upload_status",
        "sessions_status",
        "history_status",
        "fae_external_domain_healthy",
        "management_replica_synchronization_unchanged",
    ):
        assert required in source
    assert "require_evidence" not in source
    assert "evidence[" not in source


def test_orchestrator_directly_enqueues_crashes_restarts_and_checks_idempotence() -> None:
    source = _orchestrator()
    lowered = source.lower()
    assert "acceptance_cli" in source and '"enqueue"' in source and '"inspect"' in source
    assert "dispatching-paused" in source and "completion-paused" in source
    assert "SIGKILL" in source and "launchctl" in source and "kickstart" in source
    assert "metabot_posts" in source and "duplicate_reupload" in source
    assert 'response["inserted"] != 0' in source
    assert "execution_worker.event_outbox" in source
    assert "execution_worker.local_runs" in source
    assert "Popen" in source
    assert "after local terminal" in lowered
    assert "after real metabot post" in lowered


def test_disposable_revocation_never_changes_production_worker_or_key() -> None:
    source = _orchestrator()
    assert "relay-acceptance-" in source
    assert "register_worker" in source
    assert '"register"' in source and '"revoke-worker"' in source
    assert "worker-v1" in source
    assert "agentops-mac-primary" in source
    assert "worker-v2" not in source
    assert "disposable_registered" in source
    assert "cleanup_failed" in source
    assert 'lease_status != 401' in source
    assert 'upload_status != 401' in source
    assert 'sessions_status != 200' in source
    assert 'history_status != 200' in source


def test_orchestrator_fails_closed_on_private_files_urls_and_remote_boundaries() -> None:
    source = _orchestrator()
    lowered = source.lower()
    for path in (
        "/Users/agentops/AgentRuntime/private/",
        "/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key",
        "/Users/agentops/AgentRuntime/private/execution-worker-postgres-dsn",
        "/Users/agentops/AgentRuntime/execution-worker-public.json",
    ):
        assert path in source
    assert "https://agent.orbbec.com.cn" in source
    assert "https://fae.orbbec.com.cn" in source
    assert "O_NOFOLLOW" in source and "O_DIRECTORY" in source
    assert "stat.S_ISREG" in source and "st_uid" in source
    assert "0o600" in source and "0o700" in source
    assert "BatchMode=yes" in source and "StrictHostKeyChecking=yes" in source
    assert "127.0.0.1:9120" in source and "9101-9108" in source
    assert "management_replica_synchronization_unchanged" in source
    for forbidden in (
        "/usr/bin/security",
        "keychain",
        "/usr/bin/sudo",
        "/usr/bin/su ",
        "osascript",
        "curl -k",
        "verify=false",
    ):
        assert forbidden not in lowered


def test_orchestrator_never_logs_secrets_and_uses_real_session_requests() -> None:
    source = _orchestrator()
    lowered = source.lower()
    assert "session_cookie_file" in source
    assert '"/api/sessions?limit=1"' in source
    assert '"/api/sessions/"' in source
    assert "private_key_base64" not in lowered
    assert "password=" not in lowered
    assert not re.search(r"\bprint\([^\n]*(key|cookie|token|dsn)", lowered)
    assert "stdout=subprocess.DEVNULL" in source
    assert "stderr=subprocess.DEVNULL" in source


def test_runbook_has_exact_operations_and_never_requeues_unknown_dispatch() -> None:
    runbook = _runbook()
    lowered = runbook.lower()
    for heading in (
        "status",
        "logs",
        "key rotation",
        "worker revocation",
        "backup",
        "restore",
        "stuck job",
        "explicit interruption",
        "restart",
        "rollback",
        "removal",
    ):
        assert heading in lowered
    for command in (
        "launchctl print",
        "launchctl kickstart -k",
        "register_worker add-key",
        "register_worker revoke-key",
        "register_worker revoke-worker",
        "pg_dump",
        "pg_restore",
        "agent_execution_worker",
        "execution_worker.event_outbox",
        "execution_worker.local_runs",
        "status='interrupted'",
    ):
        assert command in runbook
    assert "dispatching|dispatched|running" in runbook
    assert "never requeued automatically" in lowered
    assert "resume" in lowered and "same" in lowered and "outbox" in lowered
    assert "terminal" in lowered and "interrupted" in lowered


def test_runbook_cloud_maintenance_commands_use_deployed_container_boundary() -> None:
    runbook = _runbook()
    maintenance = runbook.split("## Key rotation", 1)[1].split("## Backup", 1)[0]
    assert "/opt/orbbec-agent-platform/private/control-maintenance-database-url" in maintenance
    assert "docker run --rm --pull=never" in maintenance
    assert "orbbec-agent-platform-internal" in maintenance
    assert "PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE" in maintenance
    assert "register_worker add-key" in maintenance
    assert "register_worker revoke-key" in maintenance
    assert "register_worker revoke-worker" in maintenance
    assert "-m 700" in maintenance and "-m 600" in maintenance
    assert "/run/worker-registration" in maintenance
    assert not re.search(r"(?m)^python -m app\.execution_relay\.register_worker", maintenance)


def test_runbook_limits_acceptance_cli_and_uses_controlled_production_cancel() -> None:
    interruption = _runbook().split("## Explicit interruption", 1)[1].split(
        "## Restart", 1
    )[0]
    lowered = interruption.lower()
    assert "PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ENABLED=1" in interruption
    assert "active acceptance environment" in lowered
    assert "request_cancel" in interruption and "cancel_requested" in interruption
    assert "ordinary production" in lowered
    assert "update platform_control.execution_jobs" not in lowered


def test_removal_requires_exact_confirmation_and_only_dedicated_objects() -> None:
    removal = _runbook().split("## Removal", 1)[1]
    lowered = removal.lower()
    assert "--confirm-remove-agent-execution-worker" in removal
    assert "drop database agent_execution_worker" in lowered
    for role in (
        "agent_execution_worker_runtime",
        "agent_execution_worker_migrator",
        "agent_execution_worker_owner",
    ):
        assert f"drop role {role}" in lowered
    for forbidden in (
        "drop database flywheel",
        "drop database postgres",
        "drop database template",
        "brew services",
        "pg_ctl stop",
        "launchctl unload postgresql",
        "rm -rf",
    ):
        assert forbidden not in lowered
    assert "never" in lowered and "flywheel" in lowered
    assert "postgresql service" in lowered
    assert "remove.sh" in removal
    assert "agent_execution_worker.dump" in removal


def test_docs_link_release_gate_and_keep_user_routes_disabled() -> None:
    runbook = _runbook()
    cloud = CLOUD_RUNBOOK.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert "agent-execution-relay.md" in cloud
    assert "agent-execution-relay.md" in readme
    assert SUCCESS in runbook
    assert "accept.sh" in runbook
    assert "Chat routes remain disabled" in runbook
    assert "Agent Brain routes remain disabled" in runbook
