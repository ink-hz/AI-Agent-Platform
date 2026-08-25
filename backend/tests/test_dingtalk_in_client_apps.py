from __future__ import annotations

import json

import pytest

from app.control_plane.in_client_apps import (
    TrustedInClientApp,
    load_trusted_in_client_apps,
)


def _write_registry(tmp_path, payload: object):
    path = tmp_path / "dingtalk-in-client-apps.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_loads_one_trusted_office_application(tmp_path) -> None:
    path = _write_registry(
        tmp_path,
        {
            "schema_version": 1,
            "apps": [
                {
                    "id": "office",
                    "app_key": "office-key",
                    "app_secret": "office-secret",
                    "return_paths": ["/office/"],
                }
            ],
        },
    )

    assert load_trusted_in_client_apps(str(path), route_prefix="/") == (
        TrustedInClientApp(
            "office", "office-key", "office-secret", ("/office/",)
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": 2, "apps": []},
        {"schema_version": 1, "apps": [], "extra": True},
        {
            "schema_version": 1,
            "apps": [
                {
                    "id": "platform",
                    "app_key": "key",
                    "app_secret": "secret",
                    "return_paths": ["/office/"],
                }
            ],
        },
        {
            "schema_version": 1,
            "apps": [
                {
                    "id": "Office",
                    "app_key": "key",
                    "app_secret": "secret",
                    "return_paths": ["/office/"],
                }
            ],
        },
        {
            "schema_version": 1,
            "apps": [
                {
                    "id": "office",
                    "app_key": "key",
                    "app_secret": "secret",
                    "return_paths": ["https://evil.example/"],
                }
            ],
        },
        {
            "schema_version": 1,
            "apps": [
                {
                    "id": "office",
                    "app_key": "key",
                    "app_secret": "secret",
                    "return_paths": ["/office/?secret=1"],
                }
            ],
        },
        {
            "schema_version": 1,
            "apps": [
                {
                    "id": "office",
                    "app_key": "key",
                    "app_secret": "secret",
                    "return_paths": [],
                }
            ],
        },
    ],
)
def test_rejects_malformed_or_unsafe_registry(tmp_path, payload) -> None:
    path = _write_registry(tmp_path, payload)

    with pytest.raises(
        ValueError, match="trusted DingTalk application registry invalid"
    ):
        load_trusted_in_client_apps(str(path), route_prefix="/")


@pytest.mark.parametrize("duplicate", ["id", "key", "return_path"])
def test_rejects_duplicate_ids_app_keys_and_return_paths(
    tmp_path, duplicate: str
) -> None:
    first = {
        "id": "office",
        "app_key": "office-key",
        "app_secret": "first-secret",
        "return_paths": ["/office/"],
    }
    second = {
        "id": "people",
        "app_key": "people-key",
        "app_secret": "second-secret",
        "return_paths": ["/account"],
    }
    if duplicate == "id":
        second["id"] = first["id"]
    elif duplicate == "key":
        second["app_key"] = first["app_key"]
    else:
        second["return_paths"] = first["return_paths"]
    path = _write_registry(
        tmp_path, {"schema_version": 1, "apps": [first, second]}
    )

    with pytest.raises(
        ValueError, match="trusted DingTalk application registry invalid"
    ):
        load_trusted_in_client_apps(str(path), route_prefix="/")


def test_rejects_unknown_application_fields_without_echoing_values(tmp_path) -> None:
    path = _write_registry(
        tmp_path,
        {
            "schema_version": 1,
            "apps": [
                {
                    "id": "office",
                    "app_key": "public-key-secret-marker",
                    "app_secret": "private-secret-marker",
                    "return_paths": ["/office/"],
                    "provider_token": "provider-token-secret-marker",
                }
            ],
        },
    )

    with pytest.raises(ValueError) as captured:
        load_trusted_in_client_apps(str(path), route_prefix="/")

    rendered = str(captured.value)
    assert "public-key-secret-marker" not in rendered
    assert "private-secret-marker" not in rendered
    assert "provider-token-secret-marker" not in rendered


def test_repr_never_contains_application_credentials() -> None:
    application = TrustedInClientApp(
        "office", "public-key", "private-secret", ("/office/",)
    )

    assert "public-key" not in repr(application)
    assert "private-secret" not in repr(application)
    assert "office" in repr(application)


def test_rejects_oversized_and_symlinked_registry(tmp_path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * 65_537)
    oversized.chmod(0o600)
    target = _write_registry(tmp_path, {"schema_version": 1, "apps": []})
    symlink = tmp_path / "registry-link.json"
    symlink.symlink_to(target)

    for path in (oversized, symlink):
        with pytest.raises(
            ValueError, match="trusted DingTalk application registry unavailable"
        ):
            load_trusted_in_client_apps(str(path), route_prefix="/")
