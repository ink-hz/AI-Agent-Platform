import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
MISSION_SCHEMA_MIGRATION = (
    ROOT / "backend" / "control_migrations" / "029_agent_brain_mvp.sql"
)
LATEST_AGENT_BRAIN_MIGRATION = (
    ROOT / "backend" / "control_migrations" / "032_content_key_canaries.sql"
)


def test_compose_keeps_brain_opt_in_and_secret_files_private() -> None:
    compose = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    api = compose["services"]["platform-api"]
    environment = api["environment"]

    assert environment["PLATFORM_EXECUTION_RELAY_ENABLED"] == "1"
    assert environment["PLATFORM_AGENT_BRAIN_ENABLED"] == "${PLATFORM_AGENT_BRAIN_ENABLED:-0}"
    assert environment["PLATFORM_CONTROL_DATABASE_URL_FILE"] == "/run/secrets/control-database-url"
    assert environment["PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"] == "/run/secrets/content-encryption-keyring"
    assert api["volumes"] == ["platform-api-secrets:/run/secrets:ro"]

    for name, service in compose["services"].items():
        if name == "platform-loopback":
            assert service.get("ports") == ["127.0.0.1:8080:8080"]
        else:
            assert "ports" not in service


def test_remote_stage_requires_mode_0600_control_content_and_feature_state() -> None:
    stage = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    for secret in (
        "control-database-url",
        "control-audit-database-url",
        "content-encryption-keyring",
        "execution-worker-public-keyring.json",
    ):
        assert secret in stage
    assert "stat -c '%a %U'" in stage
    assert '"600 root"' in stage
    assert '"600 10001"' in stage
    assert "PLATFORM_EXECUTION_RELAY_ENABLED=1" in stage
    assert 'PLATFORM_AGENT_BRAIN_ENABLED="${PLATFORM_AGENT_BRAIN_ENABLED:-0}"' in stage
    assert '[[ "$PLATFORM_AGENT_BRAIN_ENABLED" == "0" || "$PLATFORM_AGENT_BRAIN_ENABLED" == "1" ]] || fail' in stage


def test_formal_nginx_is_dingtalk_only_and_stream_safe() -> None:
    nginx = (CLOUD / "agent-domain.nginx.conf").read_text(encoding="utf-8")

    assert 'auth_basic "Orbbec Agent Platform";' not in nginx
    assert "auth_basic_user_file" not in nginx
    assert "limit_except GET HEAD OPTIONS" not in nginx
    assert "proxy_buffering off;" in nginx
    assert "proxy_cache off;" in nginx
    assert "proxy_read_timeout 330s;" in nginx
    assert "proxy_send_timeout 330s;" in nginx
    assert 'Content-Security-Policy "default-src \'none\'' in nginx
    assert 'Permissions-Policy "camera=(), microphone=(), geolocation=()"' in nginx
    assert "listen 80;" in nginx
    assert "listen 443 ssl;" in nginx
    assert "listen 8080" not in nginx
    assert "error_log /var/log/nginx/ai-fae-agent.error.log crit;" in nginx


def test_deploy_preserves_exact_fae_identity_configuration_and_routes() -> None:
    stage = (CLOUD / "remote-stage.sh").read_text(encoding="utf-8")

    for fact in (
        "fae_container_id",
        "fae_image",
        "fae_image_id",
        "fae_started_at",
        "fae_restart_count",
        "fae_config_digest",
        "fae_mounts_digest",
        "fae_health_digest",
        "fae_ip_digest",
        "fae_domain_digest",
    ):
        assert stage.count(fact) >= 2
    for forbidden in (
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "docker rm ai-fae-backend",
        "docker compose down",
    ):
        assert forbidden not in stage


def test_cloud_deploy_and_brain_actions_share_one_atomic_remote_lock() -> None:
    deploy = (CLOUD / "deploy.sh").read_text(encoding="utf-8")
    accept = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    lock = "/opt/orbbec-agent-platform/private/agent-brain-action.lock"
    assert lock in deploy
    assert lock in accept
    assert deploy.index(
        "if ! run_remote_operation acquire_agent_brain_action_lock; then"
    ) < deploy.index('/usr/bin/python3 - acquire "$release_sha" "$deployment_id"')
    assert "release_agent_brain_action_lock" in deploy
    assert "mkdir -m 700" in deploy
    assert "mkdir -m 700" in accept


