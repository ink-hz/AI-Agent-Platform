"""Opt-in contract check against an isolated ZenTao test product."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.sdk_workbench.zentao import ZenTaoClient


@pytest.mark.live_zentao
@pytest.mark.asyncio
async def test_real_zentao_story_and_task_contract() -> None:
    names = (
        "SDK_LIVE_ZENTAO_URL",
        "SDK_LIVE_ZENTAO_ACCOUNT",
        "SDK_LIVE_ZENTAO_PASSWORD_FILE",
        "SDK_LIVE_PRODUCT_ID",
        "SDK_LIVE_EXECUTION_ID",
    )
    if os.getenv("SDK_LIVE_ACCEPT") != "1":
        pytest.skip("isolated ZenTao trial requires explicit SDK_LIVE_ACCEPT=1")
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.fail(f"live ZenTao settings missing: {', '.join(missing)}")

    base_url = os.environ["SDK_LIVE_ZENTAO_URL"]
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        pytest.fail("live trial must use a loopback URL or SSH tunnel")
    password_file = Path(os.environ["SDK_LIVE_ZENTAO_PASSWORD_FILE"])
    if password_file.is_symlink() or not password_file.is_file() or password_file.stat().st_mode & 0o077:
        pytest.fail("live ZenTao password file must be a private regular file")

    product_id = int(os.environ["SDK_LIVE_PRODUCT_ID"])
    execution_id = int(os.environ["SDK_LIVE_EXECUTION_ID"])
    marker = uuid4().hex[:12]
    title = f"SDK-V1-ACCEPT-{marker} 需求与任务 API 契约"
    spec = f"场景：隔离实例 API 契约试验 {marker}\n设备/平台：无真实设备"
    verify = "需求和任务可通过相同 ID 读回；本记录不代表 SDK 测试通过。"
    task_name = f"SDK-V1-ACCEPT-{marker} 核对需求关联"
    task_desc = "仅验证禅道 API 字段；不产生构建或硬件测试结论。"

    client = ZenTaoClient(base_url, os.environ["SDK_LIVE_ZENTAO_ACCOUNT"], password_file)
    try:
        story_id = await client.create_story(product_id, title, spec, verify)
        story = await client.get_story(story_id)
        assert str(story["product"]) == str(product_id)
        assert story["title"] == title
        assert story["spec"] == spec
        assert story["verify"] == verify
        assert int(story["version"]) >= 1

        listed = await client.list_stories(product_id, 1)
        assert isinstance(listed["stories"], list)
        assert any(str(item.get("id")) == str(story_id) for item in listed["stories"])
        task_id = await client.create_task(execution_id, story_id, task_name, task_desc)
        task = await client.get_task(task_id)
        assert str(task["story"]) == str(story_id)
        assert str(task["execution"]) == str(execution_id)
        assert task["name"] == task_name
        assert task["desc"] == task_desc
    finally:
        await client.close()
