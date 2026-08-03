from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import subprocess
import sys

from app.config import load_config
from app.review.database import resolve_review_database_url
from app.review.repository import PsycopgReviewRepository

from .config import default_sources
from .export import ExportError, export_source
from .importer import ReviewBackfillError, import_bundle, import_bundle_with_review


def _keychain_value(
    account: str,
    service: str,
    *,
    runner=subprocess.run,
) -> str:
    try:
        result = runner(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                account,
                "-s",
                service,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("sync_database_unavailable") from error
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("sync_database_unavailable")
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync remote Agent observability data")
    parser.add_argument("--source", choices=("fae", "admin", "all"), default="all")
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args(argv)
    config = load_config()
    sources = default_sources(config.remote_ssh_host, config.remote_ssh_key_path)
    selected = tuple(sources) if args.source == "all" else (args.source,)
    database_url: str | None = None
    review_database_url: str | None = None
    if not args.export_only:
        database_url = config.sync_database_url
        if not database_url:
            try:
                database_url = _keychain_value(
                    config.sync_keychain_account,
                    config.sync_keychain_service,
                )
            except RuntimeError as error:
                print(str(error), file=sys.stderr)
                return 1
        review_database_url = resolve_review_database_url(config)

    failed = False
    for kind in selected:
        try:
            bundle = export_source(sources[kind])
            if args.export_only:
                print(f"{kind}: exported {bundle.source_counts}")
                continue
            assert database_url is not None
            if review_database_url:
                coordinated = import_bundle_with_review(
                    database_url,
                    bundle,
                    review_repository=PsycopgReviewRepository(review_database_url),
                    actor="codex",
                )
                print(json.dumps(
                    {"source_kind": kind, "source_sync": asdict(coordinated.source_sync)},
                    ensure_ascii=False,
                    sort_keys=True,
                ))
                print(json.dumps(
                    {"source_kind": kind, "review_backfill": {"status": "succeeded", **asdict(coordinated.review_backfill)}},
                    ensure_ascii=False,
                    sort_keys=True,
                ))
            else:
                result = import_bundle(database_url, bundle)
                print(json.dumps(
                    {"source_kind": kind, "source_sync": asdict(result)},
                    ensure_ascii=False,
                    sort_keys=True,
                ))
                print(json.dumps(
                    {"source_kind": kind, "review_backfill": {"status": "failed", "reason": "review_database_unavailable"}},
                    ensure_ascii=False,
                    sort_keys=True,
                ), file=sys.stderr)
                failed = True
        except ReviewBackfillError as error:
            failed = True
            print(json.dumps(
                {"source_kind": kind, "source_sync": asdict(error.source_sync)},
                ensure_ascii=False,
                sort_keys=True,
            ))
            print(json.dumps(
                {"source_kind": kind, "review_backfill": {"status": "failed", "reason": error.reason}},
                ensure_ascii=False,
                sort_keys=True,
            ), file=sys.stderr)
        except (ExportError, Exception) as error:  # sanitized below
            failed = True
            category = (
                str(error)
                if isinstance(error, ExportError)
                else "sync_failed"
            )
            print(f"{kind}: {category}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
