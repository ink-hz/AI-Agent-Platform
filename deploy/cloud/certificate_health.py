#!/usr/bin/env python3
"""Read-only certificate/renewal probe. JSON only; never renews or sends alerts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess


@dataclass(frozen=True)
class Certificate:
    fingerprint: str
    not_before: datetime
    not_after: datetime


def _certificate_time(value: str) -> datetime:
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(value), UTC)


def read_disk_certificate(path: Path) -> Certificate:
    result = subprocess.run(
        ["openssl", "x509", "-in", str(path), "-noout", "-dates", "-fingerprint", "-sha256"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    fingerprint = next(value for key, value in fields.items() if key.lower().endswith("fingerprint"))
    return Certificate(
        fingerprint.replace(":", "").lower(),
        _certificate_time(fields["notBefore"]), _certificate_time(fields["notAfter"]),
    )


def read_external_certificate(host: str, port: int, timeout: float) -> Certificate:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as connection:
        with context.wrap_socket(connection, server_hostname=host) as peer:
            cert = peer.getpeercert()
            return Certificate(
                hashlib.sha256(peer.getpeercert(binary_form=True)).hexdigest(),
                _certificate_time(cert["notBefore"]), _certificate_time(cert["notAfter"]),
            )


def read_renewal_state(timer: str, service: str) -> dict:
    properties = {
        "timer": (timer, ("ActiveState", "UnitFileState")),
        "service": (service, ("ActiveState", "Result", "ExecMainStatus", "ExecMainExitTimestamp")),
    }
    state = {}
    for kind, (unit, names) in properties.items():
        command = ["systemctl", "show", unit, "--no-pager"]
        for name in names:
            command.extend(("--property", name))
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
        state[kind] = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return state


def _last_check(value: str) -> datetime | None:
    if not value:
        return None
    # systemctl's TZ=UTC and LC_ALL=C make this independent of the server timezone.
    return datetime.strptime(value.split(" ", 1)[1], "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)


def check_health(
    *, host: str = "47.106.112.69", port: int = 443,
    cert_path: Path = Path("/etc/letsencrypt/live/sdk-ip/cert.pem"),
    timer: str = "orbbec-sdk-ip-cert.timer", service: str = "orbbec-sdk-ip-cert.service",
    warning_hours: float = 24, max_check_age_hours: float = 26, timeout: float = 5,
    disk_reader=read_disk_certificate, peer_reader=read_external_certificate,
    state_reader=read_renewal_state, clock=lambda: datetime.now(UTC),
) -> dict:
    for limit in (warning_hours, max_check_age_hours, timeout):
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("limits must be finite and positive")
    if not (1 <= port <= 65535) or not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host):
        raise ValueError("invalid TLS endpoint")
    for name, suffix in ((timer, "timer"), (service, "service")):
        if not re.fullmatch(r"[A-Za-z0-9@_.:-]+\." + suffix, name):
            raise ValueError("invalid systemd unit")
    now = clock()
    if now.tzinfo is None:
        raise ValueError("clock must be timezone aware")
    now = now.astimezone(UTC)
    disk = disk_reader(cert_path)
    external = peer_reader(host, port, timeout)
    for cert in (disk, external):
        if (not re.fullmatch(r"[0-9a-f]{64}", cert.fingerprint)
                or cert.not_before.tzinfo is None or cert.not_after.tzinfo is None
                or cert.not_after <= cert.not_before):
            raise ValueError("invalid public certificate metadata")
    units = state_reader(timer, service)
    timer_state, service_state = units["timer"], units["service"]
    last = _last_check(service_state.get("ExecMainExitTimestamp", ""))
    age_hours = (now - last).total_seconds() / 3600 if last else None
    remaining = min((cert.not_after - now).total_seconds() / 3600 for cert in (disk, external))
    timer_active = timer_state.get("ActiveState") == "active"
    timer_enabled = timer_state.get("UnitFileState") in {"enabled", "enabled-runtime"}
    service_success = (
        service_state.get("Result") == "success"
        and service_state.get("ExecMainStatus") == "0"
        and service_state.get("ActiveState") in {"inactive", "active", "activating"}
    )
    alerts = []
    if disk.fingerprint != external.fingerprint:
        alerts.append("certificate_mismatch")
    if remaining <= 0:
        alerts.append("certificate_expired")
    elif remaining <= warning_hours:
        alerts.append("certificate_expiring")
    if any(cert.not_before > now for cert in (disk, external)):
        alerts.append("certificate_not_yet_valid")
    if not timer_active:
        alerts.append("renewal_timer_inactive")
    if not timer_enabled:
        alerts.append("renewal_timer_disabled")
    if not service_success:
        alerts.append("renewal_failed")
    if age_hours is None or age_hours > max_check_age_hours:
        alerts.append("renewal_check_stale")
    elif age_hours < -5 / 60:
        alerts.append("renewal_check_in_future")
    return {
        "status": "alert" if alerts else "ok", "checked_at": now.isoformat(), "host": host,
        "certificate": {
            "sha256": disk.fingerprint, "external_sha256": external.fingerprint,
            "not_before": disk.not_before.isoformat(), "not_after": disk.not_after.isoformat(),
            "remaining_hours": round(remaining, 3), "warning_hours": warning_hours,
        },
        "renewal": {
            "timer": timer, "service": service, "timer_active": timer_active,
            "timer_enabled": timer_enabled, "service_success": service_success,
            "last_check_at": last.isoformat() if last else None,
            "check_age_hours": round(age_hours, 3) if age_hours is not None else None,
            "max_check_age_hours": max_check_age_hours,
        },
        "alerts": alerts,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="47.106.112.69")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--cert-path", type=Path, default=Path("/etc/letsencrypt/live/sdk-ip/cert.pem"))
    parser.add_argument("--timer", default="orbbec-sdk-ip-cert.timer")
    parser.add_argument("--service", default="orbbec-sdk-ip-cert.service")
    parser.add_argument("--warning-hours", type=float, default=24)
    parser.add_argument("--max-check-age-hours", type=float, default=26)
    parser.add_argument("--timeout", type=float, default=5)
    arguments = parser.parse_args(argv)
    try:
        report = check_health(**vars(arguments))
    except (OSError, ValueError, KeyError, IndexError, StopIteration, subprocess.SubprocessError):
        # Provider/process diagnostics may contain private values. Emit fixed categories only.
        report = {"status": "error", "alerts": ["collection_failed"]}
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return {"ok": 0, "alert": 1, "error": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
