"""Exact host authorization contract shared by enabled and unavailable routers."""

MANUFACTURING_PREFIX = "/api/v1/manufacturing"
MANUFACTURING_ROUTES = frozenset(
    {
        ("GET", "/access"),
        ("GET", "/health"),
        ("GET", "/catalog"),
        ("GET", "/assets/{asset_name}"),
        ("GET", "/lines/{line_id}"),
        ("GET", "/lines/{line_id}/stages/{stage_id}"),
        ("PUT", "/lines/{line_id}/stages/{stage_id}"),
        ("POST", "/lines/{line_id}/stages/{stage_id}/tasks"),
        ("PATCH", "/lines/{line_id}/stages/{stage_id}/tasks/{task_id}"),
        ("GET", "/dashboard"),
        ("GET", "/projects"),
        ("POST", "/projects"),
        ("GET", "/projects/{project_id}"),
        ("PATCH", "/projects/{project_id}"),
        ("PUT", "/projects/{project_id}/stages/{stage_id}"),
        ("POST", "/projects/{project_id}/tasks"),
        ("PATCH", "/projects/{project_id}/tasks/{task_id}"),
        ("GET", "/project-tasks"),
        ("GET", "/handbook"),
        ("GET", "/collaboration"),
        ("POST", "/systems"),
        ("PATCH", "/systems/{system_id}"),
        ("POST", "/authorities"),
        ("PATCH", "/authorities/{authority_id}"),
        ("GET", "/projects/{project_id}/controls"),
        ("POST", "/projects/{project_id}/packages"),
        ("PUT", "/projects/{project_id}/packages/{package_id}"),
        ("POST", "/projects/{project_id}/packages/{package_id}/actions"),
        ("POST", "/projects/{project_id}/handoffs"),
        ("PATCH", "/projects/{project_id}/handoffs/{handoff_id}"),
        ("POST", "/projects/{project_id}/readiness"),
        ("PUT", "/projects/{project_id}/readiness/{readiness_id}"),
        ("POST", "/projects/{project_id}/readiness/{readiness_id}/issues"),
    }
)
MANUFACTURING_AUTH_ROUTES = frozenset(
    (method, MANUFACTURING_PREFIX + path) for method, path in MANUFACTURING_ROUTES
)

WORKFLOW_ROUTES = frozenset(
    {
        ("GET", "/access"),
        ("GET", "/assets/{asset_name}"),
        ("GET", "/handbook"),
        ("GET", "/dashboard"),
        ("GET", "/projects"),
        ("POST", "/projects"),
        ("GET", "/projects/{project_id}"),
        ("GET", "/projects/{project_id}/members"),
        ("PUT", "/projects/{project_id}/members/{user_id}"),
        ("GET", "/projects/{project_id}/objects/{kind}"),
        ("GET", "/projects/{project_id}/objects/{kind}/{object_id}"),
        ("POST", "/projects/{project_id}/commands"),
        ("GET", "/projects/{project_id}/trace"),
        ("GET", "/projects/{project_id}/exchanges"),
    }
)
WORKFLOW_AUTH_ROUTES = frozenset(
    (method, MANUFACTURING_PREFIX + "/workflow" + path)
    for method, path in WORKFLOW_ROUTES
) | frozenset({("GET", "/manufacturing"), ("GET", "/manufacturing/")})
