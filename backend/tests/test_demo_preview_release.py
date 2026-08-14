from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
DEPLOY = CLOUD / "deploy-demo-preview.sh"
ACCEPT = CLOUD / "accept-demo-preview.sh"
ROLLBACK = CLOUD / "rollback-demo-preview.sh"
PREREQUISITES = CLOUD / "bootstrap-demo-preview-prerequisites.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "minimal-dingtalk-demo.md"

TARGET = "root@47.106.112.69"
SSH_KEY = "/Users/neo/.ssh/orbbec_aliyun_ed25519"
LIVE_NGINX_SHA = (
    "382d733e1a581569f4ceedd03ce24ab9113f61a595015bc0449e1319026c1e97"
)
PREFIX = "/_preview/dingtalk-r1/"

SECRET_FILES = {
    "dingtalk-app-key",
    "dingtalk-agent-id",
    "dingtalk-corp-id",
    "dingtalk-app-secret",
    "preview-control-database-url",
    "preview-control-audit-database-url",
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "preview-identity-hmac-keyring",
    "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring",
    "demo-userids",
}
OPERATOR_FILES = {
    "dingtalk-app-key",
    "dingtalk-agent-id",
    "dingtalk-corp-id",
    "dingtalk-app-secret",
    "demo-userids",
}


def _text(path: Path) -> str:
    assert path.is_file(), f"missing Task 4 artifact: {path}"
    return path.read_text(encoding="utf-8")


def test_release_scripts_have_fixed_non_secret_interface_and_target() -> None:
    deploy = _text(DEPLOY)
    accept = _text(ACCEPT)

    assert "[[ $# -eq 2 ]]" in deploy
    assert 'release_sha="$1"' in deploy
    assert 'archive_sha256="$2"' in deploy
    assert TARGET in deploy
    assert SSH_KEY in deploy
    assert LIVE_NGINX_SHA in deploy
    assert "[[ $# -eq 0 ]]" in accept
    assert "root@" not in accept
    assert "set -euo pipefail" in deploy and "set -euo pipefail" in accept
    assert "umask 077" in deploy and "umask 077" in accept


def test_deploy_refuses_dirty_source_and_builds_a_verified_immutable_archive() -> None:
    value = _text(DEPLOY)

    for required in (
        "status --porcelain=v1 --untracked-files=all",
        "rev-parse HEAD",
        "git archive",
        "--format=tar",
        "sha256sum",
        "archive_sha256",
        "release_sha",
        "release-manifest",
        "archive.sha256",
        "manifest.sha256",
        "tar -tf",
        "mktemp -d",
    ):
        assert required in value
    assert "--force" not in value
    assert "git stash" not in value
    assert "git reset" not in value


def test_remote_release_extraction_removes_group_and_other_write_bits() -> None:
    value = _text(DEPLOY)
    extraction = value.split("extract_release() {", 1)[1].split(
        "validate_release_contract() {", 1
    )[0]

    assert '/bin/chmod -R go-w -- "$staging"' in extraction
    assert '/bin/chmod -R go-w -- "$release_path"' in extraction
    assert extraction.index('/bin/chown -R root:root "$staging"') < extraction.index(
        '/bin/chmod -R go-w -- "$staging"'
    )


def test_deploy_has_prepare_verify_activate_order_and_bounded_failure_paths() -> None:
    value = _text(DEPLOY)

    for function in (
        "prepare_release()",
        "verify_release()",
        "activate_release()",
        "rollback_after_activation()",
    ):
        assert function in value
    calls = [
        value.rindex("prepare_release\n"),
        value.rindex("verify_release\n"),
        value.rindex("activate_release\n"),
    ]
    assert calls == sorted(calls)
    activate = value.index("activate_release()")
    assert value.index("install-demo-preview.sh", activate) > activate
    assert "activation_completed=1" in value
    assert "trap rollback_after_activation EXIT" in value
    assert "rollback-demo-preview.sh" in value
    assert "docker compose down" not in value


def test_prepare_requires_exact_root_owned_secret_set_and_small_allowlist() -> None:
    value = _text(DEPLOY)

    assert "files=12" in value
    assert SECRET_FILES == {
        name for name in SECRET_FILES if name in value
    }
    assert "/opt/orbbec-agent-platform/private/demo-preview" in value
    assert "0:600:regular file" in value
    assert "0:700:directory" in value
    assert "1 <= len(userids) <= 3" in value
    assert "len(set(userids))" in value
    assert "demo-userids" in value
    assert "cat " not in value
    assert "set -x" not in value


