import os
from pathlib import Path
import subprocess

import pytest
import yaml

from app.agent_brain.worker_runtime import tick, validate_worker_mode


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
MISSION_SCHEMA_MIGRATION = (
    ROOT / "backend" / "control_migrations" / "029_agent_brain_mvp.sql"
)
LATEST_AGENT_BRAIN_MIGRATION = (
    ROOT / "backend" / "control_migrations" / "041_agent_brain_durable_loop.sql"
)


def test_control_bootstrap_provisions_brain_worker_credentials() -> None:
    script = (CLOUD / "bootstrap-control-db.sh").read_text(encoding="utf-8")

    for required in (
        "platform_brain_worker",
        "platform_brain_worker_preview",
        "brain-worker-password",
        "preview-brain-worker-password",
        "brain-worker-database-url",
        "preview-brain-worker-database-url",
    ):
        assert required in script

    # The SQL is embedded in an expanding shell heredoc. PostgreSQL's
    # positional format markers must escape their dollar signs or `set -u`
    # treats `$I` / `$L` as unset shell variables during a production deploy.
    assert "%1\\$I" in script
    assert "%2\\$L" in script
    assert "%1$I" not in script
    assert "%2$L" not in script
    assert (
        'brain_dsn="postgresql://${brain_roles[$index]}:${brain_password}'
        '@platform-postgres:5432/${database_name}"'
    ) in script
    assert "legacy_brain_dsn" in script
    assert "brain-dsn-repair.part" in script


def test_compose_keeps_brain_opt_in_and_secret_files_private() -> None:
    compose = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    api = compose["services"]["platform-api"]
    environment = api["environment"]

    assert environment["PLATFORM_EXECUTION_RELAY_ENABLED"] == "1"
    assert environment["PLATFORM_DIRECT_AGENT_ENABLED"] == (
        "${PLATFORM_DIRECT_AGENT_ENABLED:-0}"
    )
    assert environment["PLATFORM_AGENT_BRAIN_ENABLED"] == "${PLATFORM_AGENT_BRAIN_ENABLED:-0}"
    assert environment["PLATFORM_CONTROL_DATABASE_URL_FILE"] == "/run/secrets/control-database-url"
    assert environment["PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"] == "/run/secrets/content-encryption-keyring"
    assert api["volumes"] == ["platform-api-secrets:/run/secrets:ro"]
    assert environment["PLATFORM_AGENT_BRAIN_V2_ENABLED"] == (
        "${PLATFORM_AGENT_BRAIN_V2_ENABLED:-0}"
    )

    worker = compose["services"]["platform-brain"]
    assert "ports" not in worker
    assert set(worker["networks"]) == {"platform-internal", "platform-edge"}
    assert worker["command"] == [
        "python", "-m", "app.agent_brain.worker_runtime", "all"
    ]
    assert worker["user"] == "10001:10001"
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert worker["security_opt"] == ["no-new-privileges:true"]
    assert worker["environment"]["PLATFORM_BRAIN_DATABASE_URL_FILE"] == (
        "/run/secrets/brain-worker-database-url"
    )
    assert worker["environment"]["PLATFORM_BRAIN_MODEL_MANIFEST"] == (
        "/app/brain-model.release.json"
    )
    assert worker["environment"]["PLATFORM_BRAIN_PROVIDER_API_KEY_FILE"] == (
        "/run/secrets/brain-provider-api-key"
    )
    assert worker["environment"]["PLATFORM_BRAIN_PROVIDER_BASE_URL"] == (
        "${PLATFORM_BRAIN_PROVIDER_BASE_URL:-https://cc.nexcor.ai}"
    )
    assert worker["environment"]["PLATFORM_BRAIN_PROVIDER_AUTH_SCHEME"] == (
        "${PLATFORM_BRAIN_PROVIDER_AUTH_SCHEME:-bearer}"
    )
    assert "PLATFORM_DINGTALK_APP_SECRET_FILE" not in worker["environment"]
    assert worker["volumes"] == ["platform-brain-secrets:/run/secrets:ro"]

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
        "brain-worker-database-url",
        "brain-provider-api-key",
    ):
        assert secret in stage
    assert "stat -c '%a %U'" in stage
    assert '"600 root"' in stage
    assert '"600 10001"' in stage
    assert "PLATFORM_EXECUTION_RELAY_ENABLED=1" in stage
    assert 'PLATFORM_AGENT_BRAIN_ENABLED="${PLATFORM_AGENT_BRAIN_ENABLED:-0}"' in stage
    assert '[[ "$PLATFORM_AGENT_BRAIN_ENABLED" == "0" ]] || fail' in stage
    assert 'PLATFORM_AGENT_BRAIN_V2_ENABLED="${PLATFORM_AGENT_BRAIN_V2_ENABLED:-0}"' in stage
    assert '[[ "$PLATFORM_AGENT_BRAIN_V2_ENABLED" == "0" ]] || fail' in stage
    assert 'PLATFORM_DIRECT_AGENT_ENABLED="${PLATFORM_DIRECT_AGENT_ENABLED:-1}"' in stage
    assert '[[ "$PLATFORM_DIRECT_AGENT_ENABLED" == "1" ]] || fail' in stage
    assert "orbbec-agent-platform-brain-secrets" in stage
    assert (
        "for service_name in platform-brain platform-loopback platform-api "
        "platform-directory platform-dingtalk-stream;"
    ) in stage


