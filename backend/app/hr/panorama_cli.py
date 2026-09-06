from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx

from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.model_adapter import BrainModelManifest
from app.control_plane.dsn import validate_control_dsn
from app.local_secrets import read_secret_file

from .panorama_analysis import ConfiguredPanoramaModel, PanoramaAnalyzer
from .panorama_collection import PublicSourceCollector, SourceTarget
from .panorama_evidence import EvidenceArchive
from .panorama_models import CreateProductionBatch, CreateTalentSource
from .panorama_producer import PanoramaProductionPipeline
from .panorama_repository import PanoramaRepository

DEFAULT_CATALOG = Path("/data/agent-platform/hr-intelligence/source-catalog.json")


class OperatorRuntime(Protocol):
    def seed_sources(self, catalog: str | Path) -> Mapping[str, object]: ...

    async def run(self, trigger: str) -> Mapping[str, object]: ...

    async def resume(self, batch_id: UUID) -> Mapping[str, object]: ...

    def status(self, current: bool) -> Mapping[str, object]: ...


def _select_catalog_sources(
    sources: Sequence[object], source_keys: Sequence[str]
) -> tuple[object, ...]:
    if not source_keys or len(set(source_keys)) != len(source_keys):
        raise RuntimeError("panorama source catalog invalid")
    by_key = {getattr(source, "company_key", None): source for source in sources}
    if any(key not in by_key for key in source_keys):
        raise RuntimeError("panorama source catalog is incomplete")
    return tuple(by_key[key] for key in source_keys)


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError("panorama producer configuration unavailable")
    return value


