from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from app.local_secrets import SecretFileUnavailable, read_secret_file

from .auth import validate_return_path


_APP_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_REGISTRY_FIELDS = frozenset({"schema_version", "apps"})
_APPLICATION_FIELDS = frozenset(
    {"id", "app_key", "app_secret", "return_paths"}
)
_MAX_APPLICATIONS = 32
_MAX_RETURN_PATHS = 32


@dataclass(frozen=True, repr=False)
class TrustedInClientApp:
    app_id: str
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    return_paths: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"TrustedInClientApp(app_id={self.app_id!r}, "
            "app_key=<redacted>, app_secret=<redacted>, "
            f"return_paths={self.return_paths!r})"
        )


def _required_string(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError
    return value


def _parse_application(value: object, *, route_prefix: str) -> TrustedInClientApp:
    if not isinstance(value, dict) or frozenset(value) != _APPLICATION_FIELDS:
        raise ValueError
    app_id = _required_string(value["id"], maximum=32)
    if app_id == "platform" or _APP_ID.fullmatch(app_id) is None:
        raise ValueError
    app_key = _required_string(value["app_key"], maximum=512)
    app_secret = _required_string(value["app_secret"], maximum=4096)
    raw_paths = value["return_paths"]
    if (
        not isinstance(raw_paths, list)
        or not raw_paths
        or len(raw_paths) > _MAX_RETURN_PATHS
    ):
        raise ValueError
    return_paths: list[str] = []
    for raw_path in raw_paths:
        path = _required_string(raw_path, maximum=512)
        selected = validate_return_path(path, route_prefix=route_prefix)
        if selected in return_paths:
            raise ValueError
        return_paths.append(selected)
    return TrustedInClientApp(
        app_id=app_id,
        app_key=app_key,
        app_secret=app_secret,
        return_paths=tuple(return_paths),
    )


def load_trusted_in_client_apps(
    path: str, *, route_prefix: str
) -> tuple[TrustedInClientApp, ...]:
    try:
        raw = read_secret_file(path, max_bytes=65_536)
    except SecretFileUnavailable:
        raise ValueError(
            "trusted DingTalk application registry unavailable"
        ) from None
    try:
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or frozenset(payload) != _REGISTRY_FIELDS
            or payload.get("schema_version") != 1
            or isinstance(payload.get("schema_version"), bool)
        ):
            raise ValueError
        raw_apps = payload.get("apps")
        if not isinstance(raw_apps, list) or len(raw_apps) > _MAX_APPLICATIONS:
            raise ValueError
        applications = tuple(
            _parse_application(value, route_prefix=route_prefix)
            for value in raw_apps
        )
        app_ids = {application.app_id for application in applications}
        app_keys = {application.app_key for application in applications}
        return_paths = {
            path
            for application in applications
            for path in application.return_paths
        }
        expected_paths = sum(
            len(application.return_paths) for application in applications
        )
        if (
            len(app_ids) != len(applications)
            or len(app_keys) != len(applications)
            or len(return_paths) != expected_paths
        ):
            raise ValueError
        return applications
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError(
            "trusted DingTalk application registry invalid"
        ) from None
