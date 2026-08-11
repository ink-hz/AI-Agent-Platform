from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from app.config import load_config
from app.local_secrets import SecretFileUnavailable, read_secret_file
from app.review.database import resolve_review_database_url
from app.review.handoff import (
    HandoffImporter,
    OutboxItemError,
    list_outbox_items,
    load_outbox_item,
)
from app.review.repository import PsycopgReviewRepository
from app.registry.repository import YamlRepository

from .config import default_sources
from .export import ExportError, export_source
from .importer import ReviewBackfillError, import_bundle, import_bundle_with_review


HANDOFF_STATES = (
    "prepared",
    "pending",
    "acknowledged",
    "blocked",
    "terminal_failed",
)


def sync_feedback_closure_outbox(directory: Path, importer) -> dict[str, int]:
    summary = {state: 0 for state in HANDOFF_STATES}
    summary["invalid"] = 0
    try:
        paths = list_outbox_items(directory)
    except OutboxItemError:
        summary["invalid"] = 1
        return summary
    for path in paths:
        try:
            payload = load_outbox_item(path)
            handoff = payload.get("handoff")
            state = handoff.get("state") if isinstance(handoff, dict) else None
            if state not in HANDOFF_STATES:
                summary["invalid"] += 1
                continue
            if state not in {"acknowledged", "terminal_failed"}:
                importer.import_path(path)
            final = load_outbox_item(path)
            final_handoff = final.get("handoff")
            final_state = (
                final_handoff.get("state")
                if isinstance(final_handoff, dict)
                else None
            )
            if final_state in HANDOFF_STATES:
                summary[final_state] += 1
            else:
                summary["invalid"] += 1
        except (OSError, OutboxItemError, ValueError):
            summary["invalid"] += 1
    return summary

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
                database_url = read_secret_file(config.sync_database_url_file)
            except SecretFileUnavailable:
                print("sync_database_unavailable", file=sys.stderr)
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
                review_repository = PsycopgReviewRepository(review_database_url)
                coordinated = import_bundle_with_review(
                    database_url,
                    bundle,
                    review_repository=review_repository,
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
                if kind == "fae":
                    handoff = sync_feedback_closure_outbox(
                        Path(config.feedback_closure_outbox_dir),
                        HandoffImporter(
                            review_repository,
                            YamlRepository(config.registry_path),
                        ),
                    )
                    print(json.dumps(
                        {"source_kind": kind, "closure_handoff": handoff},
                        ensure_ascii=False,
                        sort_keys=True,
                    ))
                    if any(
                        handoff[state]
                        for state in (
                            "prepared",
                            "pending",
                            "blocked",
                            "terminal_failed",
                            "invalid",
                        )
                    ):
                        failed = True
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
