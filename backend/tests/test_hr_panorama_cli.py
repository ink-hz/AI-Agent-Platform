from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.hr.panorama_cli import (
    PanoramaOperatorRuntime,
    _select_catalog_sources,
    build_parser,
    main,
)
from app.hr.panorama_models import TalentSource

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
    robosense = next(
        item for item in document["companies"] if item["canonical_name"] == "速腾聚创"
    )
    assert {
        "https://app.mokahr.com/social-recruitment/robosense/77883",
        "https://app.mokahr.com/campus-recruitment/robosense/69887",
    } <= set(robosense["approved_urls"])
    scantech = next(
        item for item in document["companies"] if item["canonical_name"] == "思看科技"
    )
    assert any("sikankeji/100000204" in url for url in scantech["approved_urls"])
    assert any("sikankeji/100000205" in url for url in scantech["approved_urls"])
    huawei = next(
        item for item in document["companies"] if item["canonical_name"] == "华为"
    )
    assert "https://career.huawei.com/reccampportal/portal5/social-recruitment.html" in huawei["approved_urls"]
    assert "https://career.huawei.com/reccampportal/portal5/campus-recruitment.html" in huawei["approved_urls"]
    assert any("huawei-special-recruitment.html" in url for url in huawei["approved_urls"])
    insta360 = next(
        item for item in document["companies"] if item["canonical_name"] == "影石创新"
    )
    assert {
        "https://arashivision.jobs.feishu.cn/socialENG",
        "https://arashivision.jobs.feishu.cn/campus",
    } <= set(insta360["approved_urls"])


def test_production_run_selects_only_bundled_catalog_keys_in_catalog_order() -> None:
    legacy = SimpleNamespace(company_key="company-legacy")
    huawei = SimpleNamespace(company_key="huawei")
    hesai = SimpleNamespace(company_key="hesai")

    assert _select_catalog_sources((legacy, hesai, huawei), ("huawei", "hesai")) == (
        huawei,
        hesai,
    )

    with pytest.raises(RuntimeError, match="catalog is incomplete"):
        _select_catalog_sources((hesai,), ("huawei", "hesai"))


def test_seed_sources_reconciles_changed_catalog_without_replacing_identity(
    tmp_path,
) -> None:
    owner_id = uuid4()
    source_id = uuid4()
    existing = TalentSource(
        source_id,
        owner_id,
        uuid4(),
        "company",
        "robosense",
        "速腾聚创",
        ("RoboSense",),
        ("https://www.robosense.cn/about/joinus",),
        True,
        datetime.now(timezone.utc),
        datetime.now(timezone.utc),
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": [
                    {
                        "company_key": "robosense",
                        "canonical_name": "速腾聚创",
                        "aliases": ["RoboSense"],
                        "approved_urls": [
                            "https://www.robosense.cn/about/joinus",
                            "https://app.mokahr.com/social-recruitment/robosense/77883",
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )

    class Repository:
        def __init__(self) -> None:
            self.reconciled = []

        def list_sources(self, selected_owner_id, **_kwargs):
            assert selected_owner_id == owner_id
            return (existing,)

        def reconcile_source(self, command):
            self.reconciled.append(command)
            return command

        def create_source(self, _command):
            raise AssertionError("existing source must not be recreated")

    repository = Repository()
    runtime = PanoramaOperatorRuntime(
        owner_id=owner_id,
        repository=repository,
        collector=object(),
        analyzer=object(),
        async_client=object(),
        model_client=object(),
    )

    assert runtime.seed_sources(catalog) == {"created": 0, "existing": 1, "updated": 1}
    assert repository.reconciled[0].source_id == source_id
    assert repository.reconciled[0].approved_urls[-1].startswith(
        "https://app.mokahr.com/"
    )


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


@pytest.mark.asyncio
async def test_runtime_retries_a_failed_analysis_without_recollecting() -> None:
    owner_id, batch_id, source_id = uuid4(), uuid4(), uuid4()
    failed = SimpleNamespace(
        state="failed",
        error_code="analysis_failed",
        row_version=4,
        selected_source_ids=(source_id,),
    )
    analyzing = SimpleNamespace(
        state="analyzing",
        error_code=None,
        row_version=5,
        selected_source_ids=(source_id,),
    )

    class Repository:
        def __init__(self) -> None:
            self.retry_calls = []

        def production_batch(self, selected_owner_id, selected_batch_id):
            assert (selected_owner_id, selected_batch_id) == (owner_id, batch_id)
            return failed

        def retry_production_analysis(
            self, selected_owner_id, selected_batch_id, *, expected_row_version
        ):
            self.retry_calls.append(
                (selected_owner_id, selected_batch_id, expected_row_version)
            )
            return analyzing

        def sources_for_run(self, selected_owner_id, selected_source_ids):
            assert (selected_owner_id, selected_source_ids) == (
                owner_id,
                (source_id,),
            )
            return ("source",)

    repository = Repository()
    runtime = object.__new__(PanoramaOperatorRuntime)
    runtime._owner_id = owner_id
    runtime._repository = repository

    async def deliver(batch, sources):
        assert batch is analyzing
        assert sources == ("source",)
        return {"batch_id": str(batch_id)}

    runtime._deliver = deliver

    result = await runtime.resume(batch_id)

    assert result == {"batch_id": str(batch_id)}
    assert repository.retry_calls == [(owner_id, batch_id, 4)]
