from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SELF_CHECK = REPO_ROOT / "docs/superpowers/specs/hr-cloud-loop/selfcheck.py"
ASSET_DIR = REPO_ROOT / "docs/superpowers/specs/hr-cloud-loop"
SPEC = REPO_ROOT / "docs/superpowers/specs/2026-09-09-hr-cloud-loop-runtime-spec.md"


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(REPO_ROOT / "backend/.venv/bin/python"), str(root / "docs/superpowers/specs/hr-cloud-loop/selfcheck.py"), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def copy_contract_docs(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    target_assets = target / "docs/superpowers/specs/hr-cloud-loop"
    target_assets.parent.mkdir(parents=True)
    shutil.copytree(ASSET_DIR, target_assets)
    shutil.copy2(SPEC, target / "docs/superpowers/specs/2026-09-09-hr-cloud-loop-runtime-spec.md")
    return target


def test_selfcheck_passes_repository_contracts() -> None:
    result = run_check(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "A0 docs self-check passed" in result.stdout


def test_selfcheck_detects_invalid_datetime(tmp_path: Path) -> None:
    root = copy_contract_docs(tmp_path)
    examples_path = root / "docs/superpowers/specs/hr-cloud-loop/contract-examples.json"
    examples = json.loads(examples_path.read_text())
    log = next(item for item in examples if item["name"] == "ordinary_log_whitelist")
    log["value"]["at"] = "2026-99-99T25:61:61Z"
    examples_path.write_text(json.dumps(examples))

    result = run_check(root)
    assert result.returncode != 0
    assert "ordinary_log_whitelist" in result.stdout
    assert "date-time" in result.stdout


def test_selfcheck_detects_missing_direct_definition_example(tmp_path: Path) -> None:
    root = copy_contract_docs(tmp_path)
    examples_path = root / "docs/superpowers/specs/hr-cloud-loop/contract-examples.json"
    examples = json.loads(examples_path.read_text())
    examples = [item for item in examples if item["definition"] != "ExactRef"]
    examples_path.write_text(json.dumps(examples))

    result = run_check(root)
    assert result.returncode != 0
    assert "direct positive example missing for $defs/ExactRef" in result.stdout


def test_selfcheck_detects_condition_matrix_mutation(tmp_path: Path) -> None:
    root = copy_contract_docs(tmp_path)
    examples_path = root / "docs/superpowers/specs/hr-cloud-loop/contract-examples.json"
    examples = json.loads(examples_path.read_text())
    examples = [item for item in examples if item["name"] != "Change_add_bad_target"]
    examples_path.write_text(json.dumps(examples))

    result = run_check(root)
    assert result.returncode != 0
    assert "COND-CHANGE-ADD" in result.stdout


def test_selfcheck_detects_budget_arithmetic_mutation(tmp_path: Path) -> None:
    root = copy_contract_docs(tmp_path)
    manifest_path = root / "docs/superpowers/specs/hr-cloud-loop/selfcheck-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["budget_assertions"][0]["expected_remaining"] += 1
    manifest_path.write_text(json.dumps(manifest))

    result = run_check(root)
    assert result.returncode != 0
    assert "BUDGET-" in result.stdout