def test_direct_agent_runtime_is_started_without_enabling_brain_v1() -> None:
    source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    assert 'v1_mission_modes.append("direct_agent")' in source
    assert 'v1_mission_modes.append("brain")' in source
    assert "and not config.agent_brain_v2_enabled" in source
    assert "mission_modes=tuple(v1_mission_modes)" in source


def test_control_bootstrap_runs_execution_job_kind_preflight_before_migrations() -> None:
    bootstrap = (CLOUD / "bootstrap-control-db.sh").read_text(encoding="utf-8")
    helper = CLOUD / "preflight-execution-job-kind.sh"

    assert helper.is_file()
    assert helper.stat().st_mode & 0o111
    source = helper.read_text(encoding="utf-8")
    assert "default_transaction_read_only=on" in source
    assert "left join platform_control.mission_runs" in source
    assert "migration_042_orphan" in source
    assert "migration_042_unknown_phase" in source
    assert "EXECUTION_JOB_KIND_PREFLIGHT_OK" in source
    assert "EXECUTION_JOB_KIND_PREFLIGHT_FAILED" in source
    assert bootstrap.index("preflight-execution-job-kind.sh") < bootstrap.index(
        "python -m app.control_plane.migrate"
    )


def test_v2_cutover_has_exact_fail_closed_gates() -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    for gate in (
        "PROVIDER_PROBE=passed",
        "REFERENCE_RECOVERY=passed",
        "V1_NONTERMINAL_MISSIONS=0",
        "V2_MISSION_RUN_WRITES=0",
        "LOCAL_WORKER_ACCEPTS=metabot_local",
        "FAE_MANAGED_FILES_UNCHANGED=true",
    ):
        assert gate in script
    assert "PLATFORM_AGENT_BRAIN_V2_ENABLED" in script
    assert "provider-evidence.sha256" in script
    assert 'docker exec "$brain" cat -- "/tmp/$probe_name"' in script
    assert 'docker cp "$brain:/tmp/$probe_name"' not in script


def test_v2_rollback_stops_intake_without_rewriting_history() -> None:
    script = (CLOUD / "rollback-dingtalk-production.sh").read_text(
        encoding="utf-8"
    ).lower()

    assert "platform_agent_brain_v2_enabled=0" in script
    assert "platform-brain" in script
    assert "update platform_brain.brain_loops" not in script
    assert "delete from platform_brain" not in script
    assert "insert into platform_control.missions" not in script
    assert "metabot_local" in script
    assert "queued','leased','dispatched','running" in script
    assert "rollback_blocked_active_metabot_local" in script
    assert script.index("rollback_blocked_active_metabot_local") < script.index(
        'stop "${services_to_stop[@]}"'
    )


