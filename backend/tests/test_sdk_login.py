import pytest
from app.control_plane.auth import validate_return_path
from app.control_plane.middleware import is_public_request


@pytest.mark.parametrize("path", ["/sdk", "/sdk/", "/sdk/requirements/1"])
def test_sdk_browser_shell_is_public_but_api_requires_session(path):
    assert is_public_request("GET", path, "/")
    assert is_public_request("GET", "/_preview/dingtalk-r1" + path, "/_preview/dingtalk-r1/")
    assert not is_public_request("GET", "/api/v1/sdk/me", "/")


@pytest.mark.parametrize("path", ["/sdk/", "/sdk/requirements/1"])
def test_sdk_login_returns_to_requested_page(path):
    assert validate_return_path(path, route_prefix="/") == path
    preview = "/_preview/dingtalk-r1" + path
    assert validate_return_path(preview, route_prefix="/_preview/dingtalk-r1/") == preview


@pytest.mark.parametrize("path", ["/sdk/other", "/sdk/requirements/0", "/sdk/requirements/1/extra", "/sdk/?next=https://evil.test"])
def test_sdk_login_rejects_unknown_or_unsafe_pages(path):
    with pytest.raises(ValueError):
        validate_return_path(path, route_prefix="/")
    if "?" not in path:
        assert not is_public_request("GET", path, "/")