def test_prerequisite_bootstrap_turns_five_operator_inputs_into_exact_twelve() -> None:
    value = _text(PREREQUISITES)

    assert OPERATOR_FILES == {name for name in OPERATOR_FILES if name in value}
    assert SECRET_FILES == {name for name in SECRET_FILES if name in value}
    assert "operator_files" in value
    assert "generated_files" in value
    assert "${#operator_files[@]}" in value
    assert "${#generated_files[@]}" in value
    assert "files=12" in value
    assert "item.st_uid != 0" in value
    assert "stat.S_IMODE(item.st_mode) != 0o600" in value
    assert "stat.S_IMODE(root_metadata.st_mode) != 0o700" in value
    assert ".demo-preview-prerequisite-state" in value
    assert "os.urandom(32)" in value
    assert "base64.b64encode" in value
    assert 'document["transition_versions"] = [1]' in value
    for purpose in (
        "provider-encryption",
        "provider-lookup-hmac",
        "rate-limit-hmac",
    ):
        assert purpose in value


def test_prerequisite_bootstrap_creates_preview_database_and_least_privilege_roles() -> None:
    value = _text(PREREQUISITES)
    lowered = value.lower()

    for role in (
        "platform_control_owner_preview",
        "platform_control_migrator_preview",
        "platform_control_app_preview",
        "platform_directory_worker_preview",
        "platform_audit_append_preview",
        "platform_stream_ingest_preview",
        "platform_control_maintenance_preview",
    ):
        assert role in value
    assert "agent_platform_control_preview" in value
    assert "platform-postgres" in value
    assert "docker exec -u postgres" in value
    assert "nologin" in lowered
    assert "login password" in lowered
    assert "noinherit" in lowered
    assert "nosuperuser" in lowered
    assert "nocreatedb" in lowered
    assert "nocreaterole" in lowered
    assert "noreplication" in lowered
    assert "nobypassrls" in lowered
    assert "revoke connect on database" in lowered
    assert "grant connect on database agent_platform_control_preview" in lowered
    assert "where datname <> 'agent_platform_control_preview'" in lowered
    assert "createdb" in lowered and "agent_platform_control_preview" in lowered
    assert "create database agent_platform_control " not in lowered
    assert "alter database agent_platform_control " not in lowered
    assert "app.control_plane.migrate" not in value
    assert "set -x" not in value


def test_deploy_bootstraps_then_validates_and_uses_compose_runner_networks() -> None:
    value = _text(DEPLOY)

    bootstrap = value.index("bootstrap-demo-preview-prerequisites.sh")
    exact_validation = value.index("bootstrap-demo-preview-secrets.sh", bootstrap)
    migration = value.index("run_preview_migration", exact_validation)
    assert bootstrap < exact_validation < migration
    assert "platform-demo-preview-runner" in value
    assert "compose_preview run --rm --no-deps" in value
    assert "--network host" not in value
    assert 'required_networks = {"platform-internal", "platform-edge"}' in value
    assert 'edge_priority = networks["platform-edge"].get("gw_priority", 0)' in value
    assert "edge_priority != 1 or edge_priority <= internal_priority" in value
    assert 'services[name].get("ports")' in value
    assert "docker image inspect" in value
    assert "image-id" in value
    prerequisite = _text(PREREQUISITES).lower()
    assert "grant platform_control_owner_preview" in prerequisite
    assert "role_signature" in prerequisite
    assert "1:4:2:1:1:0" in prerequisite
    assert "public" in prerequisite and "no per-role deny" in prerequisite
    assert '{{index .Config.Labels "com.docker.compose.service"}}' in _text(
        PREREQUISITES
    )


