import re
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"


def _normalized_shell(script: str) -> str:
    without_line_continuations = re.sub(r"\\\s*\n", " ", script)
    return " ".join(without_line_continuations.split())


def _assert_no_directory_compose_run(script: str) -> None:
    normalized = _normalized_shell(script)
    compose_run = re.compile(
        r'(?:"?\$\{compose\[@\]\}"?|(?:/usr/bin/)?docker\s+compose)'
        r"(?:(?!\|\||&&|;).)*?\brun\b"
        r"(?:(?!\|\||&&|;).)*?"
        r"(?:\bplatform-directory\b|app\.control_plane\.gender_probe)"
    )
    match = compose_run.search(normalized)
    assert match is None, f"directory probe must not use Compose run: {match.group(0)!r}"


def _assert_owner_count_mapping(script: str, bootstrap_variable: str) -> None:
    block = re.compile(
        rf'^expected_owner_count="(?P<default>[^"]+)"\n'
        rf'if \[\[ "\${re.escape(bootstrap_variable)}" == "1" \]\]; then\n'
        rf'  expected_owner_count="(?P<bootstrap>[^"]+)"\n'
        r"fi$",
        re.MULTILINE,
    )
    matches = list(block.finditer(script))
    assert len(matches) == 1, "owner count must be set by one explicit bootstrap block"
    assert matches[0].group("default") == "1"
    assert matches[0].group("bootstrap") == "0"
    fail_closed_gate = re.search(
        r"\[\[(?P<body>.*?)\]\] \|\| fail",
        script[matches[0].end():],
        re.DOTALL,
    )
    assert fail_closed_gate is not None
    assert (
        '"$owner_count" == "$expected_owner_count"'
        in fail_closed_gate.group("body")
    )


def _directory_gate_sql(script: str) -> str:
    heredoc = re.compile(
        r'^directory_gate_sql="\$\(/bin/cat <<\'SQL\'\n'
        r"(?P<sql>.*?)\nSQL\n\)\"$",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(heredoc.finditer(script))
    assert len(matches) == 1, "release script must define one directory gate SQL heredoc"
    return matches[0].group("sql")


def _assert_single_snapshot_directory_gate(script: str) -> None:
    sql = _directory_gate_sql(script)
    assert ";" not in sql, "directory gate must be one SQL statement"

    final_selects = list(
        re.finditer(
            r"^SELECT concat\(\n(?P<body>.*?)\n\) FROM gender_coverage$",
            sql,
            re.MULTILINE | re.DOTALL,
        )
    )
    assert len(final_selects) == 1, "directory gate must have one final aggregate SELECT"
    assert len(re.findall(r"^SELECT\b", sql, re.MULTILINE)) == 1
    final_select = final_selects[0].group(0)
    assert final_select.count("':'") == 5

    components = (
        "from platform_control.internal_users where role='platform_owner' and status='active'",
        "from active_generation where active_generation_id is not null and status='complete' and source_schema_version=2 and last_complete_at > clock_timestamp() - interval '8 hours'",
        "from platform_control.worker_heartbeats where worker_name='dingtalk-directory-event' and status='healthy' and last_seen_at > clock_timestamp() - interval '2 minutes'",
        "active_gender_count",
        "valid_gender_count",
        "null_invalid_gender_count",
    )
    positions = [final_select.find(component) for component in components]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions), "all six gates must feed the final aggregate"


