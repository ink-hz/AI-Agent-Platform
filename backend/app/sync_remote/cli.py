from __future__ import annotations

import argparse
import subprocess
import sys

from app.config import load_config

from .config import default_sources
from .export import ExportError, export_source
from .importer import import_bundle


def _keychain_value(account: str, service: str) -> str:
    result = subprocess.run(
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
    )
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
    if not args.export_only:
        try:
            database_url = _keychain_value(
                config.sync_keychain_account,
                config.sync_keychain_service,
            )
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 1

    failed = False
    for kind in selected:
        try:
            bundle = export_source(sources[kind])
            if args.export_only:
                print(f"{kind}: exported {bundle.source_counts}")
                continue
            assert database_url is not None
            result = import_bundle(database_url, bundle)
            print(f"{kind}: {result.status} {result.applied_counts}")
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
