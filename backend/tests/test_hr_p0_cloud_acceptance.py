from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud" / "accept-hr-p0.sh"
CONTROL = ROOT / "deploy" / "local-execution-worker" / "agentops-control.sh"


def test_cloud_wrapper_is_fixed_bounded_and_status_only() -> None:
    source = CLOUD.read_text("utf-8")
    for required in (
        "required_config=/Users/agentops/AgentRuntime/private/acceptance-config.json",
        "remote_config=/opt/orbbec-agent-platform/private/hr-p0-acceptance.json",
        "remote_egress_evidence=/opt/orbbec-agent-platform/private/hr-provider-egress.evidence.json",
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
        "sha256sum",
        "provider-only",
        "direct_target_egress",
        '"direct_target_egress": False',
        "set(provider_authorities) & expected_authorities",
        'parsed.hostname.endswith(".")',
        "container_cleanup_manifest=$container_root/cleanup.json",
        '"/usr/bin/psql", "-X", "-v", "ON_ERROR_STOP=1"',
        "platform_hr.positions",
        "platform_hr.position_candidates",
        "platform_hr.candidate_documents",
    ):
        assert required in source


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