def test_merged_compose_gate_distinguishes_egress_from_loopback_contract() -> None:
    value = _text(DEPLOY)

    assert 'preview_base_compose="$release_path/deploy/cloud/compose.demo-preview-base.yaml"' in value
    assert 'production_compose=(/usr/bin/docker compose' in value
    assert '-f "$base_compose")' in value
    assert 'preview_stack=(/usr/bin/docker compose' in value
    assert '-f "$preview_base_compose" -f "$preview_compose")' in value
    assert 'PLATFORM_POSTGRES_PREVIEW_ADDRESS="$postgres_address"' in value
    assert 'PLATFORM_EDGE_GATEWAY_PREVIEW_ADDRESS="$edge_gateway_address"' in value
    assert 'orbbec-agent-platform-internal' in value
    assert 'orbbec-agent-platform-edge' in value
    assert 'docker network inspect' in value
    assert (
        'compose_preview --profile demo-preview-tools config --format json'
        in value
    )
    assert 'egress_services = (' in value
    assert '"platform-api-demo-preview"' in value
    assert '"platform-demo-preview-runner"' in value
    assert 'loopback = services.get("platform-loopback-demo-preview")' in value
    assert 'set(loopback.get("networks", {})) != required_networks' in value
    assert 'loopback.get("image") != expected_image' in value
    assert 'port.get("host_ip") != "127.0.0.1"' in value
    assert 'str(port.get("published")) != "8081"' in value
    assert 'int(port.get("target", 0)) != 8080' in value
    egress = value.index("egress_services = (")
    loopback = value.index('loopback = services.get("platform-loopback-demo-preview")')
    assert egress < loopback
    assert 'edge_priority = networks["platform-edge"]' in value[egress:loopback]
    assert 'loopback_edge = loopback["networks"]["platform-edge"]' in value
    assert 'loopback_edge.get("gw_priority", 0) != 1' in value
    assert '"ipv4_address" in loopback_edge' in value


def test_accept_and_rollback_resolve_the_existing_edge_gateway() -> None:
    for path in (ACCEPT, ROLLBACK):
        value = _text(path)
        assert "docker network inspect" in value
        assert 'orbbec-agent-platform-edge' in value
        assert 'PLATFORM_EDGE_GATEWAY_PREVIEW_ADDRESS="$edge_gateway_address"' in value


def test_rollback_suppresses_compose_progress_from_machine_result() -> None:
    value = _text(ROLLBACK)
    stop = value.split("stop_demo_services() {", 1)[1].split(
        "if [[ ! -e \"$active_state\"", 1
    )[0]

    assert stop.count(">/dev/null 2>&1") == 2


def test_operator_preflight_accepts_only_safe_resumable_partial_publication() -> None:
    value = _text(DEPLOY)

    assert 'state = pathlib.Path(sys.argv[2])' in value
    assert 'state_path=/opt/orbbec-agent-platform/private/.demo-preview-prerequisite-state' in value
    assert "state.is_symlink()" in value
    assert "state_metadata.st_uid != 0" in value
    assert "stat.S_IMODE(state_metadata.st_mode) != 0o700" in value
    assert "published_generated.isdisjoint(staged_generated)" in value
    assert "published_generated | staged_generated != generated" in value
    assert "operator | published_generated" in value
    assert 'checked_file(path, 0o600)' in value
    assert 'checked_file(state / name, 0o600)' in value


def test_runner_commands_use_only_the_duplicate_free_runner_secret_view() -> None:
    value = _text(DEPLOY)
    migration = value[value.index("run_preview_migration()") : value.index("run_preview_bootstrap()")]
    bootstrap = value[value.index("run_preview_bootstrap()") : value.index("wait_preview_health()")]

    assert "/run/demo-preview-secrets/runner/preview-control-migrator-database-url" in migration
    for name in (
        "dingtalk-app-key",
        "dingtalk-corp-id",
        "dingtalk-app-secret",
        "preview-identity-encryption-keyring",
        "preview-identity-hmac-keyring",
        "preview-control-directory-worker-database-url",
        "demo-userids",
    ):
        assert f'/run/demo-preview-secrets/runner/$name' in bootstrap
    assert "/run/demo-preview-secrets/runtime/" not in migration + bootstrap
    assert "/run/demo-preview-secrets/offline/" not in migration + bootstrap
    assert "cap_add" not in migration + bootstrap
    assert "--cap-add" not in migration + bootstrap


def test_current_symlink_transaction_is_safe_unique_and_reentrant() -> None:
    value = _text(DEPLOY)

    assert "resolve_current_release()" in value
    assert r"^/opt/orbbec-agent-platform/releases/[0-9a-f]{40}$" in value
    assert 'current_before="$(resolve_current_release)"' in value
    assert 'transaction_nonce="$(/usr/bin/od -An -N16 -tx1 /dev/urandom' in value
    assert 'transaction_id="${release_sha}-${BASHPID}-${transaction_nonce}"' in value
    assert '.current-next-$transaction_id' in value
    assert '.current-restore-$transaction_id' in value
    assert "cleanup_transaction_links()" in value
    assert "restore_current_atomically()" in value
    assert "current.part" not in value
    assert "current.rollback" not in value
    activate = value[value.index("activate_phase()") : value.index('case "$phase"')]
    assert activate.index("trap rollback_after_activation EXIT") < activate.index("/bin/ln -s")
    assert activate.index("/bin/ln -s") < activate.index("/bin/mv -Tf")
    rollback = value[value.index("rollback_after_activation()") : value.index("activate_phase()")]
    assert "cleanup_transaction_links" in rollback


