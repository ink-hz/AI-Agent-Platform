from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from app.hr.panorama_cli import build_parser, main

CATALOG = Path(__file__).parents[1] / "app/hr/panorama_source_catalog.v1.json"


class Runtime:
    def __init__(self) -> None:
        self.calls = []

    def seed_sources(self, catalog):
        self.calls.append(("seed-sources", catalog))
        return {"created": 10, "existing": 2}

    async def run(self, trigger):
        self.calls.append(("run", trigger))
        return {"batch_id": str(uuid4()), "coverage_state": "partial"}

    async def resume(self, batch_id):
        self.calls.append(("resume", batch_id))
        return {"batch_id": str(batch_id), "coverage_state": "complete"}

    def status(self, current):
        self.calls.append(("status", current))
        return {"published": True}


def test_cli_contains_only_operator_background_commands() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    for command in ("seed-sources", "run", "resume", "status"):
        assert command in help_text
    for forbidden in ("立即更新", "conversation", "web-trigger"):
        assert forbidden not in help_text


def test_bundled_catalog_contains_priority_and_session_companies() -> None:
    document = json.loads(CATALOG.read_text("utf-8"))
    companies = {item["canonical_name"] for item in document["companies"]}

    assert {
        "联合光电",
        "速腾聚创",
        "禾赛科技",
        "拓竹",
        "创想三维",
        "智能派",
        "知象光电",
        "先临三维",
        "思看科技",
        "智元机器人",
        "影石创新",
        "华为",
    } <= companies


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["seed-sources", "--catalog", "/data/catalog.json"], "seed-sources"),
        (["run", "--trigger", "schedule"], "run"),
        (["resume", "00000000-0000-4000-8000-000000000001"], "resume"),
        (["status", "--current"], "status"),
    ],
)
def test_cli_dispatches_commands_and_prints_machine_readable_status(
    argv, expected, capsys
) -> None:
    runtime = Runtime()

    assert main(argv, runtime=runtime) == 0

    assert runtime.calls[0][0] == expected
    output = json.loads(capsys.readouterr().out)
    assert isinstance(output, dict)
