"""Optional native readers use the real host identity and route boundary."""
from uuid import uuid4

import pytest

from app.control_plane.authorization import AuthorizationService
from app.control_plane.models import AuthContext, Role
from test_dingtalk_auth_api import FakeAuth, _NoObservationGrants
from test_manufacturing_bridge import BASE, bundle, client


NATIVE_ROUTES = (
    ("GET", "/sap/status"),
    ("GET", "/sap/interfaces"),
    ("GET", "/sap/interfaces/{function}/contract"),
    ("POST", "/sap/diagnostics/ping"),
    ("POST", "/sap/interfaces/refresh"),
    ("POST", "/sap/mrp/stock-requirements"),
    ("GET", "/mes/status"),
    ("GET", "/mes/interfaces"),
    ("POST", "/mes/diagnostics/login"),
    ("POST", "/mes/interfaces/{interface}/query"),
)


@pytest.mark.parametrize("method,path", NATIVE_ROUTES)
@pytest.mark.parametrize("role", list(Role))
def test_native_routes_retain_management_policy(method, path, role):
    policy = AuthorizationService(_NoObservationGrants())
    actor = AuthContext(uuid4(), role, uuid4(), False)
    route = BASE + path
    assert policy.decide(None, method, route, ()).status_code == 401
    assert policy.decide(actor, method, route, ()).allowed is (
        role in {Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER}
    )
    assert not policy.decide(actor, method, route + "/extra", ()).allowed


@pytest.fixture
def native_bundle(bundle, monkeypatch, tmp_path):
    # Only the host protocol is doubled. The real middleware, route matcher and
    # actor callback below are the production implementations.
    source = bundle / "src/manufacturing_platform/__init__.py"
    with source.open("a") as stream:
        stream.write('''
native_calls = []
def _native_service(config_path, audit_path):
 return (config_path, audit_path)
build_sap_access_service = _native_service
build_mes_access_service = _native_service
def _native_router(system, service, authorize):
 r = APIRouter(prefix="/api/v1/manufacturing/" + system)
 @r.get("/status")
 def status(request: Request):
  actor = authorize(request)
  assert actor["configuration_admin"] is True
  native_calls.append((system, actor["user_id"]))
  return {"source": system, "actor": actor["user_id"]}
 @r.post("/" + ("mrp/stock-requirements" if system == "sap" else "interfaces/{interface}/query"))
 def query(request: Request):
  actor = authorize(request, write=True)
  assert actor["configuration_admin"] is True
  native_calls.append((system, actor["user_id"]))
  return {"source": system, "actor": actor["user_id"]}
 return r
def build_sap_access_router(*, service, authorize):
 return _native_router("sap", service, authorize)
def build_mes_access_router(*, service, authorize):
 return _native_router("mes", service, authorize)
''')
    for system in ("SAP", "MES"):
        monkeypatch.setenv(f"PLATFORM_MANUFACTURING_{system}_CONFIG_PATH", str(tmp_path / f"{system}-private.json"))
        monkeypatch.setenv(f"PLATFORM_MANUFACTURING_{system}_AUDIT_PATH", str(tmp_path / f"{system}-audit.json"))
    return bundle


def test_native_mount_uses_trusted_actor_and_real_post_security(native_bundle, tmp_path):
    auth = FakeAuth()
    http = client(auth)
    for path in ("/sap/status", "/mes/status"):
        assert http.get(BASE + path).status_code == 401
    assert not list(tmp_path.glob("*-*.json"))
    http.cookies.set(auth.cookie_name, "valid-cookie")
    for system in ("sap", "mes"):
        response = http.get(BASE + f"/{system}/status", headers={"X-User-ID": "forged"})
        assert response.status_code == 200, response.text
        assert response.json() == {"source": system, "actor": str(auth.context.internal_user_id)}
        assert response.headers["cache-control"] == "private, no-store"
    query = BASE + "/sap/mrp/stock-requirements"
    assert http.post(query, content="invalid").status_code == 403
    headers = {"Origin": "https://agent.example.test", "X-CSRF-Token": auth.csrf}
    assert http.post(query, headers=headers, json={"material": "M", "plant": "1000"}).status_code == 200
    auth.context = AuthContext(auth.context.internal_user_id, Role.PLATFORM_OWNER, auth.context.session_id, True)
    assert http.post(query, headers=headers, json={}).status_code == 503
    assert http.get(BASE + "/sap/status").status_code == 200
    auth.context = AuthContext(uuid4(), Role.MEMBER, auth.context.session_id, False)
    assert http.get(BASE + "/mes/status").status_code == 403
    assert not list(tmp_path.glob("*-*.json"))


