"""The new manager workspace deep link survives the existing login boundary."""
import pytest
from app.control_plane.auth import validate_return_path


@pytest.mark.parametrize("prefix", ["/", "/_preview/navigation/"])
def test_security_records_login_return_is_exact(prefix):
    path = prefix.rstrip("/") + "/admin/identity/security"
    assert validate_return_path(path, route_prefix=prefix) == path


@pytest.mark.parametrize("suffix", ["/admin/identity/security/unknown", "/admin/identity/security?redirect=/brain", "/admin/identity/%73ecurity", "/admin/identity/security#other"])
def test_security_records_login_return_does_not_broaden_identity_allowlist(suffix):
    with pytest.raises(ValueError):
        validate_return_path(suffix, route_prefix="/")
