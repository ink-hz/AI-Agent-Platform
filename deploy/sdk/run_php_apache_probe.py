#!/usr/bin/env python3
"""Observe one isolated Apache hard-OOM exit without extra container privileges."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path


def parse_wait_exit(trace: str, worker_pid: int) -> dict:
    completed = [line for line in trace.splitlines() if "wait4(" in line and re.search(r"=\s*[1-9]\d*\s*$", line)]
    if any(not re.search(r"=\s*" + str(worker_pid) + r"\s*$", line) for line in completed):
        raise ValueError("unexpected_child_exit")
    rows = [line for line in trace.splitlines()
            if "wait4(" in line and re.search(r"=\s*" + str(worker_pid) + r"\s*$", line)]
    if not rows:
        raise ValueError("oom_exit_unproven")
    if len(rows) != 1 or "WIFEXITED(s)" not in rows[0] or not re.search(r"WEXITSTATUS\(s\)\s*==\s*1\b", rows[0]):
        raise ValueError("oom_exit_rejected")
    return {"exit_code": 1, "signal": 0, "core": False}


def validate_httpd_identity(init: dict, parent: dict, cid: str, host_namespace: str) -> None:
    if (parent["ppid"] != init["pid"] or parent["pidns"] != init["pidns"]
            or parent["pidns"] == host_namespace or parent["cgroup"] != init["cgroup"]
            or cid not in parent["cgroup"] or parent["exe"] != "/opt/zbox/run/apache/httpd"):
        raise ValueError("apache_identity_invalid")


def _identity(pid: int) -> dict:
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
    ns_line = next(line for line in (proc / "status").read_text().splitlines() if line.startswith("NSpid:"))
    return {"pid": pid, "ppid": int(stat[1]), "start_ticks": stat[19],
            "pidns": os.readlink(proc / "ns/pid"), "cgroup": (proc / "cgroup").read_text().strip(),
            "exe": os.readlink(proc / "exe"), "namespace_pids": [int(value) for value in ns_line.split()[1:]]}


def _docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def _state(cid):
    return json.loads(_docker("inspect", "--format", '{"state":{{json .State}},"restarts":{{.RestartCount}},"image":{{json .Image}}}', cid))


def _logs(cid):
    result = subprocess.run(["docker", "logs", cid], capture_output=True, text=True, check=True)
    return result.stdout + result.stderr


def _await(predicate, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError("probe_observer_timeout")


def _memory_events(identity):
    line = next(row for row in identity["cgroup"].splitlines() if row.startswith("0::/"))
    path = Path("/sys/fs/cgroup") / line[3:].lstrip("/") / "memory.events"
    return {key: int(value) for key, value in (row.split() for row in path.read_text().splitlines())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", args.image) is None:
        parser.error("an immutable local candidate image ID is required")
    base = Path("/data/security-build-20261029/php81")
    attempt = Path(tempfile.mkdtemp(prefix="apache-attempt-", dir=base))
    support = attempt / "support"
    support.mkdir(mode=0o755)
    name = "sdk-php-acceptance-" + attempt.name.removeprefix("apache-attempt-")
    cid = _docker("run", "-d", "--name", name, "--no-healthcheck", "--network", "none", "--cpus=2", "--memory=3g",
                  "--memory-swap=3g", "--pids-limit=256", "--security-opt=no-new-privileges:true",
                  "--mount", f"type=bind,source={support},target=/test-support,readonly",
                  "--entrypoint", "/bin/sh", args.image, "/usr/local/share/sdk-php/probe-php-apache.sh")
    tracer = None
    evidence = {"container_id": cid, "container_name": name, "image": args.image, "status": "failed"}
    try:
        def ready_marker():
            state = _state(cid)
            if not state["state"]["Running"]:
                raise RuntimeError("probe_exited_before_observer")
            return re.search(r"PHP_PROBE_WAITING_OOM parent_pid=(\d+) worker_pid=(\d+)", _logs(cid))

        marker = _await(ready_marker)
        initial = _state(cid)
        init = _identity(initial["state"]["Pid"])
        rows = [line.split() for line in _docker("top", cid, "-eo", "pid,ppid,comm").splitlines()[1:]]
        candidates = [int(row[0]) for row in rows if int(row[1]) == init["pid"] and row[2] == "httpd"]
        if len(candidates) != 1:
            raise ValueError("apache_parent_ambiguous")
        parent = _identity(candidates[0])
        validate_httpd_identity(init, parent, cid, os.readlink("/proc/1/ns/pid"))
        if parent["namespace_pids"][-1] != int(marker[1]):
            raise ValueError("apache_namespace_pid_mismatch")
        workers = [int(row[0]) for row in rows if int(row[1]) == parent["pid"] and row[2] == "httpd"]
        if len(workers) != 1:
            raise ValueError("apache_worker_ambiguous")
        worker = _identity(workers[0])
        if (worker["pidns"] != parent["pidns"] or worker["cgroup"] != parent["cgroup"]
                or worker["namespace_pids"][-1] != int(marker[2])):
            raise ValueError("apache_worker_identity_invalid")
        before_events = _memory_events(init)
        trace_path = attempt / "wait-status.log"
        with (attempt / "strace.stderr").open("w") as errors:
            tracer = subprocess.Popen(["strace", "-qq", "-e", "trace=wait4", "-e", "signal=SIGCHLD",
                                       "-o", str(trace_path), "-p", str(parent["pid"])], stderr=errors)
            _await(lambda: f"TracerPid:\t{tracer.pid}" in Path(f"/proc/{parent['pid']}/status").read_text(), 5)
            if _identity(parent["pid"])["start_ticks"] != parent["start_ticks"]:
                raise ValueError("apache_parent_changed")
            (support / "observer-ready").write_text("ready\n")

            def observed_exit():
                if not _state(cid)["state"]["Running"]:
                    raise RuntimeError("probe_exited_during_oom")
                try:
                    return parse_wait_exit(trace_path.read_text(), int(marker[2]))
                except ValueError as error:
                    if str(error) == "oom_exit_unproven":
                        return None
                    raise

            exit_status = _await(observed_exit, 20)
            after_oom = _state(cid)
            after_events = _memory_events(init)
            if (after_oom["restarts"] != 0 or after_oom["state"]["OOMKilled"]
                    or after_oom["state"]["Pid"] != init["pid"]
                    or _identity(parent["pid"])["start_ticks"] != parent["start_ticks"]
                    or after_events.get("oom_kill", 0) != before_events.get("oom_kill", 0)
                    or after_events.get("oom", 0) != before_events.get("oom", 0)):
                raise ValueError("oom_container_or_parent_changed")
            tracer.send_signal(signal.SIGINT)
            tracer.wait(timeout=5)
            tracer = None
            (support / "oom-verified").write_text("exit=1 signal=0 core=0\n")
            code = subprocess.check_output(["docker", "wait", cid], text=True, timeout=40).strip()
            final = _state(cid)
            (attempt / "probe.log").write_text(_logs(cid))
            if code != "0" or final["state"]["OOMKilled"] or final["restarts"] != 0:
                raise ValueError("apache_probe_failed")
            evidence.update(status="ok", apache_parent=parent, original_worker=worker,
                            wait_status=exit_status, memory_events_before=before_events,
                            memory_events_after=after_events, final_state=final)
    except Exception as error:
        evidence["failure"] = str(error)
        raise
    finally:
        if tracer is not None and tracer.poll() is None:
            tracer.send_signal(signal.SIGINT)
            tracer.wait(timeout=5)
        if evidence["status"] != "ok":
            subprocess.run(["docker", "stop", "-t", "1", cid], check=False, stdout=subprocess.DEVNULL)
            (attempt / "probe.log").write_text(_logs(cid))
        (attempt / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(str(attempt), flush=True)


if __name__ == "__main__":
    main()
