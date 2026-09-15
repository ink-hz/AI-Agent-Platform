from uuid import uuid4

import pytest
from app.control_plane.auth import validate_return_path
from app.control_plane.middleware import is_public_request


def test_new_hr_shell_is_public_but_apis_are_not():
    assert is_public_request("GET", "/hr/agent", "/")
    assert not is_public_request("GET", "/api/hr/agent/threads", "/")
    assert not is_public_request("GET", "/hr/agent/unknown", "/")


@pytest.mark.parametrize("prefix", ["/", "/_preview/dingtalk-r1/"])
@pytest.mark.parametrize("local_path", ["/hr/", "/hr/agent"])
def test_hr_login_preserves_exact_work_and_position(prefix, local_path):
    path = prefix.rstrip("/") + local_path
    assert validate_return_path(path, route_prefix=prefix) == path
    work_query = f"?work={uuid4()}"
    assert validate_return_path(path + work_query, route_prefix=prefix) == path + work_query
    query = f"?position={uuid4()}&work={uuid4()}"
    assert validate_return_path(path + query, route_prefix=prefix) == path + query


@pytest.mark.parametrize(
    "query",
    [
        "?work=current",
        "?work=" + str(uuid4()) + "&work=" + str(uuid4()),
        "?next=https://evil.test",
        "?work=%2F%2Fevil.test",
        "?work=",
        "?position=" + str(uuid4()) + "#bad",
    ],
)
@pytest.mark.parametrize("local_path", ["/hr/", "/hr/agent"])
def test_hr_login_rejects_unsafe_or_ambiguous_query(query, local_path):
    with pytest.raises(ValueError):
        validate_return_path(local_path + query, route_prefix="/")
