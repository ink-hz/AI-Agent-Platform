from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import tomllib
import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "http_task_v1"
PROJECT = CONTRACT / "pyproject.toml"
FIXTURE = CONTRACT / "fixtures" / "action_digest.json"
HASH_SCRIPT = ROOT / "scripts" / "hash_http_task_contract.py"
RELEASE = ROOT / "deploy" / "cloud" / "http-task-contract.release.json"
SCHEMA = CONTRACT / "schema" / "http-task-contract-v1.schema.json"
VALID_EXAMPLES = CONTRACT / "fixtures" / "valid_examples.json"
ERROR_EXAMPLES = CONTRACT / "fixtures" / "error_examples.json"


def _contract_models():
    sys.path.insert(0, str(CONTRACT))
    try:
        from orbbec_task_contract import models
    finally:
        sys.path.pop(0)
    return models


def _hash_module():
    spec = importlib.util.spec_from_file_location(
        "hash_http_task_contract", HASH_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_runner_requires_python_311() -> None:
    project = tomllib.loads(PROJECT.read_text("utf-8"))
    assert project["project"]["requires-python"] == ">=3.11"


def test_contract_runner_pins_jcs() -> None:
    project = tomllib.loads(PROJECT.read_text("utf-8"))
    assert "jcs==0.2.1" in project["project"]["dependencies"]
    assert (
        "jcs==0.2.1"
        in (ROOT / "backend" / "requirements.txt").read_text("utf-8").splitlines()
    )


def test_contract_wheel_includes_only_the_runtime_package() -> None:
    project = tomllib.loads(PROJECT.read_text("utf-8"))
    assert project["tool"]["setuptools"]["packages"] == ["orbbec_task_contract"]


def test_action_digest_fixture_is_stable() -> None:
    models = _contract_models()
    fixture = json.loads(FIXTURE.read_text("utf-8"))
    assert models.action_digest(fixture["input"]) == fixture["lowercase_hex"]


def test_action_digest_fixture_matches_frozen_canonical_utf8_bytes() -> None:
    models = _contract_models()
    fixture = json.loads(FIXTURE.read_text("utf-8"))
    canonical = models.canonical_action_bytes(fixture["input"])
    expected = (
        '{"action_kind":"voc.submit","action_seq":1,"parameters":'
        '{"priority":2,"title":"机器人客户反馈"},"platform_task_id":'
        '"0d8f0764-91be-4af5-b4d8-e79d58ab3b07"}'
    ).encode()
    assert canonical == expected
    assert sha256(expected).hexdigest() == fixture["lowercase_hex"]


def test_contract_runner_is_importable_and_exposes_main() -> None:
    sys.path.insert(0, str(CONTRACT))
    try:
        from orbbec_task_contract import runner
    finally:
        sys.path.pop(0)
    assert callable(runner.main)


def test_contract_commits_strict_schema_and_example_assets() -> None:
    assert SCHEMA.is_file()
    assert VALID_EXAMPLES.is_file()
    assert ERROR_EXAMPLES.is_file()
    schema = json.loads(SCHEMA.read_text("utf-8"))
    required = {
        "CapabilitiesResponse",
        "HealthResponse",
        "TaskResponse",
        "CreateTaskRequest",
        "CreateTaskReceipt",
        "MessageRequest",
        "MessageReceipt",
        "CancelRequest",
        "CancelReceipt",
        "ActionExecuteRequest",
        "ActionExecuteReceipt",
        "TaskEvent",
        "EventPage",
        "ErrorEnvelope",
        "TokenBrokerRequest",
        "TokenBrokerResponse",
    }
    assert required.issubset(schema["$defs"])


def test_contract_directory_hash_is_path_sensitive_and_ignores_generated_files(
    tmp_path: Path,
) -> None:
    hash_module = _hash_module()
    source = tmp_path / "contract"
    source.mkdir()
    (source / "b.txt").write_bytes(b"second")
    (source / "a.txt").write_bytes(b"first")
    generated = source / "__pycache__"
    generated.mkdir()
    (generated / "ignored.pyc").write_bytes(b"nondeterministic")
    package_metadata = source / "orbbec_http_task_contract.egg-info"
    package_metadata.mkdir()
    (package_metadata / "SOURCES.txt").write_bytes(b"generated metadata")
    build_copy = source / "build" / "lib"
    build_copy.mkdir(parents=True)
    (build_copy / "copied.py").write_bytes(b"generated build output")

    digest = hash_module.directory_sha256(source)

    expected = sha256(b"a.txt\0first\0b.txt\0second\0").hexdigest()
    assert digest == expected


def test_contract_hash_includes_nested_source_build_directories(tmp_path: Path) -> None:
    hash_module = _hash_module()
    source = tmp_path / "contract"
    nested = source / "package" / "build"
    nested.mkdir(parents=True)
    (nested / "source.py").write_bytes(b"tracked source")
    root_build = source / "build"
    root_build.mkdir()
    (root_build / "generated.py").write_bytes(b"ignored")

    digest = hash_module.directory_sha256(source)

    expected = sha256(b"package/build/source.py\0tracked source\0").hexdigest()
    assert digest == expected


def test_manifest_hashes_a_commit_and_rejects_a_dirty_contract_tree(
    tmp_path: Path,
) -> None:
    hash_module = _hash_module()
    repository = tmp_path / "repository"
    contract = repository / "contracts" / "http_task_v1"
    contract.mkdir(parents=True)
    (contract / "contract.json").write_text('{"version":1}\n', "utf-8")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "contract@example"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Contract Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "contract"], check=True
    )
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    archive_digest = hash_module.archive_sha256(repository, commit)
    assert archive_digest == hash_module.directory_sha256(contract)

    (contract / "contract.json").write_text('{"version":2}\n', "utf-8")
    with pytest.raises(ValueError, match="working contract tree differs"):
        hash_module.write_manifest(
            repository=repository,
            source_commit=commit,
            output=repository / "release.json",
        )


def test_release_manifest_matches_contract_when_present() -> None:
    assert RELEASE.is_file()
    hash_module = _hash_module()
    manifest = json.loads(RELEASE.read_text("utf-8"))
    assert set(manifest) == {
        "contract_version",
        "source_commit",
        "sha256",
        "requires_python",
    }
    assert manifest["contract_version"] == "orbbec-http-task/v1"
    assert manifest["requires_python"] == ">=3.11"
    assert manifest["sha256"] == hash_module.archive_sha256(
        ROOT, manifest["source_commit"]
    )
    assert manifest["sha256"] == hash_module.directory_sha256(CONTRACT)
