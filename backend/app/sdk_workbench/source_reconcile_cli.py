"""Explicit-file CLI for the R0-a offline proposal report."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .source_reconcile import ReconcileError, reconcile


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages can contain input paths and arbitrary argument values.
        raise ReconcileError("invalid_arguments", "arguments")


def _error(code: str, path: str) -> int:
    print(json.dumps(dict(error=code, path=path)), file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(
        description="Offline source alias proposals; never imports business objects",
        allow_abbrev=False,
    )
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--existing-export", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except ReconcileError as error:
        return _error(error.code, error.path)
    except SystemExit as result:
        # Only the parameter boundary handles argparse's normal --help exit.
        return result.code

    inputs = [args.candidates, args.csv, args.workbook]
    if args.existing_export is not None:
        inputs.append(args.existing_export)
    output = args.output
    try:
        # lexists also refuses dangling/looping symlinks. Do not resolve a
        # dangling output symlink into a different, previously absent file.
        if os.path.lexists(output):
            if output.exists() and any(output.samefile(source) for source in inputs):
                return _error("output_is_input", "output")
            return _error("output_exists", "output")
        payloads = [path.read_bytes() for path in inputs]
    except (OSError, ValueError):
        # ValueError is confined to file operations (e.g. a NUL in a path).
        return _error("file_io", "explicit_files")

    try:
        report = reconcile(payloads[0], payloads[1], payloads[2],
                           payloads[3] if len(payloads) == 4 else None)
    except ReconcileError as error:
        return _error(error.code, error.path)
    serialized = json.dumps(report, ensure_ascii=False, allow_nan=False,
                            sort_keys=True, indent=2) + "\n"
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
    except FileExistsError:
        return _error("output_exists", "output")
    except (OSError, ValueError):
        return _error("file_io", "explicit_files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
