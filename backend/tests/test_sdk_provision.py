from __future__ import annotations

import json

import httpx
import pytest

from app.sdk_workbench.provision import (
    ProvisionConflict, ProvisionUnavailable, ZenTaoProvisioner,
)


ACCOUNT = "sdk_abcdefghijklmnopqrstuvwxyz"
MARKER = "sdk:1234567890abcdef1234567890abcdef"


def provisioner(tmp_path, respond):
    secret = tmp_path / "provision-password"
    secret.write_text("probe-admin-password\n")
    return ZenTaoProvisioner(
        "http://zentao:80", "sdk-provision", secret,
        product_id=1, group_id=24, transport=httpx.MockTransport(respond),
    )


def test_create_user_starts_without_zen_permissions_until_platform_binding_is_committed(tmp_path):
    calls = []
    created = False
    whitelist = ["admin"]

    def respond(request):
        nonlocal created, whitelist
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/v2/users/login"):
            assert json.loads(request.content) == {"account": "sdk-provision", "password": "probe-admin-password"}
            return httpx.Response(200, json={"status": "success", "token": "token"})
        assert request.headers["Token"] == "token"
        if path.endswith("/v1/users/" + ACCOUNT):
            return httpx.Response(200, json={"id": 9, "account": ACCOUNT, "realname": "SDK User", "commiter": MARKER}) if created else httpx.Response(404)
        if path.endswith("/v2/users"):
            data = json.loads(request.content)
            assert data["account"] == ACCOUNT
            assert data["realname"] == "SDK User"
            assert data["commiter"] == MARKER
            assert data["group"] == ""
            assert "gender" not in data
            assert "role" not in data
            assert len(data["password"]) >= 32
            created = True
            return httpx.Response(200, json={"status": "success", "id": 9})
        if path.endswith("/v1/groups") and request.method == "GET":
            return httpx.Response(200, json=[{"id": 24, "accounts": {}}])
        if path.endswith("/v1/products/1") and request.method == "GET":
            return httpx.Response(200, json={"id": 1, "acl": "private", "whitelist": [{"account": "admin"}]})
        if path.endswith("/v1/products/1"):
            raise AssertionError("no Zen permissions may be assigned before platform binding")
        raise AssertionError(request.url)

    service = provisioner(tmp_path, respond)
    service.create(ACCOUNT, "SDK User", MARKER)
    assert whitelist == ["admin"]
    assert calls.count(("POST", "/api.php/v2/users")) == 1


def test_reconcile_accepts_verified_account_after_display_name_changes(tmp_path):
    def respond(request):
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": "success", "token": "token"})
        if request.url.path.endswith("/users/" + ACCOUNT):
            return httpx.Response(200, json={"id": 9, "account": ACCOUNT, "realname": "Previous Name", "commiter": MARKER})
        if request.url.path.endswith("/v1/groups"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/v1/products/1"):
            return httpx.Response(200, json={"id": 1, "acl": "private", "whitelist": []})
        raise AssertionError(request.url)

    provisioner(tmp_path, respond).reconcile(ACCOUNT, "Current Name", MARKER)


def test_existing_unmarked_account_is_never_auto_claimed(tmp_path):
    def respond(request):
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": "success", "token": "token"})
        if request.method == "GET" and request.url.path.endswith("/users/" + ACCOUNT):
            return httpx.Response(200, json={"account": ACCOUNT, "realname": "Other", "commiter": ""})
        raise AssertionError("must not create or bind an existing account")

    with pytest.raises(ProvisionConflict):
        provisioner(tmp_path, respond).create(ACCOUNT, "SDK User", MARKER)


def test_uncertain_create_can_be_reconciled_only_with_marker(tmp_path):
    def respond(request):
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": "success", "token": "token"})
        if request.method == "GET" and request.url.path.endswith("/users/" + ACCOUNT):
            return httpx.Response(200, json={"account": ACCOUNT, "realname": "SDK User", "commiter": "different"})
        raise AssertionError(request.url)

    with pytest.raises(ProvisionConflict):
        provisioner(tmp_path, respond).reconcile(ACCOUNT, "SDK User", MARKER)


def test_create_timeout_never_returns_success(tmp_path):
    def respond(request):
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": "success", "token": "token"})
        if request.method == "GET":
            return httpx.Response(404)
        raise httpx.ReadTimeout("response lost")

    with pytest.raises(ProvisionUnavailable):
        provisioner(tmp_path, respond).create(ACCOUNT, "SDK User", MARKER)


def test_suspend_and_resume_change_only_the_marked_account_access(tmp_path):
    groups = [24]
    whitelist = ["admin", ACCOUNT]
    calls = []

    def respond(request):
        nonlocal groups, whitelist
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/v2/users/login"):
            return httpx.Response(200, json={"status": "success", "token": "token"})
        if path.endswith("/v1/users/" + ACCOUNT):
            return httpx.Response(200, json={"id": 9, "account": ACCOUNT, "realname": "SDK User", "commiter": MARKER})
        if path.endswith("/v2/users/9") and request.method == "PUT":
            groups = json.loads(request.content)["group"]
            return httpx.Response(200, json={"status": "success"})
        if path.endswith("/v1/groups"):
            return httpx.Response(200, json=[{"id": 24, "accounts": {ACCOUNT: "SDK User"} if 24 in groups else {}}])
        if path.endswith("/v1/products/1") and request.method == "GET":
            return httpx.Response(200, json={"id": 1, "acl": "private", "whitelist": [{"account": name} for name in whitelist]})
        if path.endswith("/v1/products/1") and request.method == "PUT":
            whitelist = json.loads(request.content)["whitelist"]
            return httpx.Response(200, json={"id": 1})
        raise AssertionError(request.url)

    service = provisioner(tmp_path, respond)
    service.set_access(ACCOUNT, MARKER, enabled=False)
    assert groups == []
    assert whitelist == ["admin"]
    service.set_access(ACCOUNT, MARKER, enabled=True)
    assert groups == [24]
    assert whitelist == ["admin", ACCOUNT]
    assert calls.count(("PUT", "/api.php/v2/users/9")) == 2