def test_acceptance_is_private_real_idempotent_and_rollback_safe() -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    for required in (
        "AGENT_BRAIN_ACCEPTANCE_OK",
        "AGENT_BRAIN_ROLLBACK_OK",
        "remote_feature 1",
        "remote_feature 0",
        "/api/v1/brain/missions",
        "/api/v1/brain/missions/",
        "/api/v1/agents/marketing-gtm-bot/missions",
        "Idempotency-Key",
        "X-CSRF-Token",
        "mission.started",
        "task.dispatched",
        "agent.accepted",
        "agent.result",
        "mission.completed",
        "mission.interrupted",
        "platform_control.mission_runs",
        "platform_control.mission_events",
        "launchctl bootout",
        "launchctl bootstrap",
        "127.0.0.1:9110",
        "fae.orbbec.com.cn",
        "http://47.106.112.69/",
        "/admin/sessions",
        "/admin/review",
        "/admin/activity",
        "__Host-platform_csrf",
        "Origin: https://agent.orbbec.com.cn",
        "restore_feature",
        "accept_failure_rollback",
        "restore_worker",
        "brain-heading",
        "/api/sessions?limit=1",
        "/api/review/overview?agent_id=hr-bot",
        "/api/operations/brief",
        "fae_mounts",
        "fae_domain_hash",
        "fae_legacy_ip_hash",
        "metabot_release_sha",
        "agent_team_release_sha",
        "local_listener_table",
        "relay_acceptance_config",
        "AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7",
        "agent-brain-action.lock",
        "acquire_action_lock",
        "release_action_lock",
        "acceptance_status=complete",
    ):
        assert required in script
    for forbidden in (
        "set -x",
        "security ",
        "read -s",
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "drop table",
        "delete from platform_control.missions",
    ):
        assert forbidden not in script.lower()
    assert "/bin/sleep 5" in script
    assert "/bin/sleep 1" not in script
    assert "restore_nginx" in script
    assert "trap restore_nginx ERR EXIT" in script
    assert "--headless=new" in script
    assert "document.querySelectorAll('.message-markdown')" in script
    assert "MARKDOWN_RENDER_OK" in script
    assert "show HEAD:deploy/cloud/agent-domain.nginx.conf" in script
    assert "MANIFEST.sha256" in script
    assert '-H "X-CSRF-Token: $member_csrf"' not in script
    assert '-H "X-CSRF-Token: $owner_csrf"' not in script
    assert "  release)" in script
    assert "  enable)" not in script
    assert "platform_control.child_runs" not in script
    assert "evidence_generation" in script
    assert "evidence_previous" in script
    assert "launchctl print" in script


def test_acceptance_sql_uses_the_real_migration_029_run_table() -> None:
    migration = MISSION_SCHEMA_MIGRATION.read_text(encoding="utf-8")
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    assert "create table platform_control.mission_runs" in migration
    assert "create table platform_control.child_runs" not in migration
    assert "platform_control.mission_runs" in script
    assert "platform_control.child_runs" not in script
    assert "phase in ('professional','direct')" in script


def test_task9_rollback_pins_the_latest_agent_brain_migration() -> None:
    runbook = (ROOT / "docs" / "runbooks" / "cloud-platform.md").read_text(
        encoding="utf-8"
    )
    plan = (
        ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-22-agent-brain-minimum-use-entry.md"
    ).read_text(encoding="utf-8")
    task9 = plan.split("### Task 9:", 1)[1]

    assert LATEST_AGENT_BRAIN_MIGRATION.is_file()
    assert "Do not drop migration 032" in runbook
    assert "Do not drop migration 032" in task9


