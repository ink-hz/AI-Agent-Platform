from __future__ import annotations

from pathlib import Path
import fcntl
import os
import re
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
PUBLISH = CLOUD / "publish-office-path-migration.sh"
ROLLBACK = CLOUD / "rollback-office-path-migration.sh"


def _normalized(value: str) -> str:
    return " ".join(re.sub(r"\\\s*\n", " ", value).split())


def _write_executable(path: Path, value: str) -> Path:
    path.write_text(textwrap.dedent(value).lstrip(), encoding="utf-8")
    path.chmod(0o700)
    return path


def _run_publish_harness(
    tmp_path: Path,
    *,
    failure: str = "",
    preexisting_lock: str = "",
    deferred_identity: bool = False,
    external_rollback_template: bool = False,
    external_transaction: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str, Path, Path]:
    platform_root = tmp_path / "opt" / "orbbec-agent-platform"
    ai_root = tmp_path / "opt" / "ai-admin-agent"
    release_sha = "a" * 40
    ai_sha = "b" * 40
    release = platform_root / "releases" / release_sha
    private = platform_root / "private"
    cloud = release / "deploy" / "cloud"
    nginx_source = tmp_path / "etc" / "nginx" / "sites-available" / "agent-domain.conf"
    backups = tmp_path / "root" / "nginx-backups"
    fake_bin = tmp_path / "bin"
    log = tmp_path / "harness.log"
    for directory in (
        cloud,
        private,
        ai_root / "scripts",
        ai_root / ".venv" / "bin",
        nginx_source.parent,
        backups,
        fake_bin,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (platform_root / "current").symlink_to(release)
    transaction_lock = private / "deploy-input.transaction.lock"
    transaction_lock.write_text("")
    transaction_lock.chmod(0o600)
    (ai_root / "RELEASE_COMMIT").write_text(ai_sha + "\n", encoding="utf-8")
    if not deferred_identity:
        (private / "office-migration-session-cookie").write_text(
            "test-session-cookie\n", encoding="utf-8"
        )
        (private / "office-migration-session-cookie").chmod(0o600)
    (ai_root / "scripts" / "smoke_platform_identity.py").write_text(
        "# harness marker\n", encoding="utf-8"
    )
    nginx_before = (
        "log_format agent_platform_redacted\n"
        "    '$request_method $uri $status $body_bytes_sent $request_time';\n"
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name agent.orbbec.com.cn;\n"
        "    location = /admin {\n"
        "        return 308 /admin/;\n"
        "    }\n"
        "    location /admin/ {\n"
        "        proxy_pass http://127.0.0.1:8011/;\n"
        "    }\n"
        "    location / {\n"
        "        proxy_pass http://127.0.0.1:8080;\n"
        "    }\n"
        "}\n"
    )
    nginx_source.write_text(nginx_before, encoding="utf-8")
    shutil.copy2(CLOUD / "office_path_nginx_transaction.py", cloud)
    shutil.copy2(ROLLBACK, cloud)
    if failure == "transaction":
        (cloud / "office_path_nginx_transaction.py").write_text(
            "raise SystemExit(1)\n", encoding="utf-8"
        )

    active_transaction = None
    if preexisting_lock == "action":
        (private / "agent-brain-action.lock").mkdir()
    elif preexisting_lock == "deploy":
        active_transaction = transaction_lock.open("r+")
        fcntl.flock(active_transaction, fcntl.LOCK_EX | fcntl.LOCK_NB)

    fake_id = _write_executable(fake_bin / "id", "#!/bin/bash\necho 0\n")
    fake_readlink = _write_executable(
        fake_bin / "readlink",
        f"#!/bin/bash\n{sys.executable} -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' \"${{@: -1}}\"\n",
    )
    fake_stat = _write_executable(fake_bin / "stat", "#!/bin/bash\necho '600 root'\n")
    fake_date = _write_executable(fake_bin / "date", "#!/bin/bash\necho 20260824T000000Z\n")
    fake_flock = _write_executable(
        fake_bin / "flock",
        f"""
        #!{sys.executable}
        import fcntl
        import sys

        descriptor = int(sys.argv[-1])
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(1)
        """,
    )
    fake_identity = _write_executable(
        ai_root / ".venv" / "bin" / "python",
        """
        #!/bin/bash
        [[ "$HARNESS_FAILURE" != "identity" ]]
        """,
    )
    del fake_identity
    fake_docker = _write_executable(
        fake_bin / "docker",
        """
        #!/bin/bash
        joined=" $* "
        case "$joined" in
          *"{{.Id}}"*) echo fae-container-id ;;
          *"{{.Image}}"*) echo sha256:fae-image-id ;;
          *"{{.State.StartedAt}}"*) echo 2026-08-24T00:00:00Z ;;
          *"{{.RestartCount}}"*)
            if [[ "$HARNESS_FAILURE" == "fae_drift" && -f "$HARNESS_LOG" ]] \
              && /usr/bin/grep -Fq forward_install "$HARNESS_LOG"; then
              echo 1
            else
              echo 0
            fi ;;
          *"{{json .Config}}"*) printf '%s' '{"safe":"config"}' ;;
          *"{{json .Mounts}}"*)
            if [[ "$HARNESS_FAILURE" == "fae_mount_order" && -f "$HARNESS_LOG" ]] \
              && /usr/bin/grep -Fq forward_install "$HARNESS_LOG"; then
              printf '%s' '[{"Destination":"/b","Source":"b","Type":"bind"},{"Destination":"/a","Source":"a","Type":"bind"}]'
            else
              printf '%s' '[{"Destination":"/a","Source":"a","Type":"bind"},{"Destination":"/b","Source":"b","Type":"bind"}]'
            fi ;;
          *) exit 91 ;;
        esac
        """,
    )
    fake_install = _write_executable(
        fake_bin / "install",
        """
        #!/bin/bash
        mode=700
        is_dir=0
        args=("$@")
        for ((index=0; index<${#args[@]}; index++)); do
          [[ "${args[$index]}" == "-d" ]] && is_dir=1
          if [[ "${args[$index]}" == "-m" ]]; then
            mode="${args[$((index+1))]}"
          fi
        done
        target="${args[$((${#args[@]}-1))]}"
        if [[ "$is_dir" == "1" ]]; then
          /bin/mkdir -p "$target"
          /bin/chmod "$mode" "$target"
          exit 0
        fi
        source="${args[$((${#args[@]}-2))]}"
        /bin/mkdir -p "$(/usr/bin/dirname "$target")"
        /bin/cp "$source" "$target"
        /bin/chmod "$mode" "$target"
        if [[ "$target" == "$HARNESS_NGINX_SOURCE.office-migration.part" ]]; then
          if [[ "$source" == *"agent-domain.office.conf" ]]; then
            echo forward_install >> "$HARNESS_LOG"
          else
            echo restore_install >> "$HARNESS_LOG"
          fi
        fi
        """,
    )
    fake_nginx = _write_executable(
        fake_bin / "nginx",
        """
        #!/bin/bash
        if [[ "$1" == "-T" ]]; then
          printf '# configuration file %s:\n' "$HARNESS_NGINX_SOURCE"
          /bin/cat "$HARNESS_NGINX_SOURCE"
          exit 0
        fi
        if [[ "$1" == "-t" ]]; then
          count=0
          [[ -f "$HARNESS_NGINX_COUNT" ]] && count="$(/bin/cat "$HARNESS_NGINX_COUNT")"
          count=$((count+1))
          printf '%s\n' "$count" > "$HARNESS_NGINX_COUNT"
          echo nginx_test >> "$HARNESS_LOG"
          if [[ "$HARNESS_FAILURE" == "nginx_t" && "$count" == "1" ]]; then
            exit 1
          fi
          exit 0
        fi
        exit 92
        """,
    )
    fake_systemctl = _write_executable(
        fake_bin / "systemctl",
        """
        #!/bin/bash
        if [[ "$1" == "is-active" ]]; then exit 0; fi
        if [[ "$1" == "reload" && "$2" == "nginx" ]]; then
          echo reload >> "$HARNESS_LOG"
          exit 0
        fi
        echo unexpected_systemctl >> "$HARNESS_LOG"
        exit 93
        """,
    )
    fake_curl = _write_executable(
        fake_bin / "curl",
        """
        #!/bin/bash
        url=""
        write=""
        output=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -w) write="$2"; shift 2 ;;
            -o) output="$2"; shift 2 ;;
            http*) url="$1"; shift ;;
            *) shift ;;
          esac
        done
        code=200
        body='{"status":"ok","runtime":{"git_sha":"'"$HARNESS_AI_SHA"'"}}'
        if [[ "$HARNESS_FAILURE" == "ai_release" ]]; then
          body='{"status":"ok","runtime":{"git_sha":"cccccccccccccccccccccccccccccccccccccccc"}}'
        fi
        case "$url" in
          *127.0.0.1:8011/health) [[ "$HARNESS_FAILURE" == "ai_health" ]] && code=503 ;;
          *127.0.0.1:8011/office/health)
            if [[ "$HARNESS_FAILURE" == "office_health" ]]; then code=200; else code=404; fi ;;
          *127.0.0.1:8011/office/*) [[ "$HARNESS_FAILURE" == "shell" ]] && code=503 ;;
          *127.0.0.1:8080/api/health) body='{"status":"ok"}' ;;
          *agent.orbbec.com.cn/office/health) code=404 ;;
          *agent.orbbec.com.cn/office/*) [[ "$HARNESS_FAILURE" == "public" ]] && code=503 ;;
          *agent.orbbec.com.cn/admin/) code=302 ;;
          *agent.orbbec.com.cn/) code=302 ;;
          *fae.orbbec.com.cn/*) code=200 ;;
          *47.106.112.69/*) code=200 ;;
        esac
        if [[ "$url" == *47.106.112.69/* && -n "$write" ]]; then
          printf '%s ' "$code"
        elif [[ -n "$write" ]]; then
          printf '%s' "$code"
        elif [[ -z "$output" ]]; then
          printf '%s' "$body"
        fi
        [[ "$code" =~ ^2 || "$code" == 302 || "$code" == 404 ]]
        """,
    )
    replacements = {
        "/opt/orbbec-agent-platform": str(platform_root),
        "/opt/ai-admin-agent": str(ai_root),
        "/etc/nginx/sites-available/agent-domain.conf": str(nginx_source),
        "/root/nginx-backups": str(backups),
        "/root/office-migration-tools": str(
            tmp_path / "root" / "office-migration-tools"
        ),
        "/usr/bin/id": str(fake_id),
        "/usr/bin/readlink": str(fake_readlink),
        "/usr/bin/stat": str(fake_stat),
        "/usr/bin/date": str(fake_date),
        "/usr/bin/flock": str(fake_flock),
        "/usr/bin/docker": str(fake_docker),
        "/usr/bin/install": str(fake_install),
        "/usr/bin/python3": sys.executable,
        "/usr/bin/sha256sum": "/sbin/sha256sum",
        "/usr/bin/curl": str(fake_curl),
        "/usr/sbin/nginx": str(fake_nginx),
        "/bin/systemctl": str(fake_systemctl),
    }
    script = PUBLISH.read_text(encoding="utf-8")
    for source, target in replacements.items():
        script = script.replace(source, target)
    rollback_for_harness = ROLLBACK.read_text(encoding="utf-8")
    for source, target in replacements.items():
        rollback_for_harness = rollback_for_harness.replace(source, target)
    rollback_for_harness = rollback_for_harness.replace(
        "metadata.st_uid != 0", "metadata.st_uid != os.getuid()"
    )
    (cloud / "rollback-office-path-migration.sh").write_text(
        rollback_for_harness, encoding="utf-8"
    )
    rollback_template_override = ""
    transaction_override = ""
    if external_transaction:
        tools = tmp_path / "root" / "office-migration-tools" / ("c" * 40)
        tools.mkdir(parents=True, exist_ok=True, mode=0o700)
        transaction = tools / "office_path_nginx_transaction.py"
        transaction.write_text(
            (CLOUD / "office_path_nginx_transaction.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        transaction.chmod(0o600)
        transaction_override = str(transaction)
        (cloud / "office_path_nginx_transaction.py").write_text(
            "raise SystemExit(1)\n", encoding="utf-8"
        )
    if external_rollback_template:
        tools = tmp_path / "root" / "office-migration-tools" / ("c" * 40)
        tools.mkdir(parents=True, mode=0o700)
        rollback_override = tools / "rollback-office-path-migration.sh"
        rollback_override.write_text(rollback_for_harness, encoding="utf-8")
        rollback_override.chmod(0o600)
        rollback_template_override = str(rollback_override)
        (cloud / "rollback-office-path-migration.sh").write_text(
            "invalid bundled rollback\n", encoding="utf-8"
        )
    executable = _write_executable(tmp_path / "publish-office.sh", script)
    try:
        result = subprocess.run(
            [str(executable)],
            cwd=tmp_path,
            env={
                **os.environ,
                "AI_ADMIN_RELEASE_SHA": ai_sha,
                "PLATFORM_RELEASE_SHA": release_sha,
                "OFFICE_MIGRATION_IDENTITY_SMOKE_MODE": (
                    "deferred_browser" if deferred_identity else "cookie"
                ),
                "OFFICE_MIGRATION_ROLLBACK_TEMPLATE": rollback_template_override,
                "OFFICE_MIGRATION_TRANSACTION": transaction_override,
                "HARNESS_FAILURE": failure,
                "HARNESS_LOG": str(log),
                "HARNESS_NGINX_SOURCE": str(nginx_source),
                "HARNESS_NGINX_COUNT": str(tmp_path / "nginx-count"),
                "HARNESS_AI_SHA": ai_sha,
            },
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    finally:
        if active_transaction is not None:
            active_transaction.close()
    return result, log.read_text() if log.exists() else "", nginx_source, backups


def test_office_path_publish_and_rollback_scripts_exist() -> None:
    assert PUBLISH.is_file()
    assert ROLLBACK.is_file()


def test_publish_is_a_locked_fail_closed_nginx_transaction() -> None:
    value = PUBLISH.read_text(encoding="utf-8")
    normalized = _normalized(value)

    for required in (
        "set -euo pipefail",
        "umask 077",
        "AI_ADMIN_RELEASE_SHA",
        "PLATFORM_RELEASE_SHA",
        "agent-brain-action.lock",
        "deploy-input.transaction.lock",
        "deploy-input.lock",
        "/usr/bin/flock",
        "/etc/nginx/sites-enabled/agent-domain.conf",
        "office_path_nginx_transaction.py",
        "nginx -T",
        "nginx -t",
        "systemctl reload nginx",
        "127.0.0.1:8011/health",
        "127.0.0.1:8011/office/health",
        "127.0.0.1:8080/api/health",
        "smoke_platform_identity.py",
        "https://agent.orbbec.com.cn/office/",
        "https://agent.orbbec.com.cn/admin/",
        "ai-fae-backend",
        "{{.Image}}",
        "{{.State.StartedAt}}",
        "{{.RestartCount}}",
        "{{json .Config}}",
        "{{json .Mounts}}",
        "https://fae.orbbec.com.cn/",
        "http://47.106.112.69/",
        "legacy_admin_route_conflict_restored",
    ):
        assert required in value

    assert normalized.index('"$identity_smoke"') < normalized.index(
        '/usr/bin/python3 "$transaction"'
    )
    assert normalized.index('/usr/bin/python3 "$transaction"') < normalized.index(
        '"$candidate" "$nginx_source_part"'
    )
    assert "systemctl restart nginx" not in value
    assert "set -x" not in value
    for forbidden in (
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "docker compose up",
        "docker compose down",
    ):
        assert forbidden not in normalized


def test_publish_collects_only_hashed_fae_config_and_mounts() -> None:
    value = PUBLISH.read_text(encoding="utf-8")

    assert "fae_config_hash" in value
    assert "fae_mounts_hash" in value
    normalized = _normalized(value)
    assert (
        "docker inspect --format '{{json .Config}}' ai-fae-backend | "
        "/usr/bin/sha256sum"
    ) in normalized
    assert "docker inspect --format '{{json .Mounts}}' ai-fae-backend" in normalized
    assert "sorted(value" in value
    assert "sort_keys=True" in value
    assert "FAE_CONFIG_RAW" not in value
    assert "FAE_MOUNTS_RAW" not in value


def test_publish_has_one_forward_install_and_automatic_backup_restore() -> None:
    value = PUBLISH.read_text(encoding="utf-8")

    assert value.count('"$candidate" "$nginx_source_part"') == 1
    assert '"$backup" "$nginx_source_part"' in value
    assert value.count("/usr/sbin/nginx -t") == 2
    assert value.count("/bin/systemctl reload nginx") == 2
    assert "rollback_required=1" in value
    assert "rollback_required=0" in value
    assert "trap rollback_on_failure EXIT" in value
    assert "wait_for_http_code" in value
    assert "/bin/sleep 0.25" in value


def test_evidence_and_installed_rollback_are_owner_only() -> None:
    value = PUBLISH.read_text(encoding="utf-8")

    assert '/usr/bin/install -d -o root -g root -m 700 "$evidence_dir"' in value
    assert '/bin/chmod 600 "$backup" "$candidate" "$baseline" "$report"' in value
    assert (
        '/usr/bin/install -o root -g root -m 700 "$rollback_rendered" '
        '"$rollback_installed"'
    ) in _normalized(value)


def test_rollback_is_argument_free_hash_bound_and_does_not_touch_services() -> None:
    value = ROLLBACK.read_text(encoding="utf-8")
    normalized = _normalized(value)

    for required in (
        "set -euo pipefail",
        "umask 077",
        "office-path-migrations",
        "BACKUP_SHA256",
        "CANDIDATE_SHA256",
        "sha256sum",
        "O_NOFOLLOW",
        "nginx -t",
        "systemctl reload nginx",
        "legacy_admin_route_conflict_restored=true",
        "ai-fae-backend",
    ):
        assert required in value
    assert '[[ "$#" == "0" ]]' in value
    assert "systemctl restart" not in value
    assert "docker restart" not in value
    assert "docker compose" not in normalized
    assert "../" not in value


@pytest.mark.parametrize("lock_name", ["action", "deploy"])
def test_publish_harness_refuses_existing_deployment_locks_without_nginx_write(
    tmp_path: Path, lock_name: str
) -> None:
    result, log, _nginx, _backups = _run_publish_harness(
        tmp_path, preexisting_lock=lock_name
    )

    assert result.returncode != 0
    assert log == ""


@pytest.mark.parametrize(
    "failure",
    ["ai_health", "ai_release", "office_health", "shell", "identity", "transaction"],
)
def test_publish_harness_candidate_failures_do_not_write_nginx(
    tmp_path: Path, failure: str
) -> None:
    result, log, _nginx, _backups = _run_publish_harness(tmp_path, failure=failure)

    assert result.returncode != 0
    assert "forward_install" not in log
    assert "nginx_test" not in log
    assert "reload" not in log


@pytest.mark.parametrize("failure", ["nginx_t", "public", "fae_drift"])
def test_publish_harness_post_install_failure_restores_the_complete_backup(
    tmp_path: Path, failure: str
) -> None:
    result, log, nginx_source, _backups = _run_publish_harness(
        tmp_path, failure=failure
    )

    assert result.returncode != 0
    assert log.count("forward_install") == 1
    assert log.count("restore_install") == 1
    assert "location = /admin" in nginx_source.read_text(encoding="utf-8")
    assert "location ^~ /office/" not in nginx_source.read_text(encoding="utf-8")
    assert "restart" not in log


def test_publish_harness_success_is_one_install_one_test_one_reload_and_owner_only(
    tmp_path: Path,
) -> None:
    result, log, nginx_source, backups = _run_publish_harness(tmp_path)

    assert result.returncode == 0, result.stderr
    assert log.splitlines() == ["forward_install", "nginx_test", "reload"]
    assert "location ^~ /office/" in nginx_source.read_text(encoding="utf-8")
    published_nginx = nginx_source.read_text(encoding="utf-8")
    assert "location = /admin" in published_nginx
    admin_boundary = published_nginx[
        published_nginx.index("location = /admin"):
        published_nginx.index("location = /office")
    ]
    assert "proxy_pass http://127.0.0.1:8080;" in admin_boundary
    assert "127.0.0.1:8011" not in admin_boundary
    evidence_dirs = list(backups.glob("ai-admin-office-*"))
    assert len(evidence_dirs) == 1
    evidence = evidence_dirs[0]
    assert evidence.stat().st_mode & 0o777 == 0o700
    for name in ("agent-domain.conf", "agent-domain.office.conf", "fae-baseline", "report"):
        assert (evidence / name).stat().st_mode & 0o777 == 0o600
    report = (evidence / "report").read_text(encoding="utf-8")
    assert "platform_admin_route_restored=true" in report
    assert "fae_managed_files_unchanged=true" in report
    assert not (
        tmp_path
        / "opt"
        / "orbbec-agent-platform"
        / "private"
        / "agent-brain-action.lock"
    ).exists()


def test_publish_harness_accepts_mounts_with_only_serialization_order_changed(
    tmp_path: Path,
) -> None:
    result, log, nginx_source, _backups = _run_publish_harness(
        tmp_path, failure="fae_mount_order"
    )

    assert result.returncode == 0, result.stderr
    assert log.splitlines() == ["forward_install", "nginx_test", "reload"]
    assert "location ^~ /office/" in nginx_source.read_text(encoding="utf-8")


def test_publish_harness_marks_explicit_browser_identity_as_pending(
    tmp_path: Path,
) -> None:
    result, log, nginx_source, backups = _run_publish_harness(
        tmp_path,
        failure="identity",
        deferred_identity=True,
    )

    assert result.returncode == 0, result.stderr
    assert "PENDING_IDENTITY" in result.stdout
    assert log.splitlines() == ["forward_install", "nginx_test", "reload"]
    assert "location ^~ /office/" in nginx_source.read_text(encoding="utf-8")
    report = next(backups.glob("ai-admin-office-*/report")).read_text(
        encoding="utf-8"
    )
    assert "authenticated_identity_smoke=deferred_browser" in report


def test_publish_harness_accepts_owner_only_external_rollback_template(
    tmp_path: Path,
) -> None:
    result, log, nginx_source, _backups = _run_publish_harness(
        tmp_path,
        external_rollback_template=True,
    )

    assert result.returncode == 0, result.stderr
    assert log.splitlines() == ["forward_install", "nginx_test", "reload"]
    assert "location ^~ /office/" in nginx_source.read_text(encoding="utf-8")


def test_publish_harness_accepts_owner_only_external_transaction(
    tmp_path: Path,
) -> None:
    result, log, nginx_source, _backups = _run_publish_harness(
        tmp_path,
        external_transaction=True,
    )

    assert result.returncode == 0, result.stderr
    assert log.splitlines() == ["forward_install", "nginx_test", "reload"]
    assert "location ^~ /office/" in nginx_source.read_text(encoding="utf-8")


def _installed_rollback(tmp_path: Path) -> Path:
    scripts = list(
        (
            tmp_path
            / "opt"
            / "orbbec-agent-platform"
            / "private"
            / "office-path-migrations"
        ).glob("*/rollback-office-path-migration.sh")
    )
    assert len(scripts) == 1
    return scripts[0]


def test_installed_rollback_harness_restores_legacy_config_from_any_working_directory(
    tmp_path: Path,
) -> None:
    publish, _log, nginx_source, _backups = _run_publish_harness(tmp_path)
    assert publish.returncode == 0, publish.stderr
    rollback = _installed_rollback(tmp_path)

    result = subprocess.run(
        [str(rollback)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HARNESS_FAILURE": "",
            "HARNESS_LOG": str(tmp_path / "harness.log"),
            "HARNESS_NGINX_SOURCE": str(nginx_source),
            "HARNESS_NGINX_COUNT": str(tmp_path / "nginx-count"),
            "HARNESS_AI_SHA": "b" * 40,
        },
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "AI_ADMIN_OFFICE_PATH_ROLLBACK_OK\n"
    assert "location = /admin" in nginx_source.read_text(encoding="utf-8")
    report = rollback.parent / "rollback-report"
    assert "legacy_admin_route_conflict_restored=true" in report.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "mutation", ["symlink", "tamper", "argument", "current_drift"]
)
def test_installed_rollback_harness_rejects_untrusted_backup_or_arguments(
    tmp_path: Path, mutation: str
) -> None:
    publish, _log, nginx_source, backups = _run_publish_harness(tmp_path)
    assert publish.returncode == 0, publish.stderr
    rollback = _installed_rollback(tmp_path)
    backup = next(backups.glob("ai-admin-office-*/agent-domain.conf"))
    arguments: list[str] = []
    office_value = nginx_source.read_text(encoding="utf-8")
    if mutation == "symlink":
        original = backup.with_name("original.conf")
        backup.rename(original)
        backup.symlink_to(original)
    elif mutation == "tamper":
        backup.write_text("tampered\n", encoding="utf-8")
    elif mutation == "current_drift":
        nginx_source.write_text(office_value + "# later change\n", encoding="utf-8")
    else:
        arguments.append(str(backup))
    expected_value = nginx_source.read_text(encoding="utf-8")

    result = subprocess.run(
        [str(rollback), *arguments],
        cwd=tmp_path,
        env={
            **os.environ,
            "HARNESS_FAILURE": "",
            "HARNESS_LOG": str(tmp_path / "harness.log"),
            "HARNESS_NGINX_SOURCE": str(nginx_source),
            "HARNESS_NGINX_COUNT": str(tmp_path / "nginx-count"),
            "HARNESS_AI_SHA": "b" * 40,
        },
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert nginx_source.read_text(encoding="utf-8") == expected_value
