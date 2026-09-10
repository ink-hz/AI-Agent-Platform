#!/usr/bin/env python3
"""Durable, offline A0 contract-document self-check."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ASSET_REL = Path("docs/superpowers/specs/hr-cloud-loop")
SPEC_REL = Path("docs/superpowers/specs/2026-09-09-hr-cloud-loop-runtime-spec.md")


def rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return True


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    root = args.root.resolve()
    assets = root / ASSET_REL
    errors: list[str] = []
    try:
        schema = json.loads((assets / "contracts.schema.json").read_text(encoding="utf-8"))
        examples = json.loads((assets / "contract-examples.json").read_text(encoding="utf-8"))
        manifest = json.loads((assets / "selfcheck-manifest.json").read_text(encoding="utf-8"))
        spec = (root / SPEC_REL).read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR load: {exc}")
        return 1

    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        fail(errors, f"schema invalid: {exc}")

    checker = FormatChecker()
    checker.checks("date-time", raises=())(rfc3339_datetime)
    validators: dict[str, Draft202012Validator] = {}
    base_validator = Draft202012Validator(schema, format_checker=checker)
    by_name: dict[str, dict[str, Any]] = {}
    positive_defs: set[str] = set()
    for item in examples:
        name = item.get("name", "<unnamed>")
        definition = item.get("definition")
        by_name[name] = item
        if definition not in schema.get("$defs", {}):
            fail(errors, f"{name}: unknown definition {definition!r}")
            continue
        validator = validators.setdefault(
            definition, base_validator.evolve(schema={"$ref": f"#/$defs/{definition}"})
        )
        found = list(validator.iter_errors(item.get("value")))
        actual_valid = not found
        expected_valid = item.get("valid") is True
        if actual_valid != expected_valid:
            detail = "valid unexpectedly" if actual_valid else "; ".join(error.message for error in found[:3])
            fail(errors, f"{name} [{definition}] expected valid={expected_valid}: {detail}")
        if expected_valid:
            positive_defs.add(definition)

    for definition in sorted(set(schema.get("$defs", {})) - positive_defs):
        fail(errors, f"direct positive example missing for $defs/{definition}")

    for rule in manifest.get("condition_coverage", []):
        for polarity in ("positive", "negative"):
            expected = polarity == "positive"
            for name in rule.get(polarity, []):
                item = by_name.get(name)
                if item is None or item.get("definition") != rule.get("definition") or item.get("valid") is not expected:
                    fail(errors, f"{rule['id']}: missing declared {polarity} example {name}")

    for entry in manifest.get("prose_coverage", []):
        if entry["contains"] not in spec:
            fail(errors, f"{entry['id']}: required prose evidence missing: {entry['contains']!r}")

    planned = set(manifest.get("planned_path_exclusions", []))
    for rel in manifest.get("existing_paths", []):
        if rel in planned or not (root / rel).exists():
            fail(errors, f"PATH-{rel}: relevant existing path missing or wrongly excluded")
    if root.joinpath(".git").exists() or (root / ".git").is_file():
        for revision in manifest.get("commit_refs", []):
            result = subprocess.run(
                ["git", "cat-file", "-e", f"{revision}^{{commit}}"], cwd=root, capture_output=True, text=True
            )
            if result.returncode:
                fail(errors, f"COMMIT-{revision}: referenced commit does not exist")

    schema_states = set(schema["$defs"]["WorkView"]["properties"]["state"]["enum"])
    manifest_states = set(manifest.get("workflow_states", []))
    if schema_states != manifest_states:
        fail(errors, f"STATE-SCHEMA: schema={sorted(schema_states)} manifest={sorted(manifest_states)}")
    state_table = spec[spec.find("## 7."):spec.find("## 8.")]
    for state in sorted(manifest_states):
        if state not in state_table:
            fail(errors, f"STATE-WORKFLOW-{state}: absent from workflow state section")

    partition = manifest.get("capability_partition", {})
    buckets = [set(partition.get(key, [])) for key in ("model_tools", "user_http_only", "deferred")]
    if len(buckets[0]) != 5:
        fail(errors, "CAP-MODEL-TOOLS: exactly five model tools required")
    if any(left & right for index, left in enumerate(buckets) for right in buckets[index + 1 :]):
        fail(errors, "CAP-DISJOINT: capability buckets overlap")
    for capability in set().union(*buckets):
        if capability not in spec:
            fail(errors, f"CAP-{capability}: capability absent from spec")

    for assertion in manifest.get("budget_assertions", []):
        total = assertion["limit"] + assertion.get("addition", 0)
        actual = total - assertion["used"] - assertion["reserved"]
        if actual != assertion["expected_remaining"]:
            fail(errors, f"{assertion['id']}: expected {assertion['expected_remaining']}, calculated {actual}")

    if errors:
        for error in errors:
            print(f"ERROR {error}")
        print(f"A0 docs self-check failed: {len(errors)} error(s)")
        return 1
    print(
        "A0 docs self-check passed: "
        f"{len(schema['$defs'])} definitions, {len(examples)} examples, "
        f"{len(manifest['condition_coverage'])} condition rules, "
        f"{len(manifest['prose_coverage'])} prose evidence IDs"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
