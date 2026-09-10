from uuid import uuid4

import pytest
from app.control_plane.auth import validate_return_path
from app.control_plane.middleware import is_public_request


def test_new_hr_shell_is_public_but_apis_are_not():
    assert is_public_request("GET", "/hr/agent", "/")
    assert not is_public_request("GET", "/api/hr/agent/threads", "/")
    assert not is_public_request("GET", "/hr/agent/unknown", "/")


@pytest.mark.parametrize("prefix", ["/", "/_preview/dingtalk-r1/"])
def test_hr_login_preserves_exact_work_and_position(prefix):
    path = prefix.rstrip("/") + "/hr/agent"
    assert validate_return_path(path, route_prefix=prefix) == path
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
def test_hr_login_rejects_unsafe_or_ambiguous_query(query):
    with pytest.raises(ValueError):
        validate_return_path("/hr/agent" + query, route_prefix="/")
