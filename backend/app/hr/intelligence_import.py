from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .intelligence_bundle import VerifiedImportBundle, verify_import_bundle


class IntelligenceImportRepository(Protocol):
    def bundle_by_manifest(
        self,
        owner_id: UUID,
        manifest_sha256: str,
    ) -> Mapping[str, object] | None: ...

    def import_verified_bundle(
        self,
        owner_id: UUID,
        bundle: VerifiedImportBundle,
    ) -> Mapping[str, object]: ...


class IntelligenceBundleImporter:
    def __init__(self, repository: IntelligenceImportRepository) -> None:
        if not callable(
            getattr(repository, "bundle_by_manifest", None)
        ) or not callable(getattr(repository, "import_verified_bundle", None)):
            raise TypeError("intelligence import repository required")
        self._repository = repository

    def import_bundle(
        self,
        path: str | Path,
        *,
        owner_id: UUID,
        expected_bundle_id: UUID | None = None,
    ) -> Mapping[str, object]:
        if not isinstance(owner_id, UUID):
            raise TypeError("intelligence owner required")
        verified = verify_import_bundle(path, expected_bundle_id=expected_bundle_id)
        existing = self._repository.bundle_by_manifest(
            owner_id,
            verified.manifest_sha256,
        )
        if existing is not None:
            if existing.get("bundle_id") != verified.bundle_id:
                raise ValueError("intelligence import idempotency mismatch")
            return existing
        return self._repository.import_verified_bundle(owner_id, verified)


class DatabaseIntelligenceImportRepository:
    def __init__(self, connection: Callable[[], object]) -> None:
        if not callable(connection):
            raise TypeError("database connection factory required")
        self._connection = connection

    def bundle_by_manifest(
        self,
        owner_id: UUID,
        manifest_sha256: str,
    ) -> Mapping[str, object] | None:
        with self._connection() as connection:
            return connection.execute(
                "select * from platform_hr.read_intelligence_bundle_by_manifest_v85(%s,%s)",
                (owner_id, manifest_sha256),
            ).fetchone()

    def import_verified_bundle(
        self,
        owner_id: UUID,
        bundle: VerifiedImportBundle,
    ) -> Mapping[str, object]:
        with self._connection() as connection:
            row = connection.execute(
                "select (platform_hr.import_intelligence_bundle_v87("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)).*",
                (
                    owner_id,
                    bundle.bundle_id,
                    bundle.manifest_sha256,
                    bundle.bundle_locator,
                    bundle.generated_at,
                    Jsonb(bundle.manifest),
                    Jsonb(bundle.catalog),
                    Jsonb(bundle.coverage),
                    Jsonb(list(bundle.jobs)),
                    Jsonb(bundle.aggregates),
                    Jsonb(list(bundle.analysis)),
                    Jsonb(list(bundle.usage)),
                    Jsonb(list(bundle.evidence_index)),
                    Jsonb(list(bundle.agent_chunk_index)),
                    Jsonb(bundle.agent_document_index),
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("intelligence import returned no record")
        return row


def _secret(path: str) -> str:
    selected = Path(path)
    if not selected.is_absolute() or selected.is_symlink() or not selected.is_file():
        raise ValueError("database secret path invalid")
    value = selected.read_text("utf-8").strip()
    if not value:
        raise ValueError("database secret empty")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hr-intelligence-import")
    parser.add_argument(
        "--bundle", default=os.getenv("PLATFORM_HR_INTELLIGENCE_BUNDLE_PATH", "/bundle")
    )
    parser.add_argument(
        "--owner-id", default=os.getenv("PLATFORM_HR_PANORAMA_OWNER_ID")
    )
    parser.add_argument("--expected-bundle-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        owner_id = UUID(str(args.owner_id))
        expected_bundle_id = UUID(args.expected_bundle_id)
    except (TypeError, ValueError):
        raise ValueError("intelligence import identity invalid") from None
    database_url = _secret(
        os.environ.get(
            "PLATFORM_CONTROL_DATABASE_URL_FILE",
            "/run/control-secrets/control-database-url",
        )
    )
    repository = DatabaseIntelligenceImportRepository(
        lambda: psycopg.connect(database_url, row_factory=dict_row)
    )
    record = IntelligenceBundleImporter(repository).import_bundle(
        Path(args.bundle),
        owner_id=owner_id,
        expected_bundle_id=expected_bundle_id,
    )
    print(json.dumps({"bundle_id": str(record["bundle_id"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
