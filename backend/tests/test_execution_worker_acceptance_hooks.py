from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from app.execution_relay.acceptance_hooks import WorkerAcceptanceHooks


DISPATCH_RUN = UUID("11111111-1111-4111-8111-111111111111")
COMPLETION_RUN = UUID("22222222-2222-4222-8222-222222222222")
OTHER_RUN = UUID("33333333-3333-4333-8333-333333333333")


def _control(directory: Path) -> Path:
    directory.mkdir(mode=0o700)
    control = directory / "control.json"
    control.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dispatching_crash_run_id": str(DISPATCH_RUN),
                "completion_crash_run_id": str(COMPLETION_RUN),
            }
        ),
        encoding="utf-8",
    )
    control.chmod(0o600)
    return control


def test_hook_is_default_disabled_and_partial_environment_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PLATFORM_WORKER_ACCEPTANCE_HOOKS", raising=False)
    monkeypatch.delenv("PLATFORM_WORKER_ACCEPTANCE_CONTROL_FILE", raising=False)
    assert WorkerAcceptanceHooks.from_environment() is None

    monkeypatch.setenv("PLATFORM_WORKER_ACCEPTANCE_CONTROL_FILE", "/tmp/control.json")
    with pytest.raises(ValueError, match="acceptance hooks unavailable"):
        WorkerAcceptanceHooks.from_environment()


def test_hook_requires_owner_only_regular_control_inside_owner_only_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "acceptance"
    control = _control(directory)
    monkeypatch.setenv("PLATFORM_WORKER_ACCEPTANCE_HOOKS", "1")
    monkeypatch.setenv("PLATFORM_WORKER_ACCEPTANCE_CONTROL_FILE", str(control))

    control.chmod(0o644)
    with pytest.raises(ValueError, match="acceptance hooks unavailable"):
        WorkerAcceptanceHooks.from_environment()
    control.chmod(0o600)
    directory.chmod(0o755)
    with pytest.raises(ValueError, match="acceptance hooks unavailable"):
        WorkerAcceptanceHooks.from_environment()


def test_dispatch_pause_counts_real_posts_once_and_is_not_rearmed_after_restart(
    tmp_path: Path,
) -> None:
    control = _control(tmp_path / "acceptance")
    hooks = WorkerAcceptanceHooks(control)

    hooks.before_metabot_post(OTHER_RUN)
    hooks.before_metabot_post(DISPATCH_RUN)

    async def exercise() -> None:
        pause = asyncio.create_task(hooks.after_metabot_post(DISPATCH_RUN))
        await asyncio.sleep(0)
        assert not pause.done()
        assert (control.parent / "dispatching-paused").read_text() == str(DISPATCH_RUN)
        state = json.loads((control.parent / "state.json").read_text())
        assert state["metabot_posts"] == {str(DISPATCH_RUN): 1}
        assert state["dispatch_pause_complete"] is True
        assert stat_mode(control.parent / "state.json") == 0o600
        pause.cancel()
        await asyncio.gather(pause, return_exceptions=True)

    asyncio.run(exercise())

    restarted = WorkerAcceptanceHooks(control)
    assert asyncio.run(restarted.after_metabot_post(DISPATCH_RUN)) is None
    state = json.loads((control.parent / "state.json").read_text())
    assert state["metabot_posts"] == {str(DISPATCH_RUN): 1}


def test_completion_pause_is_persistent_and_only_targets_exact_run(
    tmp_path: Path,
) -> None:
    control = _control(tmp_path / "acceptance")
    hooks = WorkerAcceptanceHooks(control)

    assert asyncio.run(hooks.before_terminal_upload(OTHER_RUN)) is None

    async def exercise() -> None:
        pause = asyncio.create_task(hooks.before_terminal_upload(COMPLETION_RUN))
        await asyncio.sleep(0)
        assert not pause.done()
        assert (control.parent / "completion-paused").read_text() == str(COMPLETION_RUN)
        pause.cancel()
        await asyncio.gather(pause, return_exceptions=True)

    asyncio.run(exercise())
    restarted = WorkerAcceptanceHooks(control)
    assert asyncio.run(restarted.before_terminal_upload(COMPLETION_RUN)) is None


def test_control_rejects_extra_keys_equal_run_ids_and_symlink_state(tmp_path: Path) -> None:
    directory = tmp_path / "acceptance"
    control = _control(directory)
    value = json.loads(control.read_text())
    value["extra"] = True
    control.write_text(json.dumps(value))
    control.chmod(0o600)
    with pytest.raises(ValueError, match="acceptance hooks unavailable"):
        WorkerAcceptanceHooks(control)

    value.pop("extra")
    value["completion_crash_run_id"] = value["dispatching_crash_run_id"]
    control.write_text(json.dumps(value))
    control.chmod(0o600)
    with pytest.raises(ValueError, match="acceptance hooks unavailable"):
        WorkerAcceptanceHooks(control)

    value["completion_crash_run_id"] = str(COMPLETION_RUN)
    control.write_text(json.dumps(value))
    control.chmod(0o600)
    outside = tmp_path / "outside"
    outside.write_text("do-not-replace")
    os.symlink(outside, directory / "state.json")
    with pytest.raises(ValueError, match="acceptance hooks unavailable"):
        WorkerAcceptanceHooks(control)
    assert outside.read_text() == "do-not-replace"


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
