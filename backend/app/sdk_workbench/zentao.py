"""Bounded ZenTao API v2 client for the private SDK workspace."""

from __future__ import annotations

import json
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse

import httpx


_MAX_BYTES = 1_048_576
_DOCKER_PRIVATE_NETWORKS = tuple(ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class ZenTaoUnavailable(RuntimeError):
    pass


class ZenTaoProtocolError(RuntimeError):
    pass


class ZenTaoRejected(RuntimeError):
    pass


class ZenTaoAuthenticationError(ZenTaoRejected):
    pass


class ZenTaoRequestRejected(ZenTaoRejected):
    pass


def _private_origin(value: str) -> str:
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        address = None
        if host and host != "zentao":
            try:
                address = ip_address(host)
            except ValueError:
                pass
        allowed_host = host == "zentao" or (address is not None and (
            address.is_loopback or any(address in network for network in _DOCKER_PRIVATE_NETWORKS)
        ))
        port = parsed.port
    except ValueError:
        allowed_host = False
        port = None
    if (
        parsed.scheme != "http"
        or not allowed_host
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("zentao_private_origin_required")
    return value.rstrip("/")


def _reject_nonstandard(_value: str):
    raise ValueError("nonstandard JSON")


class ZenTaoClient:
    def __init__(
        self,
        base_url: str,
        account: str,
        password_file: str | Path,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = _private_origin(base_url)
        if not account or not account.isascii() or len(account) > 80:
            raise ValueError("zentao_account_invalid")
        self._account = account
        try:
            password = Path(password_file).read_text(encoding="utf-8").strip()
        except OSError:
            raise ValueError("zentao_password_file_unavailable") from None
        if not password:
            raise ValueError("zentao_password_empty")
        self._password = password
        self._token: str | None = None
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            timeout=10.0,
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
        authenticated: bool = True,
        retry_auth: bool = True,
    ) -> dict:
        if authenticated and self._token is None:
            await self.login()
        headers = {"Token": self._token} if authenticated and self._token else {}
        try:
            async with self._client.stream(
                method,
                f"/api.php/v2/{endpoint}",
                json=payload,
                params=params,
                headers=headers,
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_BYTES:
                        raise ZenTaoProtocolError("zentao_response_too_large")
                status = response.status_code
        except ZenTaoProtocolError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise ZenTaoUnavailable("zentao_unavailable") from None
        if status >= 500:
            raise ZenTaoUnavailable("zentao_unavailable")
        if status == 401:
            self._token = None
            if authenticated and retry_auth:
                await self.login()
                return await self._request(method, endpoint, payload=payload, params=params, authenticated=True, retry_auth=False)
            raise ZenTaoAuthenticationError("zentao_authentication_failed")
        if status != 200:
            if status in {400, 403, 404, 409, 422}:
                raise ZenTaoRequestRejected("zentao_request_rejected")
            raise ZenTaoProtocolError("zentao_http_status")
        try:
            body = json.loads(content, parse_constant=_reject_nonstandard)
        except (UnicodeDecodeError, ValueError):
            raise ZenTaoProtocolError("zentao_invalid_json") from None
        if not isinstance(body, dict) or not isinstance(body.get("status"), str):
            raise ZenTaoProtocolError("zentao_invalid_envelope")
        if body["status"] != "success":
            raise ZenTaoRejected("zentao_rejected")
        return body

    async def login(self) -> None:
        body = await self._request(
            "POST", "users/login",
            payload={"account": self._account, "password": self._password},
            authenticated=False,
        )
        token = body.get("token")
        if not isinstance(token, str) or not token or len(token) > 512:
            raise ZenTaoProtocolError("zentao_invalid_token")
        self._token = token

    async def list_stories(self, product_id: int, page: int = 1) -> dict:
        if product_id < 1 or page < 1:
            raise ValueError("zentao_invalid_query")
        body = await self._request(
            "GET", f"products/{product_id}/stories",
            params={"browseType": "allstory", "recPerPage": 50, "pageID": page},
        )
        if not isinstance(body.get("stories"), list):
            raise ZenTaoProtocolError("zentao_invalid_stories")
        return body

    async def get_story(self, story_id: int) -> dict:
        if story_id < 1:
            raise ValueError("zentao_invalid_story_id")
        body = await self._request("GET", f"stories/{story_id}")
        story = body.get("story")
        if not isinstance(story, dict) or str(story.get("id")) != str(story_id):
            raise ZenTaoProtocolError("zentao_invalid_story")
        return story

    async def get_task(self, task_id: int) -> dict:
        if task_id < 1:
            raise ValueError("zentao_invalid_task_id")
        body = await self._request("GET", f"tasks/{task_id}")
        task = body.get("task")
        if not isinstance(task, dict) or str(task.get("id")) != str(task_id):
            raise ZenTaoProtocolError("zentao_invalid_task")
        return task

    async def create_story(self, product_id: int, title: str, spec: str, verify: str) -> int:
        body = await self._request(
            "POST", "stories",
            # The workbench owns requirement review; ZenTao must keep this story active.
            payload={"productID": product_id, "title": title, "spec": spec, "verify": verify, "needNotReview": 1},
        )
        return self._positive_id(body)

    async def assign_story(self, story_id: int, account: str, *, expected_version: int) -> None:
        """Edit only the native assignee after a fresh conflict check and readback."""
        if story_id < 1 or not account or not account.isascii() or len(account) > 40:
            raise ValueError("zentao_assignment_invalid")
        before = await self.get_story(story_id)
        if (before.get("assignedTo") not in {"", None, account}
                or int(before.get("version", 0)) != expected_version
                or before.get("status") not in {"active", "reviewing"}):
            raise ZenTaoRequestRejected("zentao_assignment_conflict")
        if before.get("assignedTo") == account:
            return
        await self._request("PUT", f"stories/{story_id}",
                            payload={"title": before["title"], "assignedTo": account})
        after = await self.get_story(story_id)
        if after.get("assignedTo") != account:
            raise ZenTaoProtocolError("zentao_assignment_unconfirmed")
        for field in ("title", "spec", "verify", "pri", "status", "version", "reviewer"):
            if after.get(field) != before.get(field):
                raise ZenTaoProtocolError("zentao_assignment_side_effect")

    async def create_task(self, execution_id: int, story_id: int, name: str, desc: str) -> int:
        body = await self._request(
            "POST", "tasks",
            payload={"executionID": execution_id, "story": story_id, "name": name, "desc": desc},
        )
        return self._positive_id(body)

    @staticmethod
    def _positive_id(body: dict) -> int:
        value = body.get("id")
        if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
            raise ZenTaoProtocolError("zentao_invalid_id")
        return int(value)
