from __future__ import annotations

from app.attachments import worker_runtime


def test_main_dispatches_all_runtime(monkeypatch) -> None:
    calls = []

    async def fake_run_all():
        calls.append("all")

    monkeypatch.setattr(worker_runtime, "run_all", fake_run_all)

    assert worker_runtime.main(["all"]) == 0
    assert calls == ["all"]


def test_main_dispatches_healthcheck_without_starting_worker(monkeypatch) -> None:
    monkeypatch.setattr(worker_runtime, "healthcheck", lambda: 0)

    assert worker_runtime.main(["healthcheck"]) == 0


def test_main_fails_closed_for_unknown_mode() -> None:
    assert worker_runtime.main(["processing"]) == 1