def _catalog(path: str | Path) -> tuple[dict[str, object], ...]:
    try:
        value = json.loads(Path(path).read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("panorama source catalog unavailable") from None
    if not isinstance(value, dict) or set(value) != {"schema_version", "companies"}:
        raise RuntimeError("panorama source catalog invalid")
    companies = value.get("companies")
    if value.get("schema_version") != 1 or not isinstance(companies, list):
        raise RuntimeError("panorama source catalog invalid")
    required = {"company_key", "canonical_name", "aliases", "approved_urls"}
    records: list[dict[str, object]] = []
    for item in companies:
        if not isinstance(item, dict) or set(item) != required:
            raise RuntimeError("panorama source catalog invalid")
        records.append(item)
    if not records or len(records) > 100:
        raise RuntimeError("panorama source catalog invalid")
    return tuple(records)


class PanoramaOperatorRuntime:
    def __init__(
        self,
        *,
        owner_id: UUID,
        repository: PanoramaRepository,
        collector: PublicSourceCollector,
        analyzer: PanoramaAnalyzer,
        async_client: httpx.AsyncClient,
        model_client: httpx.Client,
        source_keys: Sequence[str] = (),
    ) -> None:
        self._owner_id = owner_id
        self._repository = repository
        self._collector = collector
        self._analyzer = analyzer
        self._async_client = async_client
        self._model_client = model_client
        self._source_keys = tuple(source_keys)

    async def aclose(self) -> None:
        await self._async_client.aclose()
        self._model_client.close()

    def close(self) -> None:
        asyncio.run(self.aclose())

    def seed_sources(self, catalog: str | Path) -> Mapping[str, object]:
        existing = {
            source.company_key: source
            for source in self._repository.list_sources(
                self._owner_id, include_inactive=True, limit=100
            )
        }
        created = 0
        retained = 0
        updated = 0
        for item in _catalog(catalog):
            company_key = str(item["company_key"])
            command = CreateTalentSource(
                source_id=uuid5(
                    NAMESPACE_URL,
                    f"orbbec:panorama:source:{self._owner_id}:{company_key}",
                ),
                owner_id=self._owner_id,
                client_request_id=uuid5(
                    NAMESPACE_URL,
                    f"orbbec:panorama:source-request:{self._owner_id}:{company_key}",
                ),
                company_key=company_key,
                canonical_name=str(item["canonical_name"]),
                aliases=tuple(item["aliases"]),  # type: ignore[arg-type]
                approved_urls=tuple(item["approved_urls"]),  # type: ignore[arg-type]
                active=True,
            )
            current = existing.get(company_key)
            if current is not None:
                command = CreateTalentSource(
                    source_id=current.source_id,
                    owner_id=self._owner_id,
                    client_request_id=current.client_request_id,
                    company_key=company_key,
                    canonical_name=command.canonical_name,
                    aliases=command.aliases,
                    approved_urls=command.approved_urls,
                    active=True,
                )
                if (
                    current.canonical_name != command.canonical_name
                    or current.aliases != command.aliases
                    or current.approved_urls != command.approved_urls
                    or not current.active
                ):
                    self._repository.reconcile_source(command)
                    updated += 1
                retained += 1
                continue
            self._repository.create_source(command)
            created += 1
        return {"created": created, "existing": retained, "updated": updated}

    async def run(self, trigger: str) -> Mapping[str, object]:
        if trigger not in {"schedule", "operator"}:
            raise ValueError("panorama trigger invalid")
        stored_sources = self._repository.list_sources(self._owner_id, limit=100)
        sources = _select_catalog_sources(stored_sources, self._source_keys)
        if not sources:
            raise RuntimeError("panorama source catalog is empty")
        batch = self._repository.create_production_batch(
            CreateProductionBatch(
                batch_id=uuid4(),
                owner_id=self._owner_id,
                client_request_id=uuid4(),
                selected_source_ids=tuple(source.source_id for source in sources),
                trigger_kind=trigger,  # type: ignore[arg-type]
                analyzer_version=self._analyzer.model_version,
            )
        )
        return await self._deliver(batch, sources)

    async def resume(self, batch_id: UUID) -> Mapping[str, object]:
        batch = self._repository.production_batch(self._owner_id, batch_id)
        if batch.state == "failed" and batch.error_code == "analysis_failed":
            batch = self._repository.retry_production_analysis(
                self._owner_id,
                batch_id,
                expected_row_version=batch.row_version,
            )
        if batch.state not in {"queued", "running", "analyzing"}:
            raise RuntimeError("panorama batch cannot be resumed")
        sources = self._repository.sources_for_run(
            self._owner_id, batch.selected_source_ids
        )
        return await self._deliver(batch, sources)

    def status(self, current: bool) -> Mapping[str, object]:
        if not current:
            raise ValueError("only current publication status is supported")
        publication = self._repository.current_publication()
        if publication is None:
            return {"published": False}
        return {
            "published": True,
            "publication_id": str(publication.publication_id),
            "batch_id": str(publication.batch_id),
            "insight_version_id": str(publication.insight_version_id),
            "coverage_state": publication.coverage_state,
            "published_at": publication.published_at.isoformat(),
        }

    async def _deliver(self, batch, sources) -> Mapping[str, object]:
        targets = tuple(
            SourceTarget(
                source_id=source.source_id,
                company_name=source.canonical_name,
                source_url=url,
                approved_urls=source.approved_urls,
            )
            for source in sources
            for url in source.approved_urls
        )
        delivery = await PanoramaProductionPipeline(
            batch=batch,
            targets=targets,
            collector=self._collector,
            analyzer=self._analyzer,
            repository=self._repository,
        ).run()
        return {
            "batch_id": str(delivery.batch_id),
            "insight_version_id": str(delivery.insight_version_id),
            "snapshot_count": delivery.snapshot_count,
            "successful_source_count": delivery.successful_source_count,
            "failed_source_count": delivery.failed_source_count,
            "coverage_state": delivery.coverage_state,
        }


def build_runtime() -> PanoramaOperatorRuntime:
    database_url = read_secret_file(
        _required_environment("PLATFORM_CONTROL_DATABASE_URL_FILE")
    )
    validate_control_dsn(database_url, purpose="app")
    owner_id = UUID(_required_environment("PLATFORM_HR_PANORAMA_OWNER_ID"))
    manifest_path = os.getenv(
        "PLATFORM_BRAIN_MODEL_MANIFEST",
        os.getenv("PLATFORM_BRAIN_MODEL_MANIFEST_PATH", ""),
    ).strip()
    if not manifest_path:
        raise RuntimeError("panorama producer configuration unavailable")
    manifest = BrainModelManifest.load(Path(manifest_path))
    model_client = httpx.Client(
        timeout=httpx.Timeout(310, connect=10),
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
    )
    auth_scheme = _required_environment("PLATFORM_BRAIN_PROVIDER_AUTH_SCHEME")
    if auth_scheme not in {"x-api-key", "bearer"}:
        raise RuntimeError("panorama producer configuration unavailable")
    adapter = AnthropicMessagesAdapter.from_secret_file(
        base_url=_required_environment("PLATFORM_BRAIN_PROVIDER_BASE_URL"),
        api_key_file=_required_environment("PLATFORM_BRAIN_PROVIDER_API_KEY_FILE"),
        auth_scheme=cast(Literal["x-api-key", "bearer"], auth_scheme),
        client=model_client,
    )
    async_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=4)
    )
    archive = EvidenceArchive(production=True)
    catalog_path = os.getenv(
        "PLATFORM_HR_PANORAMA_SOURCE_CATALOG", str(DEFAULT_CATALOG)
    )
    return PanoramaOperatorRuntime(
        owner_id=owner_id,
        repository=PanoramaRepository(database_url),
        collector=PublicSourceCollector(async_client, archive),
        analyzer=PanoramaAnalyzer(ConfiguredPanoramaModel(adapter, manifest)),
        async_client=async_client,
        model_client=model_client,
        source_keys=tuple(str(item["company_key"]) for item in _catalog(catalog_path)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hr-panorama-producer",
        description="HR 招聘情报后台生产与发布工具",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed-sources", help="幂等导入受控来源目录")
    seed.add_argument(
        "--catalog",
        default=os.getenv("PLATFORM_HR_PANORAMA_SOURCE_CATALOG", str(DEFAULT_CATALOG)),
    )
    run = commands.add_parser("run", help="创建并执行后台生产批次")
    run.add_argument("--trigger", choices=("schedule", "operator"), default="operator")
    resume = commands.add_parser("resume", help="恢复未完成后台批次")
    resume.add_argument("batch_id", type=UUID)
    status = commands.add_parser("status", help="读取已发布版本状态")
    status.add_argument("--current", action="store_true", required=True)
    return parser


async def _run_and_close(awaitable, runtime: OperatorRuntime):
    operation_failed = False
    try:
        return await awaitable
    except BaseException:
        operation_failed = True
        raise
    finally:
        try:
            async_close = getattr(runtime, "aclose", None)
            if async_close is not None:
                await async_close()
            else:
                close = getattr(runtime, "close", None)
                if close is not None:
                    close()
        except Exception:
            if not operation_failed:
                raise


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime: OperatorRuntime | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    selected = runtime or build_runtime()
    owns_runtime = runtime is None
    if arguments.command in {"run", "resume"}:
        awaitable = (
            selected.run(arguments.trigger)
            if arguments.command == "run"
            else selected.resume(arguments.batch_id)
        )
        result = asyncio.run(
            _run_and_close(awaitable, selected) if owns_runtime else awaitable
        )
    else:
        try:
            if arguments.command == "seed-sources":
                result = selected.seed_sources(arguments.catalog)
            else:
                result = selected.status(arguments.current)
        finally:
            if owns_runtime:
                close = getattr(selected, "close", None)
                if close is not None:
                    close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CATALOG",
    "PanoramaOperatorRuntime",
    "build_parser",
    "build_runtime",
    "main",
]