def test_private_worker_all_mode_runs_each_durable_lane_and_heartbeats() -> None:
    calls = []

    class Runtime:
        def advance_one(self):
            calls.append("brain")
            return True

        def scan_settled_batches(self):
            calls.append("settle")
            return 1

        def dispatch_one(self):
            calls.append("adapter")
            return True

        def reconcile_one(self):
            calls.append("reconcile:persistent")
            return True

        def reconcile_adapter_tasks(self, kind):
            calls.append(f"reconcile:{kind}")
            return 1

        def reconcile_cancellations(self):
            calls.append("cancel")
            return 1

    class Repository:
        def heartbeat(self, name, *, status, error_code=None):
            calls.append(f"heartbeat:{name}:{status}")

        def expire_leases(self, *, limit):
            calls.append("expire-steps")
            return 1

        def expire_delivery_leases(self, *, limit):
            calls.append("expire-deliveries")
            return 1

        def expire_waiting_users(self, *, limit):
            calls.append("expire-users")
            return 1

        def erase_expired_model_responses(self, *, limit):
            calls.append("erase-responses")
            return 1

    changed = tick(validate_worker_mode("all"), Runtime(), Repository())

    assert changed == 10
    assert calls == [
        "brain", "settle", "heartbeat:agent-brain-step:healthy",
        "adapter", "reconcile:persistent", "reconcile:metabot_local", "cancel",
        "heartbeat:agent-brain-adapter:healthy", "expire-steps",
        "expire-deliveries", "expire-users", "erase-responses",
        "heartbeat:agent-brain-reaper:healthy",
    ]


def test_api_process_does_not_start_v1_scheduler_when_v2_is_enabled() -> None:
    source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    guard = "and not config.agent_brain_v2_enabled"

    assert guard in source
    assert source.index(guard) < source.index(
        "asyncio.create_task(agent_brain_loop(agent_brain_orchestrator))"
    )


def test_catalog_router_is_mounted_outside_the_brain_repository_gate() -> None:
    source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    catalog_mount = source.index("build_agent_catalog_router(agent_use_authorization)")
    brain_gate = source.index("if mission_repository is not None")
    assert catalog_mount < brain_gate


def test_formal_nginx_keeps_platform_root_and_proxies_office_safely() -> None:
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
    assert "location = /api/v1/internal/session/subject" in nginx
    internal_block = nginx.split(
        "location = /api/v1/internal/session/subject", 1
    )[1].split("}", 1)[0]
    assert "return 404;" in internal_block
    assert "proxy_pass" not in internal_block
    assert "location = /office" in nginx
    assert "location = /office/health" in nginx
    assert "location ^~ /office/assets/" in nginx
    assert "location ^~ /office/knowledge-assets/" in nginx
    assert "location ^~ /office/" in nginx
    assert "proxy_pass http://127.0.0.1:8011;" in nginx
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert 'proxy_set_header Forwarded "";' in nginx
    assert 'proxy_set_header Authorization "";' in nginx
    assert "proxy_set_header Cookie" not in nginx
    assert "zone=ai_admin_office_chat:10m" in nginx
    assert "zone=ai_admin_office_conn:10m" in nginx
    assert nginx.count("location = /admin {") == 1
    assert nginx.count("location ^~ /admin/ {") == 1
    admin_boundary = nginx[
        nginx.index("location = /admin {"):nginx.index("location = /office {")
    ]
    assert "proxy_pass http://127.0.0.1:8080;" in admin_boundary
    assert "127.0.0.1:8011" not in admin_boundary


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
    assert stage.count("value=sorted(value") == 2
    assert "{{json .Mounts}}' ai-fae-backend | /usr/bin/sha256sum" not in stage


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
        "x-platform-entry-state: brain-preparing",
        "/api/v1/brain/missions",
        "/api/v1/brain/missions/",
        "/api/v1/agents/marketing-gtm-bot/conversations",
        "Idempotency-Key",
        "X-CSRF-Token",
        "conversation.started",
        "task.dispatched",
        "agent.accepted",
        "agent.result",
        "turn.completed",
        "mission.interrupted",
        "platform_control.mission_runs",
        "platform_control.mission_events",
        "worker-pm2.sh",
        '"$worker_supervisor" stop',
        '"$worker_supervisor" restore online',
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
    assert "location: /admin" not in script.lower()
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
    assert "launchctl" not in script


