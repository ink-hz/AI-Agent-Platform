#!/usr/bin/env python3
"""Fail closed unless an SDK plan Compose overlay changes only the API image and flag."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys


API_CONTAINER = "orbbec-agent-platform-platform-api-1"
PROJECT = "orbbec-agent-platform"
ENV_FILE = "/opt/orbbec-agent-platform/private/platform.env"
PLAN_FLAG = "PLATFORM_SDK_PLAN_ENABLED"
IMAGE = re.compile(r"orbbec-agent-platform:[0-9a-f]{40}\Z")


class OverlayGuardError(ValueError):
    pass


def compare_configs(baseline: dict, candidate: dict, current_image: str, target_image: str) -> int:
    if IMAGE.fullmatch(target_image) is None:
        raise OverlayGuardError("candidate_image_invalid")
    old = copy.deepcopy(baseline)
    new = copy.deepcopy(candidate)
    try:
        old_api = old["services"]["platform-api"]
        new_api = new["services"]["platform-api"]
        if old_api.pop("image") != current_image or new_api.pop("image") != target_image:
            raise OverlayGuardError("image_mismatch")
        if new_api["environment"].pop(PLAN_FLAG) != "1":
            raise OverlayGuardError("plan_flag_disabled")
        old_api.get("environment", {}).pop(PLAN_FLAG, None)
    except (KeyError, TypeError):
        raise OverlayGuardError("compose_shape_invalid") from None
    if old != new:
        raise OverlayGuardError("unrelated_compose_change")
    return len(old["services"])


def _run(command: list[str], *, environment: dict[str, str]) -> str:
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=40, check=False)
    if result.returncode:
        raise OverlayGuardError("docker_command_failed")
    return result.stdout


def preflight(overlay_path: Path, target_image: str, expected_container_id: str) -> dict:
    if not overlay_path.is_absolute() or not overlay_path.is_file() or overlay_path.is_symlink():
        raise OverlayGuardError("overlay_file_invalid")
    if re.fullmatch(r"[0-9a-f]{64}", expected_container_id) is None:
        raise OverlayGuardError("container_id_invalid")
    expected_overlay = {"services": {"platform-api": {"image": target_image, "environment": {PLAN_FLAG: "1"}}}}
    if json.loads(overlay_path.read_text(encoding="utf-8")) != expected_overlay:
        raise OverlayGuardError("overlay_content_invalid")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PLATFORM_") and not key.startswith("COMPOSE_")}
    inspection = json.loads(_run(["docker", "inspect", API_CONTAINER], environment=environment))[0]
    if inspection["Id"] != expected_container_id or not inspection["State"]["Running"]:
        raise OverlayGuardError("running_container_changed")
    labels = inspection["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != PROJECT:
        raise OverlayGuardError("compose_project_mismatch")
    paths = labels.get("com.docker.compose.project.config_files", "").split(",")
    if len(paths) < 2 or any(not Path(path).is_absolute() or not Path(path).is_file() or Path(path).is_symlink() for path in paths):
        raise OverlayGuardError("compose_files_invalid")
    actual_environment = dict(entry.split("=", 1) for entry in inspection["Config"]["Env"] if "=" in entry)
    # HR's model selection was supplied at its original Compose invocation rather
    # than persisted in platform.env. Keep that runtime choice during this release.
    if "PLATFORM_HR_AGENT_MODEL" in actual_environment:
        environment["PLATFORM_HR_AGENT_MODEL"] = actual_environment["PLATFORM_HR_AGENT_MODEL"]
    command = ["docker", "compose", "--project-name", PROJECT, "--env-file", ENV_FILE]
    for path in paths:
        command.extend(["-f", path])
    baseline = json.loads(_run([*command, "config", "--format", "json"], environment=environment))
    candidate = json.loads(_run([*command, "-f", str(overlay_path), "config", "--format", "json"], environment=environment))
    configured_image = inspection["Config"]["Image"]
    if _run(["docker", "image", "inspect", configured_image, "--format", "{{.Id}}"], environment=environment).strip() != inspection["Image"]:
        raise OverlayGuardError("running_image_reference_drift")
    service_count = compare_configs(baseline, candidate, configured_image, target_image)
    if any(actual_environment.get(key) != str(value) for key, value in baseline["services"]["platform-api"]["environment"].items()):
        raise OverlayGuardError("running_environment_drift")
    return {"services": service_count, "compose_files": len(paths), "current_image": inspection["Image"],
            "target_image": target_image, "container_id": inspection["Id"],
            "runtime_overrides": ["PLATFORM_HR_AGENT_MODEL"] if "PLATFORM_HR_AGENT_MODEL" in actual_environment else []}


def main(args: list[str]) -> int:
    try:
        if len(args) != 3:
            raise OverlayGuardError("usage")
        print("SDK_OVERLAY_CONFIG_OK " + json.dumps(preflight(Path(args[0]), args[1], args[2]), sort_keys=True))
    except (OverlayGuardError, OSError, ValueError, subprocess.TimeoutExpired):
        print("SDK_OVERLAY_CONFIG_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
