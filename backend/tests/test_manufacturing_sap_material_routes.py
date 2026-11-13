"""Real SAP test reads retain host identity, CSRF and exact route matching."""
import pytest

from app.manufacturing_paths import WORKFLOW_ROUTES
from test_dingtalk_auth_api import FakeAuth
from test_manufacturing_bridge import BASE, client
from test_manufacturing_native_access import workflow_native_bundle, native_bundle, bundle

PATHS = ("/material-control/sap/status", "/material-control/sap/production-order",
         "/material-control/sap/stock-requirements")


def test_sap_reads_are_exactly_registered():
    for path in PATHS:
        assert ("GET", path) in WORKFLOW_ROUTES
    for path in PATHS[1:]:
        assert ("POST", path) in WORKFLOW_ROUTES
    assert ("POST", PATHS[0]) not in WORKFLOW_ROUTES


def test_older_workflow_reserves_sap_reads(workflow_native_bundle):
    auth = FakeAuth()
    http = client(auth)
    for path in PATHS:
        assert http.get(BASE + "/workflow" + path).status_code == 401
    http.cookies.set(auth.cookie_name, "valid-cookie")
    for path in PATHS:
        response = http.get(BASE + "/workflow" + path)
        assert response.status_code == 503, response.text
        assert response.headers["cache-control"] == "private, no-store"
    for path in PATHS[1:]:
        route = BASE + "/workflow" + path
        assert http.post(route).status_code == 403
        response = http.post(route, headers={"Origin": "https://agent.example.test", "X-CSRF-Token": auth.csrf})
        assert response.status_code == 503


def test_same_lazy_sap_reader_is_injected(workflow_native_bundle, tmp_path):
    with workflow_native_bundle.open("a") as stream:
        stream.write('''
def build_workflow_router(*, database_path, authority_database_path, assets_dir,
 authenticate, resolve_subject, release, dataset, sap_access_service=None):
 r = APIRouter(prefix="/api/v1/manufacturing/workflow")
 @r.get("/material-control/sap/status")
 def status(request: Request):
  actor = authenticate(request)
  return {"injected": sap_access_service is not None,
   "same": sap_access_service is native_router_services.get("sap"),
   "actor": actor["user_id"], "count": len(native_constructions)}
 return r
''')
    auth = FakeAuth()
    http = client(auth)
    http.cookies.set(auth.cookie_name, "valid-cookie")
    response = http.get(BASE + "/workflow" + PATHS[0])
    assert response.status_code == 200, response.text
    assert response.json() == {"injected": True, "same": True,
                               "actor": str(auth.context.internal_user_id), "count": 2}
    assert not list(tmp_path.glob("*.json"))