def test_current_switch_signal_window_resolves_the_link_instead_of_trusting_a_late_flag() -> None:
    value = _text(DEPLOY)
    activate = value[value.index("activate_phase()") : value.index('case "$phase"')]
    rollback = value[value.index("rollback_after_activation()") : value.index("activate_phase()")]

    attempted = activate.index("current_switch_attempted=1")
    move = activate.index("/bin/mv -Tf")
    switched = activate.index("current_switched=1")
    assert attempted < move < switched
    assert "${current_switch_attempted:-0}" in rollback
    assert 'active_current="$(resolve_current_release)"' in rollback
    assert '"$active_current" == "$current_before"' in rollback
    assert '"$active_current" != "$release_path"' in rollback
    assert "trap '' HUP INT TERM" in rollback


def test_failed_activation_rollback_is_fail_closed_before_current_restore() -> None:
    value = _text(DEPLOY)
    rollback = value[value.index("rollback_after_activation()") : value.index("activate_phase()")]

    assert "rollback-demo-preview.sh" in rollback
    assert "|| true" not in rollback
    assert "AGENT_DEMO_PREVIEW_ROLLBACK_OK" in rollback
    assert "orbbec-agent-demo-preview.conf" in rollback
    assert "rollback-retry" in rollback
    assert 'rollback_listeners="$(preview_listener_set)"' in rollback
    assert "restore_current_atomically" in rollback
    task3 = rollback.index("rollback-demo-preview.sh")
    include_absent = rollback.index("orbbec-agent-demo-preview.conf", task3)
    listener_absent = rollback.index('rollback_listeners="$(preview_listener_set)"', include_absent)
    restore = rollback.index("restore_current_atomically", listener_absent)
    assert task3 < include_absent < listener_absent < restore
    assert "preserve_rollback_retry" in rollback
    runbook = _text(RUNBOOK)
    assert "rollback-retry" in runbook
    assert "回滚失败时保留新 current" in runbook


def test_every_8081_gate_uses_the_same_any_address_listener_set_contract() -> None:
    deploy = _text(DEPLOY)
    accept = _text(ACCEPT)

    parser = "/usr/bin/awk '$4 ~ /:8081$/ {print $4}' | /usr/bin/sort"
    assert deploy.count("preview_listener_set()") == 1
    assert accept.count("preview_listener_set()") == 1
    assert parser in deploy and parser in accept

    preflight = deploy[
        deploy.index("validate_read_only_preflight()") : deploy.index("extract_release()")
    ]
    verify = deploy[deploy.index("verify_invariants()") : deploy.index("verify_phase()")]
    rollback = deploy[
        deploy.index("rollback_after_activation()") : deploy.index("activate_phase()")
    ]
    assert 'preflight_listeners="$(preview_listener_set)"' in preflight
    assert '[[ -z "$preflight_listeners" ]]' in preflight
    assert 'verify_listeners="$(preview_listener_set)"' in verify
    assert '[[ "$verify_listeners" == "127.0.0.1:8081" ]]' in verify
    assert 'rollback_listeners="$(preview_listener_set)"' in rollback
    assert '[[ -n "$rollback_listeners" ]]' in rollback

    assert 'accept_listeners="$(preview_listener_set)"' in accept
    assert '[[ "$accept_listeners" == "127.0.0.1:8081" ]]' in accept
    for value in (deploy, accept):
        assert "0\\.0\\.0\\.0" not in value
        assert "\\[::\\]" not in value


def test_8081_listener_parser_keeps_wildcards_other_interfaces_and_duplicates() -> None:
    program = "$4 ~ /:8081$/ {print $4}"
    sample = "\n".join(
        (
            "LISTEN 0 128 127.0.0.1:8081 0.0.0.0:*",
            "LISTEN 0 128 *:8081 *:*",
            "LISTEN 0 128 [::]:8081 [::]:*",
            "LISTEN 0 128 10.0.0.8:8081 0.0.0.0:*",
            "LISTEN 0 128 127.0.0.1:8081 0.0.0.0:*",
            "LISTEN 0 128 127.0.0.1:8080 0.0.0.0:*",
        )
    )
    result = subprocess.run(
        ["/usr/bin/awk", program],
        input=sample,
        text=True,
        capture_output=True,
        check=True,
    )
    assert sorted(result.stdout.splitlines()) == [
        "*:8081",
        "10.0.0.8:8081",
        "127.0.0.1:8081",
        "127.0.0.1:8081",
        "[::]:8081",
    ]


