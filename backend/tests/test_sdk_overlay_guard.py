from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "deploy" / "sdk" / "overlay_guard.py"


def guard():
    spec = importlib.util.spec_from_file_location("sdk_overlay_guard", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configs():
    old = {"services": {
        "platform-api": {"image": "sha256:old", "environment": {"PLATFORM_HR_AGENT_ENABLED": "1"},
                         "volumes": ["/data/hr:/data/hr:ro"], "networks": {"internal": None}},
        "platform-brain": {"image": "sha256:brain", "environment": {"BRAIN": "1"}},
    }, "networks": {"internal": {}}}
    new = {"services": {
        "platform-api": {"image": "orbbec-agent-platform:" + "a" * 40,
                         "environment": {"PLATFORM_HR_AGENT_ENABLED": "1", "PLATFORM_SDK_PLAN_ENABLED": "1"},
                         "volumes": ["/data/hr:/data/hr:ro"], "networks": {"internal": None}},
        "platform-brain": {"image": "sha256:brain", "environment": {"BRAIN": "1"}},
    }, "networks": {"internal": {}}}
    return old, new


def test_sdk_plan_overlay_allows_only_api_image_and_plan_flag():
    module = guard()
    old, new = configs()
    assert module.compare_configs(old, new, "sha256:old", "orbbec-agent-platform:" + "a" * 40) == 2
    old["services"]["platform-api"]["image"] = "orbbec-agent-platform:" + "b" * 40
    assert module.compare_configs(old, new, "orbbec-agent-platform:" + "b" * 40,
                                  "orbbec-agent-platform:" + "a" * 40) == 2


@pytest.mark.parametrize("mutation", ["hr_mount", "brain_image", "extra_environment", "wrong_flag", "wrong_image"])
def test_sdk_plan_overlay_rejects_other_changes(mutation):
    module = guard()
    old, new = configs()
    if mutation == "hr_mount": new["services"]["platform-api"]["volumes"] = []
    elif mutation == "brain_image": new["services"]["platform-brain"]["image"] = "sha256:newbrain"
    elif mutation == "extra_environment": new["services"]["platform-api"]["environment"]["PLATFORM_DAQ_ENABLED"] = "0"
    elif mutation == "wrong_flag": new["services"]["platform-api"]["environment"]["PLATFORM_SDK_PLAN_ENABLED"] = "0"
    else: new["services"]["platform-api"]["image"] = "orbbec-agent-platform:" + "b" * 40
    with pytest.raises(module.OverlayGuardError):
        module.compare_configs(old, new, "sha256:old", "orbbec-agent-platform:" + "a" * 40)
