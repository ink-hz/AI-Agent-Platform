import asyncio

import pytest

from app.main import cancel_tasks


@pytest.mark.asyncio
async def test_cancel_tasks_waits_for_task_cleanup():
    cleaned = asyncio.Event()

    async def worker():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)

    await cancel_tasks([task])

    assert task.cancelled()
    assert cleaned.is_set()
