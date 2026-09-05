from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud" / "accept-hr-p0.sh"
CONTROL = ROOT / "deploy" / "local-execution-worker" / "agentops-control.sh"
VERIFY_RELEASE = ROOT / "deploy" / "cloud" / "verify-web-research-release.py"


def test_cloud_wrapper_is_fixed_bounded_and_status_only() -> None:
    source = CLOUD.read_text("utf-8")
    for required in (
        "required_config=/Users/agentops/AgentRuntime/private/acceptance-config.json",
        "remote_config=/opt/orbbec-agent-platform/private/hr-p0-acceptance.json",
        "web_research_current=/Users/agentops/AgentRuntime/web-research/current",
        "cloud_admin_host=root@47.106.112.69",
        "ssh_bin=/usr/bin/ssh",
        "python_bin=/usr/bin/python3",
        "ConnectTimeout=8",
        "ServerAliveInterval=10",
        "ServerAliveCountMax=3",
        "timeout -k 10 1200",
        "target_agent=hr-bot",
        "python -m app.hr.p0_acceptance_cli",
        "HR_P0_ACCEPTANCE_OK",
    ):
        assert required in source
    for forbidden in (
        "eval ",
        "sudo -S",
        "security find-generic-password",
        "open -a",
        "osascript",
        "Feishu",
        "DingTalk",
        "rm -rf",
        "docker system prune",
    ):
        assert forbidden not in source
    assert "*" not in "\n".join(
        line for line in source.splitlines() if "rm " in line or "rmdir " in line
    )


def test_cloud_wrapper_requires_secure_strict_config_and_egress_evidence() -> None:
    source = CLOUD.read_text("utf-8")
    for required in (
        "schema_version",
        "cloud_admin_host",
        "cloud_admin_key",
        "set(value) == expected_keys",
        "600 $required_user",
        "700 $required_user",
        "! -L",
        "65536",
        "deployment_egress_evidence_sha256",
        "verify-web-research-release.py",
        "expected_egress_source_sha256=5604d7ac150a5bcd9e722edd777c5946f9e82fdb1bc4df5e6a3aceed0b8d5fe6",
        "expected_egress_release_sha256=c0a7aaf71f5ae8555371b0a93eae8499dd4e68e7224f0cb51cce4351df8f39fd",
        'web_research_source="$web_research_release/codex-process.mjs"',
        "system/com.orbbec.web-research",
        "/Library/LaunchDaemons/com.orbbec.web-research.plist",
        "release_sha",
        "auth_configured",
        "socket_mode",
        'remote ip "*:8088"',
        "/var/run/mDNSResponder",
        "provider_gateway=10.10.20.133",
        "provider_port=8088",
        "target_denial_probe=1.1.1.1",
        "target_denial_port=443",
        "SANDBOX_PROVIDER_EGRESS_OK",
        "container_cleanup_manifest=$container_root/cleanup.json",
        '"/usr/bin/psql", "-X", "-v", "ON_ERROR_STOP=1"',
        "platform_hr.positions",
        "platform_hr.position_candidates",
        "platform_hr.candidate_documents",
    ):
        assert required in source
    assert "remote_egress_evidence" not in source


def test_web_research_release_verifier_matches_deployed_flat_layout(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "web-research"
    files = {
        "release/codex-process.mjs": b"egress-enforced\n",
        "release/sidecar.mjs": b"sidecar\n",
        "release/schemas/search-output.schema.json": b"{}\n",
        "bin/web-search": b"#!/bin/sh\n",
        "bin/marketing-search": b"#!/bin/sh\n",
    }
    lines = [
        f"{hashlib.sha256(content).hexdigest()}  {relative}\n"
        for relative, content in files.items()
    ]
    manifest = "".join(lines).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    source_digest = hashlib.sha256(files["release/codex-process.mjs"]).hexdigest()
    release = runtime / "releases" / manifest_digest
    (release / "schemas").mkdir(parents=True)
    (runtime / "bin").mkdir(parents=True)
    for relative, content in files.items():
        destination = (
            release / relative.removeprefix("release/")
            if relative.startswith("release/")
            else runtime / relative
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (release / ".manifest.sha256").write_bytes(manifest)

    result = subprocess.run(
        [
            "python3",
            str(VERIFY_RELEASE),
            str(release),
            str(runtime),
            manifest_digest,
            source_digest,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "WEB_RESEARCH_RELEASE_OK"

    (runtime / "bin" / "web-search").write_bytes(b"tampered\n")
    rejected = subprocess.run(
        [
            "python3",
            str(VERIFY_RELEASE),
            str(release),
            str(runtime),
            manifest_digest,
            source_digest,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert rejected.stdout == ""


def test_agentops_dispatches_only_fixed_hr_p0_command_and_config() -> None:
    source = CONTROL.read_text("utf-8")
    for required in (
        "hr_p0_accept=$runtime_root/platform/deploy/cloud/accept-hr-p0.sh",
        "hr_p0_config=$runtime_root/private/acceptance-config.json",
        "accept-hr-p0)",
        '[[ $# -eq 2 && "$2" == "$hr_p0_config" ]] || fail',
        'run_fixed "$hr_p0_accept" "$hr_p0_config"',
    ):
        assert required in source
    for forbidden in ("eval ", 'exec "$@"', "find-generic-password", "sudo -S"):
        assert forbidden not in source


def test_cloud_wrapper_uses_exact_cleanup_targets_only() -> None:
    source = CLOUD.read_text("utf-8")
    assert "trap cleanup EXIT" in source
    assert '"$container_config"' in source
    assert '"$container_fixture_root/resume-strong.md"' in source
    assert '"$container_fixture_root/resume-adjacent.md"' in source
    assert '"$container_fixture_root/resume-invalid.txt"' in source
    assert '"$container_fixture_root/panorama-result.json"' in source
    assert '"$container_fixture_root/recruiting-results.json"' in source


def test_cloud_wrapper_cleans_partial_manifest_before_reporting_failure() -> None:
    source = CLOUD.read_text("utf-8")
    for required in (
        "cli_status=0",
        "|| cli_status=$?",
        'cli_succeeded = sys.argv[4] == "0"',
        "len(values) > expected_count",
        "array[]::uuid[]",
        "subprocess.DEVNULL",
        "timeout=60",
    ):
        assert required in source
    assert source.index("container_name:$container_cleanup_manifest") < source.index(
        '[[ "$cli_status" == 0'
    )
