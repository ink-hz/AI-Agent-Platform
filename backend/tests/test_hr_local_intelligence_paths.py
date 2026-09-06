import importlib.util
from pathlib import Path

import pytest


def test_default_local_root_is_absolute_and_outside_repository(monkeypatch) -> None:
    assert importlib.util.find_spec("tools.hr_intelligence.paths") is not None
    from tools.hr_intelligence.paths import DEFAULT_LOCAL_ROOT, local_data_root

    monkeypatch.delenv("HR_INTELLIGENCE_LOCAL_ROOT", raising=False)

    selected = local_data_root()

    assert selected == DEFAULT_LOCAL_ROOT
    assert selected.is_absolute()
    assert Path(__file__).parents[2].resolve() not in selected.parents


def test_relative_or_repository_local_data_root_is_rejected(monkeypatch) -> None:
    from tools.hr_intelligence.paths import local_data_root

    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", "relative/path")
    with pytest.raises(ValueError, match="absolute"):
        local_data_root()

    repository_root = Path(__file__).parents[2].resolve()
    monkeypatch.setenv(
        "HR_INTELLIGENCE_LOCAL_ROOT",
        str(repository_root / "tmp/hr-intelligence"),
    )
    with pytest.raises(ValueError, match="outside repository"):
        local_data_root()