@pytest.mark.parametrize("bad", ["missing", "relative", "alias"])
def test_invalid_optional_source_keeps_other_source_and_legacy(native_bundle, monkeypatch, tmp_path, bad):
    if bad == "missing":
        monkeypatch.delenv("PLATFORM_MANUFACTURING_SAP_AUDIT_PATH")
    elif bad == "relative":
        monkeypatch.setenv("PLATFORM_MANUFACTURING_SAP_CONFIG_PATH", "relative.json")
    else:
        monkeypatch.setenv("PLATFORM_MANUFACTURING_SAP_AUDIT_PATH", str(tmp_path / "SAP-private.json"))
    auth = FakeAuth()
    http = client(auth)
    assert http.get(BASE + "/sap/status").status_code == 401
    http.cookies.set(auth.cookie_name, "valid-cookie")
    response = http.get(BASE + "/sap/status")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert http.get(BASE + "/mes/status").status_code == 200
    assert http.get(BASE + "/catalog").status_code == 200


@pytest.mark.parametrize("configured", [False, True])
def test_older_release_reserves_unconfigured_native_routes(bundle, monkeypatch, tmp_path, configured):
    if configured:
        monkeypatch.setenv("PLATFORM_MANUFACTURING_SAP_CONFIG_PATH", str(tmp_path / "old-config.json"))
        monkeypatch.setenv("PLATFORM_MANUFACTURING_SAP_AUDIT_PATH", str(tmp_path / "old-audit.json"))
    auth = FakeAuth()
    http = client(auth)
    assert http.get(BASE + "/sap/status").status_code == 401
    http.cookies.set(auth.cookie_name, "valid-cookie")
    for system in ("sap", "mes"):
        response = http.get(BASE + f"/{system}/status")
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"
    assert http.get(BASE + "/catalog").status_code == 200


@pytest.mark.parametrize("hardlink", [False, True])
def test_native_audit_cannot_alias_business_data(native_bundle, monkeypatch, tmp_path, hardlink):
    database = tmp_path / "business.sqlite3"
    database.write_bytes(b"business sentinel")
    audit = database
    if hardlink:
        audit = tmp_path / "audit-hardlink.json"
        audit.hardlink_to(database)
    monkeypatch.setenv("PLATFORM_MANUFACTURING_DATABASE_PATH", str(database))
    monkeypatch.setenv("PLATFORM_MANUFACTURING_SAP_AUDIT_PATH", str(audit))
    auth = FakeAuth()
    http = client(auth)
    http.cookies.set(auth.cookie_name, "valid-cookie")
    assert http.get(BASE + "/sap/status").status_code == 503
    assert http.get(BASE + "/mes/status").status_code == 200
    assert http.get(BASE + "/catalog").status_code == 200
    assert database.read_bytes() == b"business sentinel"


@pytest.mark.parametrize("collision", ["other_audit_lock", "own_lock_config", "production_config", "snapshot", "investigations", "business_wal"])
def test_native_storage_rejects_auxiliary_collisions(native_bundle, monkeypatch, tmp_path, collision):
    snapshot = str(tmp_path / "snapshot.sqlite3")
    production_config = str(tmp_path / "production-private.json")
    monkeypatch.setenv("PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_CONFIG_PATH", production_config)
    monkeypatch.setenv("PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_SNAPSHOT_PATH", snapshot)
    if collision == "own_lock_config":
        monkeypatch.setenv("PLATFORM_MANUFACTURING_SAP_CONFIG_PATH", str(tmp_path / "SAP-audit.json.lock"))
    else:
        target = {
            "other_audit_lock": str(tmp_path / "MES-audit.json.lock"),
            "production_config": production_config,
            "snapshot": snapshot,
            "investigations": snapshot + ".investigations.sqlite3",
            "business_wal": str(tmp_path / "db/workbench.sqlite3-wal"),
        }[collision]
        monkeypatch.setenv("PLATFORM_MANUFACTURING_SAP_AUDIT_PATH", target)
    auth = FakeAuth()
    http = client(auth)
    http.cookies.set(auth.cookie_name, "valid-cookie")
    assert http.get(BASE + "/sap/status").status_code == 503
    assert http.get(BASE + "/catalog").status_code == 200
    assert not list(tmp_path.glob("*.json"))