def test_production_compose_runs_identity_and_least_privilege_workers():
    value = yaml.safe_load((CLOUD / "compose.yaml").read_text(encoding="utf-8"))
    services = value["services"]

    assert set(services) == {
        "platform-postgres",
        "platform-api",
        "platform-loopback",
        "platform-directory",
        "platform-dingtalk-stream",
    }
    api = services["platform-api"]
    startup = api["command"][2]
    assert "IFS= read" not in startup
    for secret_name in ("dingtalk-app-key", "dingtalk-agent-id", "dingtalk-corp-id"):
        assert f"/bin/cat /run/secrets/{secret_name}" in startup
    for variable in (
        "PLATFORM_DINGTALK_APP_KEY",
        "PLATFORM_DINGTALK_AGENT_ID",
        "PLATFORM_DINGTALK_CORP_ID",
    ):
        assert f'test -n "$${variable}"' in startup
    assert api["environment"]["PLATFORM_IDENTITY_MODE"] == "production"
    assert api["environment"]["PLATFORM_PUBLIC_BASE_URL"] == "https://agent.orbbec.com.cn"
    assert api["environment"]["PLATFORM_ROUTE_PREFIX"] == "/"
    assert api["environment"]["PLATFORM_COOKIE_NAME"] == "__Host-platform_session"
    assert api["environment"]["PLATFORM_TRUSTED_PROXY_CIDRS"] == "172.30.0.3/32"
    assert set(api["networks"]) == {"platform-internal", "platform-edge"}
    assert api["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.4"
    assert services["platform-postgres"]["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.2"

    directory = services["platform-directory"]
    stream = services["platform-dingtalk-stream"]
    assert directory["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.5"
    assert directory["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.5"
    assert stream["networks"]["platform-internal"]["ipv4_address"] == "172.30.0.6"
    assert stream["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.6"
    assert api["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.4"
    assert services["platform-loopback"]["networks"]["platform-edge"]["ipv4_address"] == "172.31.0.3"
    assert directory["command"] == [
        "python", "-m", "app.control_plane.worker_runtime", "directory"
    ]
    assert stream["command"] == [
        "python", "-m", "app.control_plane.worker_runtime", "stream"
    ]
    assert directory["restart"] == stream["restart"] == "unless-stopped"
    assert directory["read_only"] is stream["read_only"] is True
    assert directory["cap_drop"] == stream["cap_drop"] == ["ALL"]
    assert set(directory["networks"]) == set(stream["networks"]) == {
        "platform-internal", "platform-edge"
    }
    assert directory["volumes"] != stream["volumes"]
    assert "PLATFORM_CONTROL_STREAM_DATABASE_URL_FILE" not in directory["environment"]
    assert "PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE" not in stream["environment"]

    serialized = (CLOUD / "compose.yaml").read_text(encoding="utf-8")
    for forbidden in ("clientSecret:", "dingtalk-app-secret:", "corp-id:"):
        assert forbidden not in serialized
    assert services["platform-loopback"]["ports"] == ["127.0.0.1:8080:8080"]
    assert services["platform-loopback"]["environment"] == {
        "PLATFORM_LOOPBACK_TARGET_BASE_URL": "http://172.30.0.4:8080",
        "PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS": "127.0.0.1/32,172.31.0.1/32",
        "PLATFORM_LOOPBACK_SOURCE_ADDRESS": "172.30.0.3",
    }
    for name, service in services.items():
        if name != "platform-loopback":
            assert "ports" not in service


def test_runtime_image_contains_control_migrations():
    dockerfile = (CLOUD / "Dockerfile").read_text(encoding="utf-8")
    assert "backend/control_migrations" in dockerfile


def test_formal_nginx_uses_backend_auth_and_preserves_basic_auth_rollback():
    formal = (CLOUD / "dingtalk_nginx_transaction.py").read_text(encoding="utf-8")
    rollback = (CLOUD / "agent-domain.basic-auth.nginx.conf").read_text(
        encoding="utf-8"
    )

    assert "proxy_pass http://127.0.0.1:8080;" in formal
    assert "proxy_read_timeout 360s;" in formal
    assert "proxy_send_timeout 360s;" in formal
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in formal
    assert 'proxy_set_header Forwarded "";' in formal
    assert 'proxy_set_header Authorization "";' in formal
    assert 'Content-Security-Policy "default-src \'none\';' in formal
    assert "orbbec-agent-demo-preview.conf" in formal

    assert 'auth_basic "Orbbec Agent Platform";' in rollback
    assert "limit_except GET HEAD OPTIONS" in rollback
    assert "proxy_pass http://127.0.0.1:8080;" in rollback


def test_cutover_and_rollback_are_atomic_and_fae_safe():
    publish = (CLOUD / "publish-dingtalk-production.sh").read_text(encoding="utf-8")
    rollback = (CLOUD / "rollback-dingtalk-production.sh").read_text(encoding="utf-8")

    for script in (publish, rollback):
        assert "set -euo pipefail" in script
        assert "nginx -t" in script
        assert "systemctl reload nginx" in script
        assert "ai-fae-backend" in script
        assert "StartedAt" in script
        for forbidden in (
            "docker restart ai-fae-backend",
            "docker stop ai-fae-backend",
            "docker compose down",
            "systemctl restart nginx",
        ):
            assert forbidden not in script
    assert "PLATFORM_IDENTITY_MODE=production" in publish
    assert "agent-domain.basic-auth.nginx.conf" in rollback
    assert "PREVIOUS_RELEASE" in publish
    assert "PREVIOUS_PLATFORM_ENV" in publish
    assert "PREVIOUS_RELEASE" in rollback
    assert 'stop "${services_to_stop[@]}"' in rollback
    assert 'up -d --force-recreate "${services_to_start[@]}"' in rollback
    assert '/bin/ln -sfn "$PREVIOUS_RELEASE" "$platform_root/current"' in rollback


def test_cutover_supports_a_fail_closed_first_owner_login_stage():
    publish = (CLOUD / "publish-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )

    assert "--allow-unbound-owner" in publish
    assert 'expected_owner_count="1"' in publish
    assert 'expected_owner_count="0"' in publish
    assert 'OWNER_BOOTSTRAP=%q' in publish
    assert "DINGTALK_PRODUCTION_OWNER_LOGIN_REQUIRED" in publish
    assert "dingtalk_nginx_transaction.py" in publish
    assert '[[ "$code" == "302" ]]' in publish
    assert "/usr/bin/tr -d '\\r'" in publish
    assert "/usr/bin/grep -Fxiq 'location: /login'" in publish
    assert "https://agent.orbbec.com.cn/login" in publish


def test_identity_secret_bootstrap_is_noninteractive_and_service_scoped():
    script = (CLOUD / "bootstrap-dingtalk-production-secrets.sh").read_text(
        encoding="utf-8"
    )

    for required in (
        "identity-encryption-keyring",
        "identity-hmac-keyring",
        "rate-limit-hmac-keyring",
        "control-database-url",
        "control-audit-database-url",
        "control-directory-worker-database-url",
        "control-stream-ingest-database-url",
        "orbbec-agent-platform-api-secrets",
        "orbbec-agent-platform-directory-secrets",
        "orbbec-agent-platform-stream-secrets",
        "chown 10001:10001",
        "chmod 600",
    ):
        assert required in script
    for forbidden in ("security ", "read -s", "set -x", "dingtalk-app-secret="):
        assert forbidden not in script
    assert script.count("openssl rand 32") >= 3
    assert "cmp -s" not in script


def test_initial_owner_binding_uses_exact_private_provider_id_and_two_phase_receipt():
    script = (CLOUD / "bind-production-owner.sh").read_text(encoding="utf-8")

    for required in (
        "dingtalk-owner-userid",
        "show-directory-generation",
        "bind-owner",
        "--provider-id-file",
        "--receipt-file",
        "--receipt-key-file",
        "--confirm",
        "--approver",
        "platform_control_owner",
        "owner_binding=1",
        "docker run --rm",
        "{{.Config.Image}}",
        "--network orbbec-agent-platform-internal",
    ):
        assert required in script
    assert script.count("--approver") >= 2
    for forbidden in (
        "display_name", "苍渊", "grep.*name", "security ", "set -x",
        "run --rm --no-deps",
    ):
        assert forbidden not in script


def test_production_acceptance_covers_platform_identity_workers_and_fae_invariants():
    script = (CLOUD / "accept-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )

    for required in (
        "https://agent.orbbec.com.cn/",
        "https://agent.orbbec.com.cn/login",
        "/api/v1/account",
        "platform-identity-mode",
        "platform-directory",
        "platform-dingtalk-stream",
        "dingtalk-directory-event",
        "platform_owner",
        "active_generation_id",
        "127.0.0.1:8080",
        "ai-fae-backend",
        "FAE_STARTED_AT",
        "nginx -t",
        "DINGTALK_PRODUCTION_ACCEPTANCE_OK release=",
        "location: /login",
    ):
        assert required in script
    for forbidden in (
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "docker compose down",
        "systemctl restart nginx",
        "set -x",
        'auth_basic "AI ADMIN Demo";',
        "/admin/?view=services",
    ):
        assert forbidden not in script


def test_release_probes_never_use_compose_run_for_the_directory_service():
    for name in (
        "publish-dingtalk-production.sh",
        "accept-dingtalk-production.sh",
    ):
        _assert_no_directory_compose_run(
            (CLOUD / name).read_text(encoding="utf-8")
        )


def test_release_owner_count_mapping_preserves_the_explicit_bootstrap_stage():
    publish = (CLOUD / "publish-dingtalk-production.sh").read_text(encoding="utf-8")
    acceptance = (CLOUD / "accept-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )

    _assert_owner_count_mapping(publish, "owner_bootstrap")
    _assert_owner_count_mapping(acceptance, "OWNER_BOOTSTRAP")


def test_release_directory_gate_is_one_six_component_snapshot():
    for name in (
        "publish-dingtalk-production.sh",
        "accept-dingtalk-production.sh",
    ):
        _assert_single_snapshot_directory_gate(
            (CLOUD / name).read_text(encoding="utf-8")
        )


def test_publish_gates_cutover_on_the_running_directory_container_before_nginx_changes():
    script = (CLOUD / "publish-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )

    assert 'directory_id="$("${compose[@]}" ps -q platform-directory)"' in script
    assert 'docker inspect --format \'{{.State.Health.Status}}\' "$directory_id"' in script
    assert 'gender_probe_json="$(/usr/bin/docker exec "$directory_id"' in script
    assert "python -m app.control_plane.gender_probe" in script
    assert 'python -m app.control_plane.gender_probe)" || fail' in script
    assert 'json.loads(sys.stdin.read()).get("ready") is True' in script
    assert '<<<"$gender_probe_json" || fail' in script
    assert script.index("python -m app.control_plane.gender_probe") < script.index(
        '/usr/bin/install -o root -g root -m 644 "$rendered"'
    )
    assert script.index("python -m app.control_plane.gender_probe") < script.index(
        "/usr/sbin/nginx -t"
    )
    assert script.index('docker inspect --format \'{{.State.Health.Status}}\' "$directory_id"') < script.index(
        "python -m app.control_plane.gender_probe"
    )
    _assert_no_directory_compose_run(script)
    assert 'echo "$gender_probe_json"' not in script


def test_publish_and_accept_recheck_one_snapshot_of_all_directory_release_gates():
    publish = (CLOUD / "publish-dingtalk-production.sh").read_text(encoding="utf-8")
    acceptance = (CLOUD / "accept-dingtalk-production.sh").read_text(
        encoding="utf-8"
    )

    for script in (publish, acceptance):
        assert 'directory_id="$("${compose[@]}" ps -q platform-directory)"' in script
        assert 'docker inspect --format \'{{.State.Health.Status}}\' "$directory_id"' in script
        assert 'gender_probe_json="$(/usr/bin/docker exec "$directory_id"' in script
        assert 'python -m app.control_plane.gender_probe)" || fail' in script
        assert '<<<"$gender_probe_json" || fail' in script
        _assert_no_directory_compose_run(script)
        _assert_single_snapshot_directory_gate(script)
        assert script.count('/usr/bin/docker exec "$postgres_id" psql') == 1
        for required in (
            "status='complete'",
            "source_schema_version=2",
            "last_complete_at > clock_timestamp() - interval '8 hours'",
            "worker_name='dingtalk-directory-event'",
            "member.status='active'",
            "member.gender in ('male','female')",
            "member.gender is null or member.gender not in ('male','female')",
            "owner_count",
            "fresh_generation_count",
            "heartbeat_count",
            "active_gender_count",
            "valid_gender_count",
            "null_invalid_gender_count",
        ):
            assert required in script
        assert script.index('docker inspect --format \'{{.State.Health.Status}}\' "$directory_id"') < script.index(
            "python -m app.control_plane.gender_probe"
        )
        for forbidden in (
            'echo "$gender_probe_json"',
            "select member.display_name",
            "select member.gender",
            "encrypted_provider_id",
            "union_encrypted_provider_id",
            "provider_id",
            "mobile",
        ):
            assert forbidden not in script

    assert '[[ "$OWNER_BOOTSTRAP" == "0" || "$OWNER_BOOTSTRAP" == "1" ]]' in acceptance
    _assert_owner_count_mapping(publish, "owner_bootstrap")
    _assert_owner_count_mapping(acceptance, "OWNER_BOOTSTRAP")


def test_release_runbooks_use_candidate_probe_and_one_snapshot_release_gate():
    cloud = (ROOT / "docs" / "runbooks" / "cloud-platform.md").read_text(
        encoding="utf-8"
    )
    acceptance = (
        ROOT / "docs" / "runbooks" / "dingtalk-r1-acceptance.md"
    ).read_text(encoding="utf-8")

    for text in (cloud, acceptance):
        assert 'docker compose --env-file "$environment_path"' in text
        assert 'ps -q platform-directory' in text
        assert 'docker exec "$directory_id" python -m app.control_plane.gender_probe' in text
        assert "one consistent SQL snapshot" in text
        assert "owner-bootstrap-aware owner count" in text
        assert "active > 0" in text
        assert "active = valid" in text
        assert "null_invalid = 0" in text
        assert (
            "run --rm --no-deps platform-directory "
            "python -m app.control_plane.gender_probe"
        ) not in " ".join(text.split())


def test_release_runbooks_require_platform_first_gender_gates_and_reverse_rollback():
    cloud = (ROOT / "docs" / "runbooks" / "cloud-platform.md").read_text(
        encoding="utf-8"
    )
    acceptance = (
        ROOT / "docs" / "runbooks" / "dingtalk-r1-acceptance.md"
    ).read_text(encoding="utf-8")

    for text in (cloud, acceptance):
        for required in (
            "python -m app.control_plane.gender_probe",
            "`ready`",
            "source schema version exactly `2`",
            "`active:valid:null_invalid`",
            "`gender in ('male','female')`",
            "null/invalid count is zero",
        ):
            assert required in text
        for forbidden in (
            "employee names",
            "gender values",
            "provider identifiers",
            "mobile numbers",
            "ciphertext",
            "raw rows",
            "provider payloads",
        ):
            assert forbidden in text

    assert cloud.index("python -m app.control_plane.gender_probe") < cloud.index(
        "publish-dingtalk-production.sh"
    )
    assert "Platform deploys first" in cloud
    assert "AI ADMIN strict consumer" in cloud
    assert "Rollback AI ADMIN first" in cloud
