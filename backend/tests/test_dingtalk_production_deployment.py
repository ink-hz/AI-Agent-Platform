import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
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


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o700)
    return path


def _run_release_harness(
    tmp_path: Path,
    script_name: str,
    *,
    probe_json: str = '{"ready":true}',
    probe_status: int = 0,
    directory_gates: str | None,
    owner_bootstrap: bool = False,
    python_optimize: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    harness_log = tmp_path / "harness.log"
    owner_count_path = tmp_path / "owner-count"
    platform_root = tmp_path / "opt" / "orbbec-agent-platform"
    release = platform_root / "releases" / ("a" * 40)
    previous = platform_root / "releases" / ("b" * 40)
    private = platform_root / "private"
    nginx_available = tmp_path / "etc" / "nginx" / "sites-available" / "agent-domain.conf"
    nginx_enabled = tmp_path / "etc" / "nginx" / "sites-enabled" / "agent-domain.conf"
    backup_root = tmp_path / "root" / "nginx-backups"
    for path in (
        release / "deploy" / "cloud",
        previous / "deploy" / "cloud",
        private,
        nginx_available.parent,
        nginx_enabled.parent,
        backup_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (private / "platform.env").write_text("SAFE_TEST=1\n", encoding="utf-8")
    owner_count_path.write_text("0\n", encoding="utf-8")
    for name in (
        "dingtalk-owner-userid",
        "dingtalk-corp-id",
        "control-migrator-database-url",
        "control-audit-database-url",
        "identity-encryption-keyring",
        "identity-hmac-keyring",
        "owner-receipt-keyring",
    ):
        (private / name).write_text("safe-test-value\n", encoding="utf-8")
        (private / name).chmod(0o600)
    for root in (release, previous):
        (root / "deploy" / "cloud" / "compose.yaml").write_text(
            "services: {}\n", encoding="utf-8"
        )
    (release / "PREVIOUS_RELEASE").write_text(str(previous) + "\n", encoding="utf-8")
    (release / "PREVIOUS_PLATFORM_ENV").write_text("safe\n", encoding="utf-8")
    (release / "deploy" / "cloud" / "dingtalk_nginx_transaction.py").write_text(
        "from pathlib import Path\nimport sys\nPath(sys.argv[2]).write_text(Path(sys.argv[1]).read_text())\n",
        encoding="utf-8",
    )
    nginx_available.write_text(
        "proxy_read_timeout 360s;\n"
        "proxy_set_header X-Forwarded-For $remote_addr;\n"
        "platform-identity-mode\n",
        encoding="utf-8",
    )
    current = platform_root / "current"
    current.symlink_to(release)
    (private / "dingtalk-production-cutover").write_text(
        "\n".join(
            (
                f"BACKUP_PATH={backup_root / 'prior'}",
                f"RELEASE_PATH={release}",
                f"PREVIOUS_RELEASE={previous}",
                f"PREVIOUS_ENVIRONMENT={release / 'PREVIOUS_PLATFORM_ENV'}",
                "FAE_ID=fae-id",
                "FAE_STARTED_AT=fae-start",
                f"OWNER_BOOTSTRAP={1 if owner_bootstrap else 0}",
                "",
            )
        ),
        encoding="utf-8",
    )
    (private / "dingtalk-production-cutover").chmod(0o600)

    fake_docker = _write_executable(
        fake_bin / "docker",
        r"""
        #!/bin/bash
        joined=" $* "
        if [[ "$joined" == *" compose "*" ps -q "* ]]; then
          echo "${!#}-id"
        elif [[ "$joined" == *"{{.Id}}"* ]]; then
          echo "fae-id"
        elif [[ "$joined" == *"{{.State.StartedAt}}"* ]]; then
          echo "fae-start"
        elif [[ "$joined" == *"{{range .Config.Env}}"* ]]; then
          echo "PLATFORM_IDENTITY_MODE=production"
        elif [[ "$joined" == *"{{.Config.Image}}"* ]]; then
          echo "orbbec-agent-platform:test"
        elif [[ "$joined" == *"{{.State.Health.Status}}"* || "$joined" == *"{{if .State.Health}}"* ]]; then
          echo "healthy"
        elif [[ "$joined" == *" app.control_plane.gender_probe "* ]]; then
          printf '%s\n' "$HARNESS_PROBE_JSON"
          exit "$HARNESS_PROBE_STATUS"
        elif [[ "$joined" == *" psql "* ]]; then
          if [[ -n "$HARNESS_DIRECTORY_GATES" ]]; then
            printf '%s\n' "$HARNESS_DIRECTORY_GATES"
          else
            printf '%s:1:1:3:3:0\n' "$(/bin/cat "$HARNESS_OWNER_COUNT")"
          fi
        elif [[ "$joined" == *" show-directory-generation "* ]]; then
          printf '%s\n' '{"status":"ok","generation":{"status":"complete","is_active":true,"generation_id":"00000000-0000-0000-0000-000000000001"}}'
        elif [[ "$joined" == *" bind-owner "*" --confirm "* ]]; then
          printf '1\n' > "$HARNESS_OWNER_COUNT"
          printf '%s\n' '{"status":"ok","operation":"bind"}'
        elif [[ "$joined" == *" bind-owner "* ]]; then
          printf '%s\n' '{"status":"dry_run","receipt_created":true}'
        else
          exit 97
        fi
        """,
    )
    fake_id = _write_executable(fake_bin / "id", "#!/bin/bash\necho 0\n")
    fake_date = _write_executable(
        fake_bin / "date", "#!/bin/bash\necho 20260823T000000Z\n"
    )
    fake_readlink = _write_executable(
        fake_bin / "readlink", '#!/bin/bash\necho "$HARNESS_RELEASE"\n'
    )
    fake_stat = _write_executable(
        fake_bin / "stat", "#!/bin/bash\necho '600 root'\n"
    )
    fake_install = _write_executable(
        fake_bin / "install",
        r"""
        #!/bin/bash
        if [[ "$1" == "-d" ]]; then
          /bin/mkdir -p "${!#}"
          exit 0
        fi
        target="${!#}"
        source="${@: -2:1}"
        /bin/mkdir -p "$(/usr/bin/dirname "$target")"
        /bin/cp "$source" "$target"
        if [[ "$target" == *"sites-available"* ]]; then
          echo nginx_write >> "$HARNESS_LOG"
        fi
        """,
    )
    fake_printf = _write_executable(
        fake_bin / "printf",
        r"""
        #!/bin/bash
        shift
        printf 'BACKUP_PATH=%s\nRELEASE_PATH=%s\nPREVIOUS_RELEASE=%s\nPREVIOUS_ENVIRONMENT=%s\nFAE_ID=%s\nFAE_STARTED_AT=%s\nOWNER_BOOTSTRAP=%s\n' "$@"
        """,
    )
    fake_noop = _write_executable(fake_bin / "noop", "#!/bin/bash\nexit 0\n")
    fake_nginx = _write_executable(
        fake_bin / "nginx", '#!/bin/bash\necho nginx >> "$HARNESS_LOG"\n'
    )
    fake_systemctl = _write_executable(
        fake_bin / "systemctl", '#!/bin/bash\necho systemctl >> "$HARNESS_LOG"\n'
    )
    fake_ss = _write_executable(
        fake_bin / "ss", "#!/bin/bash\necho 'LISTEN 0 128 127.0.0.1:8080'\n"
    )
    fake_curl = _write_executable(
        fake_bin / "curl",
        r"""
        #!/bin/bash
        headers=""
        output=""
        write_code=0
        url=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -D) headers="$2"; shift 2 ;;
            -o) output="$2"; shift 2 ;;
            -w) write_code=1; shift 2 ;;
            http*) url="$1"; shift ;;
            *) shift ;;
          esac
        done
        code=200
        body='platform-identity-mode'
        if [[ "$url" == */api/v1/account ]]; then
          code=401
          body=''
        elif [[ "$url" == */api/health ]]; then
          body='{"status":"ok"}'
        elif [[ "$url" == */ ]]; then
          code=302
        fi
        if [[ -n "$headers" ]]; then
          printf 'HTTP/1.1 %s OK\r\nlocation: /login\r\n\r\n' "$code" > "$headers"
        fi
        if [[ -n "$output" && "$output" != /dev/null ]]; then
          printf '%s' "$body" > "$output"
        elif [[ -z "$output" ]]; then
          printf '%s' "$body"
        fi
        if [[ "$write_code" == 1 ]]; then
          printf '%s' "$code"
        fi
        """,
    )

    script = (CLOUD / script_name).read_text(encoding="utf-8")
    replacements = {
        "/opt/orbbec-agent-platform": str(platform_root),
        "/etc/nginx/sites-available/agent-domain.conf": str(nginx_available),
        "/etc/nginx/sites-enabled/agent-domain.conf": str(nginx_enabled),
        "/root/nginx-backups": str(backup_root),
        "/usr/bin/id": str(fake_id),
        "/usr/bin/docker": str(fake_docker),
        "/usr/bin/python3": sys.executable,
        "/usr/bin/date": str(fake_date),
        "/usr/bin/readlink": str(fake_readlink),
        "/usr/bin/stat": str(fake_stat),
        "/usr/bin/install": str(fake_install),
        "/usr/bin/printf": str(fake_printf),
        "/usr/bin/curl": str(fake_curl),
        "/usr/bin/ss": str(fake_ss),
        "/usr/bin/openssl": str(fake_noop),
        "/usr/sbin/nginx": str(fake_nginx),
        "/bin/systemctl": str(fake_systemctl),
        "/bin/chown": str(fake_noop),
        "/bin/chmod": str(fake_noop),
        "/bin/sleep": str(fake_noop),
    }
    for source, target in replacements.items():
        script = script.replace(source, target)
    harness_script = _write_executable(tmp_path / script_name, script)
    environment = {
        **os.environ,
        "HARNESS_LOG": str(harness_log),
        "HARNESS_PROBE_JSON": probe_json,
        "HARNESS_PROBE_STATUS": str(probe_status),
        "HARNESS_DIRECTORY_GATES": directory_gates or "",
        "HARNESS_OWNER_COUNT": str(owner_count_path),
        "HARNESS_RELEASE": str(release),
    }
    if python_optimize:
        environment["PYTHONOPTIMIZE"] = "1"
    arguments = [str(harness_script)]
    if script_name == "publish-dingtalk-production.sh":
        arguments.append(str(release))
        if owner_bootstrap:
            arguments.append("--allow-unbound-owner")
        (private / "dingtalk-production-cutover").unlink()
    result = subprocess.run(
        arguments,
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    log = harness_log.read_text(encoding="utf-8") if harness_log.exists() else ""
    return result, log


class _StatefulReleaseHarness:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run_publish_bootstrap(self) -> subprocess.CompletedProcess[str]:
        result, _ = _run_release_harness(
            self.root,
            "publish-dingtalk-production.sh",
            directory_gates=None,
            owner_bootstrap=True,
        )
        return result

    def run_acceptance(self) -> subprocess.CompletedProcess[str]:
        return self._run_prepared("accept-dingtalk-production.sh")

    def run_owner_bind(self) -> subprocess.CompletedProcess[str]:
        return self._run_prepared(
            "bind-production-owner.sh",
            "operator_a",
            "operator_b",
            "BACKUP_REF",
            "INCIDENT_REF",
        )

    def _run_prepared(
        self, script_name: str, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        fake_bin = self.root / "bin"
        platform_root = self.root / "opt" / "orbbec-agent-platform"
        release = platform_root / "releases" / ("a" * 40)
        nginx_available = (
            self.root / "etc" / "nginx" / "sites-available" / "agent-domain.conf"
        )
        nginx_enabled = (
            self.root / "etc" / "nginx" / "sites-enabled" / "agent-domain.conf"
        )
        replacements = {
            "/opt/orbbec-agent-platform": str(platform_root),
            "/etc/nginx/sites-available/agent-domain.conf": str(nginx_available),
            "/etc/nginx/sites-enabled/agent-domain.conf": str(nginx_enabled),
            "/root/nginx-backups": str(self.root / "root" / "nginx-backups"),
            "/usr/bin/id": str(fake_bin / "id"),
            "/usr/bin/docker": str(fake_bin / "docker"),
            "/usr/bin/python3": sys.executable,
            "/usr/bin/date": str(fake_bin / "date"),
            "/usr/bin/readlink": str(fake_bin / "readlink"),
            "/usr/bin/stat": str(fake_bin / "stat"),
            "/usr/bin/install": str(fake_bin / "install"),
            "/usr/bin/printf": str(fake_bin / "printf"),
            "/usr/bin/curl": str(fake_bin / "curl"),
            "/usr/bin/ss": str(fake_bin / "ss"),
            "/usr/bin/openssl": str(fake_bin / "noop"),
            "/usr/sbin/nginx": str(fake_bin / "nginx"),
            "/bin/systemctl": str(fake_bin / "systemctl"),
            "/bin/chown": str(fake_bin / "noop"),
            "/bin/chmod": str(fake_bin / "noop"),
            "/bin/sleep": str(fake_bin / "noop"),
        }
        script = (CLOUD / script_name).read_text(encoding="utf-8")
        for source, target in replacements.items():
            script = script.replace(source, target)
        executable = _write_executable(self.root / script_name, script)
        environment = {
            **os.environ,
            "HARNESS_LOG": str(self.root / "harness.log"),
            "HARNESS_PROBE_JSON": '{"ready":true}',
            "HARNESS_PROBE_STATUS": "0",
            "HARNESS_DIRECTORY_GATES": "",
            "HARNESS_OWNER_COUNT": str(self.root / "owner-count"),
            "HARNESS_RELEASE": str(release),
        }
        return subprocess.run(
            [str(executable), *arguments],
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )


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
    assert 'expected_owner_count="0"' not in acceptance
    assert '"$owner_count" == "1"' in acceptance


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
    assert 'sys.exit(0 if json.loads(sys.stdin.read()).get("ready") is True else 1)' in script
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


@pytest.mark.parametrize(
    ("probe_json", "probe_status", "directory_gates", "python_optimize"),
    [
        ('{"ready":true}', 1, "0:1:1:3:3:0", False),
        ('{"ready":false}', 0, "0:1:1:3:3:0", True),
        ('{"ready":true}', 0, "0:0:1:3:3:0", False),
    ],
)
def test_executable_publish_harness_fails_before_nginx_for_every_gate_failure(
    tmp_path, probe_json, probe_status, directory_gates, python_optimize
):
    result, log = _run_release_harness(
        tmp_path,
        "publish-dingtalk-production.sh",
        probe_json=probe_json,
        probe_status=probe_status,
        directory_gates=directory_gates,
        owner_bootstrap=True,
        python_optimize=python_optimize,
    )

    assert result.returncode != 0
    assert result.stderr == "DINGTALK_PRODUCTION_PUBLISH_FAILED\n"
    assert "nginx_write" not in log
    assert "nginx" not in log
    assert "systemctl" not in log


def test_executable_harness_allows_zero_owner_publish_then_requires_one_owner_acceptance(
    tmp_path,
):
    harness = _StatefulReleaseHarness(tmp_path)

    publish_result = harness.run_publish_bootstrap()
    acceptance_before_bind = harness.run_acceptance()
    owner_count_before_bind = (tmp_path / "owner-count").read_text(
        encoding="utf-8"
    )
    bind_result = harness.run_owner_bind()
    owner_count_after_bind = (tmp_path / "owner-count").read_text(
        encoding="utf-8"
    )
    acceptance_after_bind = harness.run_acceptance()

    assert publish_result.returncode == 0, publish_result.stderr
    assert publish_result.stdout == "DINGTALK_PRODUCTION_OWNER_LOGIN_REQUIRED\n"
    assert acceptance_before_bind.returncode != 0
    assert acceptance_before_bind.stderr == "DINGTALK_PRODUCTION_ACCEPTANCE_FAILED\n"
    assert owner_count_before_bind == "0\n"
    assert bind_result.returncode == 0, bind_result.stderr
    assert bind_result.stdout == "PRODUCTION_OWNER_BINDING_OK owner_binding=1\n"
    assert owner_count_after_bind == "1\n"
    assert acceptance_after_bind.returncode == 0, acceptance_after_bind.stderr
    assert acceptance_after_bind.stdout.startswith("DINGTALK_PRODUCTION_ACCEPTANCE_OK ")


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
        assert "assert json." not in script
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

    _assert_owner_count_mapping(publish, "owner_bootstrap")
    assert '"$owner_count" == "1"' in acceptance


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
    normalized_cloud = " ".join(cloud.split())
    normalized_acceptance = " ".join(acceptance.split())
    assert "pre-cutover bootstrap may have zero active owners" in normalized_cloud
    assert (
        "formal post-cutover acceptance requires exactly one active owner"
        in normalized_cloud
    )
    assert (
        "formal post-cutover acceptance requires exactly one active owner"
        in normalized_acceptance
    )
    assert (
        "one active owner, or zero only during the explicit owner-bootstrap stage"
        not in normalized_acceptance
    )


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
