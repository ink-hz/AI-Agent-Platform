"""Public release construction; real local asset test is explicit and read-only."""

import json
import os
from pathlib import Path

import pytest

from app.hr.intelligence_bundle import BundleVerificationError, verify_import_bundle
from app.hr_agent.knowledge import KnowledgeReleases
from tests.test_hr_intelligence_import import _bundle

SOURCE = Path(__file__).parents[1] / "hr_agent_knowledge"


def check_build(bundle_path, output):
    from tools.hr_agent.build_intelligence_release import build

    verified = verify_import_bundle(bundle_path)
    manifest = build(SOURCE, bundle_path, output)
    assert build(SOURCE, bundle_path, output) == manifest
    published = KnowledgeReleases(output).current()
    items = [item for item in published.items if item["ref"]["kind"] == "intelligence"]
    names = {name for name in verified.agent_document_index if name.endswith(".md")}
    assert {item["path"].removeprefix("intelligence/") for item in items} == names
    for item in items:
        name = item["path"].removeprefix("intelligence/")
        assert published.read(item["ref"]).encode() == (bundle_path / name).read_bytes()
        assert item["ref"]["revision"] == str(verified.bundle_id)
        assert verified.generated_at.isoformat() in item["description"]
    assert len([i for i in published.items if i["ref"]["kind"] == "method"]) == 9
    provenance = json.loads(
        (published.directory / "intelligence-provenance.json").read_text()
    )
    assert provenance["manifest_sha256"] == verified.manifest_sha256
    assert (published.directory / "intelligence-source-manifest.json").read_bytes() == (
        bundle_path / "manifest.json"
    ).read_bytes()
    return published, items


def test_build_preserves_verified_markdown_and_failure_keeps_pointer(tmp_path):
    from tools.hr_agent.build_intelligence_release import build

    _, bundle_path = _bundle(tmp_path)
    output = tmp_path / "publication"
    published, items = check_build(bundle_path, output)
    pointer = (output / "current.json").read_bytes()
    (published.directory / items[0]["path"]).write_text("changed")
    with pytest.raises(ValueError, match="immutable"):
        build(SOURCE, bundle_path, output)
    assert (output / "current.json").read_bytes() == pointer
    (bundle_path / "agent/index.md").write_text("tampered")
    with pytest.raises(BundleVerificationError, match="checksum"):
        build(SOURCE, bundle_path, output)
    assert (output / "current.json").read_bytes() == pointer


def test_explicit_real_local_bundle_is_preserved(tmp_path):
    configured = os.environ.get("HR_TEST_INTELLIGENCE_BUNDLE")
    if not configured:
        pytest.skip(
            "explicit public local Bundle required; fixture is not actual asset import"
        )
    check_build(Path(configured), tmp_path / "publication")
