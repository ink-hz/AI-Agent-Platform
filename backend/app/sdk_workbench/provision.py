"""Private ZenTao 22.6 user creation with explicit identity and access verification."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

import httpx

from .zentao import _private_origin


_ACCOUNT = re.compile(r"sdk_[a-z2-7]{26}\Z")
_MARKER = re.compile(r"sdk:[0-9a-f]{32}\Z")
_MAX_RESPONSE = 1_048_576


class ProvisionUnavailable(RuntimeError):
    pass


class ProvisionConflict(RuntimeError):
    pass


class ProvisionRejected(RuntimeError):
    pass


class ZenTaoProvisioner:
    def __init__(
        self, base_url: str, account: str, password_file: str | Path, *,
        product_id: int, group_id: int, transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = _private_origin(base_url)
        if not account or not re.fullmatch(r"[a-z][a-z0-9._-]{1,29}", account):
            raise ValueError("sdk_provision_account_invalid")
        if product_id < 1 or group_id < 1:
            raise ValueError("sdk_provision_scope_invalid")
        self.account = account
        self.password = Path(password_file).read_text(encoding="utf-8").strip()
        if not self.password:
            raise ValueError("sdk_provision_password_empty")
        self.product_id = product_id
        self.group_id = group_id
        self.transport = transport

    @staticmethod
    def _validate(account: str, display_name: str, marker: str) -> None:
        if not _ACCOUNT.fullmatch(account) or not _MARKER.fullmatch(marker):
            raise ValueError("sdk_provision_identity_invalid")
        if not (1 <= len(display_name) <= 100) or any(ord(ch) < 32 for ch in display_name):
            raise ValueError("sdk_provision_display_name_invalid")

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url, transport=self.transport,
            timeout=8, trust_env=False, follow_redirects=False,
        )

    @staticmethod
    def _request(client: httpx.Client, method: str, path: str, *, token: str | None = None, payload: dict | None = None):
        try:
            with client.stream(method, path, headers={"Token": token} if token else {}, json=payload) as response:
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _MAX_RESPONSE:
                        raise ProvisionRejected("sdk_provision_response_too_large")
                status = response.status_code
        except (httpx.TimeoutException, httpx.TransportError):
            raise ProvisionUnavailable("sdk_provision_zentao_unavailable") from None
        if status == 404:
            return None
        if status >= 500:
            raise ProvisionUnavailable("sdk_provision_zentao_unavailable")
        if status not in {200, 201}:
            raise ProvisionRejected("sdk_provision_zentao_rejected")
        try:
            return json.loads(chunks)
        except (UnicodeDecodeError, ValueError):
            raise ProvisionRejected("sdk_provision_invalid_response") from None

    def _login(self, client: httpx.Client) -> str:
        body = self._request(
            client, "POST", "/api.php/v2/users/login",
            payload={"account": self.account, "password": self.password},
        )
        token = body.get("token") if isinstance(body, dict) and body.get("status") == "success" else None
        if not isinstance(token, str) or not 1 <= len(token) <= 512:
            raise ProvisionRejected("sdk_provision_auth_failed")
        return token

    def _user(self, client: httpx.Client, token: str, account: str) -> dict | None:
        body = self._request(client, "GET", f"/api.php/v1/users/{account}", token=token)
        if body is None:
            return None
        if not isinstance(body, dict) or body.get("account") != account:
            raise ProvisionRejected("sdk_provision_user_response_invalid")
        return body

    def _verify_identity(self, client: httpx.Client, token: str, account: str, marker: str) -> dict:
        user = self._user(client, token, account)
        if user is None:
            raise ProvisionUnavailable("sdk_provision_reconcile_required")
        if user.get("commiter") != marker:
            raise ProvisionConflict("sdk_provision_account_conflict")
        return user

    def _group_ids(self, client: httpx.Client, token: str, account: str) -> set[int]:
        groups = self._request(client, "GET", "/api.php/v1/groups", token=token)
        if not isinstance(groups, list):
            raise ProvisionRejected("sdk_provision_group_unconfirmed")
        return {
            int(group["id"]) for group in groups
            if isinstance(group, dict) and str(group.get("id", "")).isdigit()
            and isinstance(group.get("accounts"), dict) and account in group["accounts"]
        }

    def _product_whitelist(self, client: httpx.Client, token: str) -> list[str]:
        product_path = f"/api.php/v1/products/{self.product_id}"
        product = self._request(client, "GET", product_path, token=token)
        if not isinstance(product, dict) or str(product.get("id")) != str(self.product_id) or product.get("acl") != "private":
            raise ProvisionRejected("sdk_provision_product_invalid")
        return self._whitelist(product)

    @staticmethod
    def _whitelist(product: dict) -> list[str]:
        values = product.get("whitelist")
        if not isinstance(values, list) or len(values) > 5000:
            raise ProvisionRejected("sdk_provision_whitelist_invalid")
        names = [row.get("account") for row in values if isinstance(row, dict)]
        if len(names) != len(values) or not all(isinstance(name, str) and name for name in names):
            raise ProvisionRejected("sdk_provision_whitelist_invalid")
        return names

    def create(self, account: str, display_name: str, marker: str) -> None:
        self._validate(account, display_name, marker)
        with self._client() as client:
            token = self._login(client)
            if self._user(client, token, account) is not None:
                raise ProvisionConflict("sdk_provision_account_conflict")
            password = secrets.token_urlsafe(36) + "A1!"
            body = self._request(
                client, "POST", "/api.php/v2/users", token=token,
                payload={"account": account, "password": password, "realname": display_name,
                         "group": "", "visions": ["rnd"], "commiter": marker},
            )
            if not isinstance(body, dict) or body.get("status") != "success" or not isinstance(body.get("id"), int) or body["id"] < 1:
                raise ProvisionRejected("sdk_provision_create_unconfirmed")
            self._verify_identity(client, token, account, marker)
            if self._group_ids(client, token, account) or account in self._product_whitelist(client, token):
                raise ProvisionRejected("sdk_provision_unprivileged_create_failed")

    def reconcile(self, account: str, display_name: str, marker: str) -> None:
        self._validate(account, display_name, marker)
        with self._client() as client:
            token = self._login(client)
            self._verify_identity(client, token, account, marker)
            if self._group_ids(client, token, account) or account in self._product_whitelist(client, token):
                raise ProvisionRejected("sdk_provision_unprivileged_reconcile_failed")

    def set_access(self, account: str, marker: str, *, enabled: bool) -> None:
        if not _ACCOUNT.fullmatch(account) or not _MARKER.fullmatch(marker):
            raise ValueError("sdk_provision_identity_invalid")
        with self._client() as client:
            token = self._login(client)
            user = self._verify_identity(client, token, account, marker)
            user_id = user.get("id")
            if not isinstance(user_id, int) or user_id < 1:
                raise ProvisionRejected("sdk_provision_user_response_invalid")
            desired_groups = {self.group_id} if enabled else set()
            if self._group_ids(client, token, account) != desired_groups:
                changed = self._request(
                    client, "PUT", f"/api.php/v2/users/{user_id}", token=token,
                    payload={"group": [self.group_id] if enabled else []},
                )
                if not isinstance(changed, dict) or changed.get("status") != "success":
                    raise ProvisionRejected("sdk_provision_group_update_rejected")
            if self._group_ids(client, token, account) != desired_groups:
                raise ProvisionRejected("sdk_provision_group_unconfirmed")
            product_path = f"/api.php/v1/products/{self.product_id}"
            names = self._product_whitelist(client, token)
            desired = [*names, account] if enabled and account not in names else [name for name in names if name != account] if not enabled else names
            if desired != names:
                self._request(client, "PUT", product_path, token=token, payload={"whitelist": desired})
            confirmed = self._product_whitelist(client, token)
            if (account in confirmed) != enabled or not set(desired).issubset(confirmed):
                raise ProvisionRejected("sdk_provision_product_unconfirmed")