def test_verify_uses_isolated_preview_stack_and_preview_only_database_roles() -> None:
    value = _text(DEPLOY)

    for required in (
        "compose.yaml",
        "compose.demo-preview.yaml",
        "docker compose",
        "config",
        "docker build",
        "PLATFORM_IMAGE",
        "agent_platform_control_preview",
        "platform_control_migrator_preview",
        "platform_directory_worker_preview",
        "app.control_plane.migrate",
        "app.control_plane.demo_bootstrap",
        "DEMO_DIRECTORY_READY",
        "platform-api-demo-preview",
        "platform-loopback-demo-preview",
        "127.0.0.1:8081",
    ):
        assert required in value
    assert "platform_control_owner " not in value
    assert "agent_platform_control " not in value
    assert "compose_preview up -d --no-deps platform-postgres" not in value


def test_activation_is_locked_to_current_live_nginx_and_preview_rollback() -> None:
    value = _text(DEPLOY)

    assert (
        f'EXPECTED_LIVE_SHA256="{LIVE_NGINX_SHA}"' in value
        or f"EXPECTED_LIVE_SHA256={LIVE_NGINX_SHA}" in value
    )
    assert "install-demo-preview.sh" in value
    assert "rollback-demo-preview.sh" in value
    assert "systemctl restart nginx" not in value
    assert "docker restart" not in value
    assert "docker compose down" not in value


def test_release_preserves_existing_services_and_public_network_surface() -> None:
    deploy = _text(DEPLOY)
    accept = _text(ACCEPT)
    combined = deploy + accept

    for required in (
        "RestartCount",
        "StartedAt",
        "Config.Image",
        "docker inspect",
        "agent.orbbec.com.cn/",
        "agent.orbbec.com.cn/admin",
        "fae.orbbec.com.cn/",
        "http://47.106.112.69/",
        "ss -H -lnt",
        "127.0.0.1:8081",
        "preview_listener_set",
        "$4 ~ /:8081$/",
    ):
        assert required in combined
    for forbidden in (
        "ai-fae-backend restart",
        "platform-loopback restart",
        "platform-api restart",
        "systemctl restart nginx",
    ):
        assert forbidden not in combined.lower()


def test_acceptance_covers_public_login_security_and_invariants() -> None:
    value = _text(ACCEPT)

    for required in (
        f"https://agent.orbbec.com.cn{PREFIX}",
        "api/health",
        "login",
        "assets/",
        "api/v1/auth/dingtalk/start",
        "api/v1/auth/dingtalk/callback",
        "api/v1/account",
        "invalid_state",
        "replayed_state",
        "provider_zero_call",
        "unapproved_denial",
        "Secure",
        "HttpOnly",
        "SameSite=Lax",
        f"Path={PREFIX}",
        "401",
        "200",
        "application/json",
        "platform_preview_login_challenge",
    ):
        assert required in value
    assert "Authorization:" not in value
    assert "curl -v" not in value
    assert "set -x" not in value


def test_acceptance_output_is_fixed_pass_fail_only() -> None:
    value = _text(ACCEPT)

    assert "DEMO_PREVIEW_ACCEPTANCE_PASS" in value
    assert "DEMO_PREVIEW_ACCEPTANCE_FAIL" in value
    assert re.search(r"printf ['\"]%s\\n['\"] ['\"]PASS ", value)
    assert "response_body" not in value
    assert "set-cookie:" not in value.lower()
    assert "authorization_url" not in value


def test_runbook_documents_prepare_verify_activate_and_no_live_claim() -> None:
    value = _text(RUNBOOK)

    for required in (
        "prepare → verify → activate",
        TARGET,
        SSH_KEY,
        LIVE_NGINX_SHA,
        PREFIX,
        "deploy-demo-preview.sh",
        "accept-demo-preview.sh",
        "/opt/orbbec-agent-platform/current/deploy/cloud/rollback-demo-preview.sh",
        "1–3",
        "root:0600",
        "Task 1–3",
        "未部署",
        "不代表生产切换",
    ):
        assert required in value


def test_shell_scripts_are_syntax_valid() -> None:
    for path in (DEPLOY, ACCEPT):
        assert path.is_file()
        result = subprocess.run(
            ["/bin/bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
