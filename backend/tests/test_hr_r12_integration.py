from __future__ import annotations

import ast
import inspect
import threading

from fastapi.testclient import TestClient

from app.main import create_app


def test_create_app_declares_all_hr_r12_service_boundaries() -> None:
    parameters = set(inspect.signature(create_app).parameters)

    assert {
        "hr_position_intelligence_service",
        "hr_candidate_service",
        "hr_resource_service",
        "hr_task_context_provider",
        "hr_position_task_service",
        "hr_position_package_projector",
    }.issubset(parameters)


def test_create_app_wires_candidate_documents_to_the_existing_download_service() -> None:
    tree = ast.parse(inspect.getsource(create_app))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CandidateService"
    ]

    assert len(calls) == 1
    assert {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in calls[0].keywords
    }["document_tickets"] == "conversation_attachment_download_service"


def test_create_app_wires_one_position_package_projection_loop() -> None:
    source = inspect.getsource(create_app)
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "position_package_projection_loop"
    ]

    assert len(calls) == 1
    assert ast.unparse(calls[0].args[0]) == "hr_position_package_projector"
    assert 'and "direct_agent" in v1_mission_modes' in source
    assert "and hr_position_service is not None" in source


def test_create_app_runs_position_package_projection_in_one_worker_thread() -> None:
    caller_thread = threading.get_ident()
    called = threading.Event()
    worker_threads: list[int] = []

    class _Projector:
        def reconcile_one(self) -> bool:
            worker_threads.append(threading.get_ident())
            called.set()
            return False

    app = create_app(
        start_poller=False,
        hr_position_package_projector=_Projector(),
    )

    with TestClient(app):
        assert called.wait(timeout=2)

    assert len(set(worker_threads)) == 1
    assert worker_threads[0] != caller_thread


def test_automatic_position_package_projector_requires_every_runtime_gate(
    monkeypatch,
) -> None:
    from app import main as app_main

    repository_calls = []
    projector_calls = []

    class _Repository:
        def __init__(self, database_url):
            repository_calls.append(database_url)

    class _Projector:
        def __init__(self, repository, positions, codec, **kwargs):
            projector_calls.append((repository, positions, codec, kwargs))

    monkeypatch.setattr(app_main, "PositionPackageProjectionRepository", _Repository)
    monkeypatch.setattr(app_main, "PositionPackageProjector", _Projector)
    enabled = {
        "identity_enabled": True,
        "direct_agent_enabled": True,
        "database_url": "postgresql://app@db/control",
        "positions": object(),
        "content_codec": object(),
        "model_version": "hr-runtime-v1",
    }
    for missing in enabled:
        arguments = dict(enabled)
        arguments[missing] = False if missing.endswith("enabled") else None
        assert app_main._build_position_package_projector(**arguments) is None
    assert repository_calls == []
    assert projector_calls == []

    selected = app_main._build_position_package_projector(**enabled)

    assert isinstance(selected, _Projector)
    assert repository_calls == [enabled["database_url"]]
    assert projector_calls[0][1:3] == (enabled["positions"], enabled["content_codec"])
    assert projector_calls[0][3]["model_version"] == enabled["model_version"]
