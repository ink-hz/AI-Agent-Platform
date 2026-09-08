import pytest
from app.config import load_config


def test_partial_knowledge_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("PLATFORM_HR_KNOWLEDGE_ROOT", "/test/releases")
    monkeypatch.delenv("PLATFORM_HR_KNOWLEDGE_AGENT_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_HR_KNOWLEDGE_COMMIT", raising=False)
    with pytest.raises(RuntimeError, match="knowledge"):
        load_config()


def test_knowledge_configuration_is_loaded_without_enabling_it_by_default(monkeypatch):
    for key in [
        "PLATFORM_HR_KNOWLEDGE_ROOT",
        "PLATFORM_HR_KNOWLEDGE_AGENT_ROOT",
        "PLATFORM_HR_KNOWLEDGE_COMMIT",
    ]:
        monkeypatch.delenv(key, raising=False)
    config = load_config()
    assert config.hr_knowledge_root == ""