def test_acceptance_sql_uses_the_real_migration_029_run_table() -> None:
    migration = MISSION_SCHEMA_MIGRATION.read_text(encoding="utf-8")
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    assert "create table platform_control.mission_runs" in migration
    assert "create table platform_control.child_runs" not in migration
    assert "platform_control.mission_runs" in script
    assert "platform_control.child_runs" not in script
    assert "phase in ('professional','direct')" in script


def test_acceptance_proves_continuous_conversation_release_and_restore() -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    for required in (
        "/api/v1/conversations",
        "/api/v1/conversations/$conversation_id/messages",
        "/api/v1/conversations/$conversation_id/events?after=$first_last_seq",
        "/api/v1/agents/marketing-gtm-bot/conversations",
        "platform_control.conversation_turns",
        "platform_control.conversation_messages",
        "mission.conversation_id=:'conversation'::uuid",
        "first_turn_id",
        "second_turn_id",
        "first_mission_id",
        "second_mission_id",
        "conversation_id=%s",
        "turn_count=2",
        "message_count=4",
        "resume_duplicate_turns=0",
        "restore_conversation",
        "third_turn_id",
        "turn_count=3",
        "message_count=6",
        "/api/operations/conversation-metrics",
    ):
        assert required in script
    assert script.index("/api/v1/conversations\"") < script.index(
        "/api/v1/conversations/$conversation_id/messages\""
    )
    assert "delete from platform_control.conversations" not in script.lower()


def test_acceptance_keeps_member_diagnostics_hidden_and_office_available() -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    for forbidden_member_text in ("诊断详情", "hr-bot", "accepted", "/missions/"):
        assert forbidden_member_text in script
    assert "document.body.innerText" in script
    assert "https://agent.orbbec.com.cn/office/?view=services" in script
    assert "OFFICE_ROUTE_UNCHANGED" in script


def test_rollback_pins_every_deployed_agent_brain_migration() -> None:
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
    assert "Do not drop migrations\n032 through 038" in runbook
    assert "Do not drop migration 032 or 033" in task9


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


def test_worker_restore_executes_fixed_pm2_online_path(tmp_path: Path) -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    function = "restore_worker() {" + script.split("  restore_worker() {", 1)[1].split(
        "\n  }\n  cleanup_accept_resources()", 1
    )[0] + "\n}\n"
    log = tmp_path / "calls"
    fake_nc = tmp_path / "nc"
    fake_nc.write_text(
        "#!/bin/bash\necho \"nc:$*\" >> \"$HARNESS_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    fake_nc.chmod(0o700)
    function = function.replace("/usr/bin/nc", str(fake_nc))
    shell = f"""set -eEuo pipefail
worker_stopped=1
worker_supervisor=/fixed/worker-pm2.sh
run_agentops() {{ echo "$*" >> {str(log)!r}; }}
{function}
restore_worker
[[ "$worker_stopped" == 0 ]]
"""

    result = subprocess.run(
        ["/bin/bash", "-c", shell],
        text=True,
        capture_output=True,
        env={**os.environ, "HARNESS_LOG": str(log)},
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "/fixed/worker-pm2.sh restore online" in calls
    assert "launchctl" not in calls
    assert "nc:-z -w 2 127.0.0.1 9120" in calls


def test_brain_disabled_rollback_keeps_root_as_use_entry() -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    rollback_gate = script.split("rollback_headers=", 1)[1].split(
        "for owner_path in /admin", 1
    )[0]

    assert "'%{http_code}'" in rollback_gate
    assert '== "200"' in rollback_gate
    assert "x-platform-entry-state: brain-preparing" in rollback_gate.lower()
    assert "location: /admin" not in rollback_gate.lower()


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
        "Conversation/Turn/Mission/run IDs",
        "event sequences",
        "worker key ID",
        "container IDs and start times",
        "Do not record prompts, answers, cookies, DingTalk IDs, or secrets",
        "Do not drop migrations\n032 through 038",
        "Do not delete Conversation, Message, Turn, Mission, or run\ndata",
        "FAE container identity",
        "separate FAE domain/IP Nginx routes remain byte-for-byte",
        "only the Agent Platform server block is intentionally replaced",
        "Stale lock recovery",
        "do not hold either lock open",
        "record the owner token",
        "deployment pointers and Brain feature state",
    ):
        assert required in runbook
