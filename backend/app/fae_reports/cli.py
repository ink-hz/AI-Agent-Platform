from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from app.local_secrets import read_secret_file

from .importer import FaeReportImporter
from .repository import PsycopgFaeReportRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fae-report")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("import")
    command.add_argument("--path", required=True)
    command.add_argument("--actor", required=True)
    args = parser.parse_args(argv)
    database_url = read_secret_file(os.environ["FAE_REPORT_IMPORT_DATABASE_URL_FILE"])
    result = FaeReportImporter(PsycopgFaeReportRepository(database_url)).import_path(
        Path(args.path), actor=args.actor
    )
    print(json.dumps(asdict(result), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