@pytest.fixture
def workflow_native_bundle(native_bundle, monkeypatch, tmp_path):
    source = native_bundle / "src/manufacturing_platform/__init__.py"
    with source.open("a") as stream:
        stream.write('''
native_constructions = []
native_router_services = {}
def _tracked_service(config_path, audit_path):
 service = object()
 native_constructions.append(service)
 return service
build_sap_access_service = _tracked_service
build_mes_access_service = _tracked_service
_original_native_router = _native_router
def _native_router(system, service, authorize):
 native_router_services[system] = service
 return _original_native_router(system, service, authorize)
WORKFLOW_API_VERSION = 1
def build_workflow_router(*, database_path, authority_database_path, assets_dir,
 authenticate, resolve_subject, release, dataset, mes_access_service=None):
 r = APIRouter(prefix="/api/v1/manufacturing/workflow")
 @r.get("/access")
 def access(request: Request):
  authenticate(request)
  return {"available": True, "mes_injected": mes_access_service is not None,
   "same_mes_service": mes_access_service is native_router_services.get("mes"),
   "construction_count": len(native_constructions)}
 return r
''')
    for key, value in {
        "WORKFLOW_DATABASE_PATH": str(tmp_path / "workflow.db"),
        "AUTHORITY_DATABASE_PATH": str(tmp_path / "authority.db"),
        "DATASET_ID": "native-test", "DATASET_MODE": "test", "DATASET_LABEL": "Test",
    }.items():
        monkeypatch.setenv("PLATFORM_MANUFACTURING_" + key, value)
    import test_manufacturing_bridge
    from app.manufacturing import build_manufacturing_router
    monkeypatch.setattr(test_manufacturing_bridge, "bridge", lambda: build_manufacturing_router(
        resolve_workflow_subject=lambda uid: True,
    ))
    return source


def test_workflow_and_native_router_share_one_lazy_mes_service(workflow_native_bundle, tmp_path):
    auth = FakeAuth()
    http = client(auth)
    # Config paths deliberately don't exist: mounting must remain lazy.
    assert not list(tmp_path.glob("*.json"))
    http.cookies.set(auth.cookie_name, "valid-cookie")
    response = http.get(BASE + "/workflow/access")
    assert response.status_code == 200, response.text
    assert response.json() == {"available": True, "mes_injected": True,
                               "same_mes_service": True, "construction_count": 2}
    assert http.get(BASE + "/mes/status").status_code == 200
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("failure", ["missing", "alias", "router"])
def test_unusable_mes_is_not_injected_and_keeps_workflow_and_sap(
    workflow_native_bundle, monkeypatch, tmp_path, failure
):
    if failure == "missing":
        monkeypatch.delenv("PLATFORM_MANUFACTURING_MES_AUDIT_PATH")
    elif failure == "alias":
        monkeypatch.setenv("PLATFORM_MANUFACTURING_MES_AUDIT_PATH", str(tmp_path / "workflow.db-wal"))
    else:
        with workflow_native_bundle.open("a") as stream:
            stream.write('''
def build_mes_access_router(*, service, authorize):
 r = APIRouter()
 r.add_api_route("/outside-mes", lambda: {})
 return r
''')
    auth = FakeAuth()
    http = client(auth)
    http.cookies.set(auth.cookie_name, "valid-cookie")
    response = http.get(BASE + "/workflow/access")
    assert response.status_code == 200, response.text
    assert response.json()["mes_injected"] is False
    assert http.get(BASE + "/mes/status").status_code == 503
    assert http.get(BASE + "/sap/status").status_code == 200


def test_older_workflow_signature_remains_usable_and_new_read_returns_503(workflow_native_bundle):
    with workflow_native_bundle.open("a") as stream:
        stream.write('''
_current_workflow = build_workflow_router
def build_workflow_router(*, database_path, authority_database_path, assets_dir,
 authenticate, resolve_subject, release, dataset):
 return _current_workflow(database_path=database_path, authority_database_path=authority_database_path,
  assets_dir=assets_dir, authenticate=authenticate, resolve_subject=resolve_subject,
  release=release, dataset=dataset)
''')
    auth = FakeAuth()
    http = client(auth)
    http.cookies.set(auth.cookie_name, "valid-cookie")
    assert http.get(BASE + "/workflow/access").status_code == 200
    assert http.get(BASE + "/mes/status").status_code == 200
    response = http.get(BASE + "/workflow/projects/invalid/work-orders/invalid/mes-materials")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