def test_enable_failure_executes_real_fail_closed_brain_and_lock_cleanup(
    tmp_path: Path,
) -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    function = "enable_with_rollback() {" + script.split(
        "enable_with_rollback() {", 1
    )[1].split("\n}\n\ncase \"$action\"", 1)[0] + "\n}\n"
    log = tmp_path / "calls"
    shell = f"""set -eEuo pipefail
log={str(log)!r}
local_runtime_preflight() {{ echo preflight >> "$log"; }}
remote_feature() {{ echo "feature:$1" >> "$log"; }}
run_relay_canary() {{ echo relay >> "$log"; return 1; }}
publish_formal_nginx() {{ echo publish >> "$log"; }}
release_action_lock() {{ echo release >> "$log"; }}
{function}
enable_with_rollback
"""

    result = subprocess.run(["/bin/bash", "-c", shell], text=True, capture_output=True)

    assert result.returncode == 1
    assert log.read_text(encoding="utf-8").splitlines() == [
        "preflight",
        "feature:0",
        "relay",
        "feature:0",
        "release",
    ]


@pytest.mark.parametrize("initially_loaded", [False, True])
def test_worker_restore_executes_real_loaded_and_unloaded_paths(
    tmp_path: Path, initially_loaded: bool
) -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    function = "restore_worker() {" + script.split("  restore_worker() {", 1)[1].split(
        "\n  }\n  cleanup_accept_resources()", 1
    )[0] + "\n}\n"
    log = tmp_path / "calls"
    fake_sudo = tmp_path / "sudo"
    fake_nc = tmp_path / "nc"
    fake_sudo.write_text(
        "#!/bin/bash\n"
        "echo \"$*\" >> \"$HARNESS_LOG\"\n"
        "if [[ \"$*\" == *\"launchctl print\"* ]]; then "
        "[[ \"$WORKER_INITIALLY_LOADED\" == 1 ]]; fi\n",
        encoding="utf-8",
    )
    fake_nc.write_text(
        "#!/bin/bash\necho \"nc:$*\" >> \"$HARNESS_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    fake_sudo.chmod(0o700)
    fake_nc.chmod(0o700)
    function = function.replace("/usr/bin/sudo", str(fake_sudo)).replace(
        "/usr/bin/nc", str(fake_nc)
    )
    shell = f"""set -eEuo pipefail
worker_stopped=1
agentops_uid=501
worker_label=com.orbbec.agent-execution-worker
worker_plist=/private/worker.plist
{function}
restore_worker
[[ "$worker_stopped" == 0 ]]
"""

    result = subprocess.run(
        ["/bin/bash", "-c", shell],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HARNESS_LOG": str(log),
            "WORKER_INITIALLY_LOADED": "1" if initially_loaded else "0",
        },
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert ("launchctl bootout" in calls) is initially_loaded
    assert "launchctl bootstrap" in calls
    assert "launchctl enable" in calls
    assert "launchctl kickstart -k" in calls
    assert "nc:-z -w 2 127.0.0.1 9120" in calls


def test_runbook_pins_dependency_order_evidence_and_non_destructive_rollback() -> None:
    runbook = (ROOT / "docs" / "runbooks" / "cloud-platform.md").read_text(
        encoding="utf-8"
    )

    ordered = (
        "migrations with Brain disabled",
        "local `agent-brain-bot`",
        "Worker allowlist and key registration",
        "cloud image with Brain disabled",
        "relay canary",
        "enable Brain",
        "switch `/`",
    )
    positions = [runbook.index(item) for item in ordered]
    assert positions == sorted(positions)
    for required in (
        "mode `0600`",
        "real DingTalk test member",
        "pre-created `hr-bot` grant",
        "Mission IDs",
        "event sequences",
        "worker key ID",
        "container IDs and start times",
        "Do not record prompts, answers, cookies, DingTalk IDs, or secrets",
        "Do not drop migration 032",
        "Do not delete Mission data",
        "FAE container identity",
        "separate FAE domain/IP Nginx routes remain byte-for-byte",
        "only the Agent Platform server block is intentionally replaced",
        "Stale lock recovery",
        "do not hold either lock open",
        "record the owner token",
        "deployment pointers and Brain feature state",
    ):
        assert required in runbook
