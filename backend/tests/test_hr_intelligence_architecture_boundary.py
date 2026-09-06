from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
APP_HR = ROOT / "backend/app/hr"
CLOUD = ROOT / "deploy/cloud"
LOCAL_FACTORY = ROOT / "backend/tools/hr_intelligence"


def test_production_image_cannot_collect_or_analyze_panorama() -> None:
    dockerfile = (CLOUD / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY --chown=10001:10001 backend/tools" not in dockerfile
    for name in (
        "panorama_cli.py",
        "panorama_producer.py",
        "panorama_collection.py",
        "panorama_analysis.py",
        "panorama_runtime.py",
    ):
        assert not (APP_HR / name).exists()


def test_production_has_no_run_or_resume_surface() -> None:
    assert not (CLOUD / "compose.hr-intelligence.yaml").exists()
    assert not (CLOUD / "hr-panorama-producer.sh").exists()
    deployed_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CLOUD / "compose.yaml", CLOUD / "remote-stage.sh")
    )
    assert "platform-hr-intelligence" not in deployed_text
    assert "panorama_cli" not in deployed_text


def test_import_service_has_no_model_secret_or_external_network() -> None:
    service = yaml.safe_load(
        (CLOUD / "compose.hr-intelligence-import.yaml").read_text(encoding="utf-8")
    )["services"]["platform-hr-intelligence-import"]

    assert service["networks"] == ["platform-internal"]
    for forbidden in (
        "PLATFORM_BRAIN_PROVIDER",
        "brain-provider-api-key",
        "platform-edge",
        "source_catalog",
    ):
        assert forbidden not in repr(service)


def test_local_factory_is_outside_the_production_package() -> None:
    assert (LOCAL_FACTORY / "cli.py").is_file()
    assert (LOCAL_FACTORY / "source_catalog.v1.json").is_file()
