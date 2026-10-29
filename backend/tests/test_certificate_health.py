"""Read-only certificate monitoring; synthetic clocks, certificates and units."""

from datetime import UTC, datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


HELPER = Path(__file__).parents[2] / "deploy/cloud/certificate_health.py"
NOW = datetime(2026, 10, 29, 2, 32, tzinfo=UTC)


def health_module():
    assert HELPER.is_file(), "read-only certificate health checker is missing"
    spec = importlib.util.spec_from_file_location("certificate_health", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixtures():
    module = health_module()
    certificate = module.Certificate(
        "a" * 64,
        datetime(2026, 10, 27, 4, 27, 56, tzinfo=UTC),
        datetime(2026, 11, 2, 20, 27, 55, tzinfo=UTC),
    )
    state = {
        "timer": {"ActiveState": "active", "UnitFileState": "enabled"},
        "service": {
            "ActiveState": "inactive", "Result": "success", "ExecMainStatus": "0",
            "ExecMainExitTimestamp": "Thu 2026-10-29 00:13:34 UTC",
        },
    }
    return module, certificate, state


def check(module, certificate, state, *, peer=None, now=NOW, **kwargs):
    return module.check_health(
        disk_reader=lambda path: certificate,
        peer_reader=lambda host, port, timeout: peer or certificate,
        state_reader=lambda timer, service: state,
        clock=lambda: now,
        **kwargs,
    )


def test_short_lived_certificate_is_healthy_without_forced_renewal():
    module, certificate, state = fixtures()
    report = check(module, certificate, state)
    assert report["status"] == "ok"
    assert report["alerts"] == []
    assert report["checked_at"] == NOW.isoformat()
    assert report["certificate"]["remaining_hours"] > 100
    assert report["renewal"]["service"] == "orbbec-sdk-ip-cert.service"


def test_remaining_lifetime_below_explicit_threshold_alerts():
    module, certificate, state = fixtures()
    now = certificate.not_after - timedelta(hours=23)
    state["service"]["ExecMainExitTimestamp"] = (now - timedelta(hours=1)).strftime(
        "%a %Y-%m-%d %H:%M:%S UTC"
    )
    report = check(module, certificate, state, now=now, warning_hours=24)
    assert "certificate_expiring" in report["alerts"]
    assert report["status"] == "alert"


def test_disk_and_public_certificate_mismatch_alerts():
    module, certificate, state = fixtures()
    peer = module.Certificate("b" * 64, certificate.not_before, certificate.not_after)
    assert "certificate_mismatch" in check(module, certificate, state, peer=peer)["alerts"]


def test_responsible_renewal_service_failure_alerts():
    module, certificate, state = fixtures()
    state["service"].update(ActiveState="failed", Result="exit-code", ExecMainStatus="1")
    assert "renewal_failed" in check(module, certificate, state)["alerts"]


def test_unrelated_old_certbot_failure_is_not_queried():
    module, certificate, state = fixtures()
    units = []

    def read_state(timer, service):
        units.extend((timer, service))
        assert "certbot.service" not in units
        return state

    report = module.check_health(
        disk_reader=lambda path: certificate,
        peer_reader=lambda *args: certificate,
        state_reader=read_state,
        clock=lambda: NOW,
    )
    assert report["status"] == "ok"
    assert units == ["orbbec-sdk-ip-cert.timer", "orbbec-sdk-ip-cert.service"]


@pytest.mark.parametrize("change,expected", [
    ({"ActiveState": "inactive"}, "renewal_timer_inactive"),
    ({"UnitFileState": "disabled"}, "renewal_timer_disabled"),
])
def test_responsible_timer_must_remain_active_and_enabled(change, expected):
    module, certificate, state = fixtures()
    state["timer"].update(change)
    assert expected in check(module, certificate, state)["alerts"]


def test_stale_success_is_not_a_renewal_health_pass():
    module, certificate, state = fixtures()
    state["service"]["ExecMainExitTimestamp"] = "Mon 2026-10-26 02:00:00 UTC"
    assert "renewal_check_stale" in check(module, certificate, state)["alerts"]


def test_expired_and_not_yet_valid_certificates_alert():
    module, certificate, state = fixtures()
    expired = module.Certificate("a" * 64, NOW - timedelta(days=7), NOW)
    future = module.Certificate("a" * 64, NOW + timedelta(hours=1), NOW + timedelta(days=6))
    assert "certificate_expired" in check(module, expired, state)["alerts"]
    assert "certificate_not_yet_valid" in check(module, future, state)["alerts"]


def test_collection_error_does_not_expose_exception_body(capsys, monkeypatch):
    module = health_module()

    def fail(**kwargs):
        raise subprocess.CalledProcessError(1, ["private-command"], stderr=b"secret-sentinel")

    monkeypatch.setattr(module, "check_health", fail)
    assert module.main([]) == 2
    raw = capsys.readouterr().out
    assert json.loads(raw) == {"status": "error", "alerts": ["collection_failed"]}
    assert "sentinel" not in raw and "private-command" not in raw


def test_cli_returns_nonzero_for_alert_and_json_for_healthy(capsys, monkeypatch):
    module = health_module()
    for status, expected in (("ok", 0), ("alert", 1)):
        monkeypatch.setattr(module, "check_health", lambda **kwargs: {"status": status})
        assert module.main(["--warning-hours", "24"]) == expected
        assert json.loads(capsys.readouterr().out)["status"] == status


def test_systemd_reader_only_uses_show_for_bound_units(monkeypatch):
    module = health_module()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[:2] == ["systemctl", "show"]
        assert kwargs["env"]["TZ"] == "UTC"
        assert kwargs["timeout"] > 0
        return subprocess.CompletedProcess(command, 0, stdout="ActiveState=active\n")

    monkeypatch.setattr(module.subprocess, "run", run)
    state = module.read_renewal_state("orbbec-sdk-ip-cert.timer", "orbbec-sdk-ip-cert.service")
    assert len(calls) == 2
    assert state["timer"]["ActiveState"] == "active"


def test_public_tls_reader_uses_verified_context(monkeypatch):
    module = health_module()
    observed = []

    class Peer:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getpeercert(self, binary_form=False):
            if binary_form:
                return b"synthetic-public-der"
            return {"notBefore": "Oct 27 04:27:56 2026 GMT",
                    "notAfter": "Nov  2 20:27:55 2026 GMT"}

    class Context:
        def wrap_socket(self, connection, *, server_hostname):
            observed.append(server_hostname)
            return Peer()

    monkeypatch.setattr(module.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(module.socket, "create_connection", lambda *args, **kwargs: Peer())
    cert = module.read_external_certificate("47.106.112.69", 443, 5)
    assert observed == ["47.106.112.69"]
    assert cert.not_after == datetime(2026, 11, 2, 20, 27, 55, tzinfo=UTC)


def test_disk_reader_parses_public_openssl_metadata_only(monkeypatch, tmp_path):
    module = health_module()

    def run(command, **kwargs):
        assert command[:2] == ["openssl", "x509"]
        assert "-noout" in command
        assert "-text" not in command
        return subprocess.CompletedProcess(command, 0, stdout=(
            "notBefore=Oct 27 04:27:56 2026 GMT\n"
            "notAfter=Nov  2 20:27:55 2026 GMT\n"
            "sha256 Fingerprint=" + ":".join(["AA"] * 32) + "\n"
        ))

    monkeypatch.setattr(module.subprocess, "run", run)
    cert = module.read_disk_certificate(tmp_path / "cert.pem")
    assert cert.fingerprint == "a" * 64


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_invalid_threshold_cannot_disable_alerts(threshold):
    module, certificate, state = fixtures()
    with pytest.raises(ValueError):
        check(module, certificate, state, warning_hours=threshold)


@pytest.mark.parametrize("first,second", [(0, 0), (7, 0), (0, 8), (7, 8)])
def test_domain_runner_isolates_dns_and_aggregates_both_results(tmp_path, first, second):
    runner = HELPER.with_name("renew-domain-certificates.sh")
    assert runner.is_file(), "DNS-only renewal dispatcher is missing"
    fake = tmp_path / "certbot"
    log = tmp_path / "arguments.jsonl"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['TEST_LOG'], 'a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print('secret-sentinel', file=sys.stderr)\n"
        "sys.exit(int(os.environ['FIRST_EXIT' if sys.argv[4] == 'agent.orbbec.com.cn' else 'SECOND_EXIT']))\n"
    )
    fake.chmod(0o700)
    result = subprocess.run(
        ["sh", str(runner)], capture_output=True, text=True,
        env={**os.environ, "CERTBOT_BINARY": str(fake), "TEST_LOG": str(log),
             "FIRST_EXIT": str(first), "SECOND_EXIT": str(second)},
    )
    assert result.returncode == (1 if first or second else 0)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [
        ["-q", "renew", "--cert-name", name, "--no-random-sleep-on-renew"]
        for name in ("agent.orbbec.com.cn", "fae.orbbec.com.cn")
    ]
    assert "sdk-ip" not in log.read_text()
    assert "secret-sentinel" not in result.stdout + result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [row["status"] for row in rows] == [
        "error" if code else "ok" for code in (first, second)
    ]


def test_domain_runner_rejects_unplanned_arguments():
    runner = HELPER.with_name("renew-domain-certificates.sh")
    assert runner.is_file(), "DNS-only renewal dispatcher is missing"
    result = subprocess.run(["sh", str(runner), "sdk-ip"], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "unexpected_arguments"
