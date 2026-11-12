"""Exact host authorization contract shared by enabled and unavailable routers."""

MANUFACTURING_PREFIX = "/api/v1/manufacturing"
NATIVE_ACCESS_ROUTES = frozenset({
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
})
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
MANUFACTURING_ROUTES |= NATIVE_ACCESS_ROUTES
MANUFACTURING_AUTH_ROUTES = frozenset(
    (method, MANUFACTURING_PREFIX + path) for method, path in MANUFACTURING_ROUTES
)

MATERIAL_REFERENCE_ROUTES = frozenset({
    ("GET", "/projects/{project_id}/material-task-references"),
})
MES_ORDER_ROUTES = frozenset({
    ("GET", "/projects/{project_id}/work-orders/{work_order_id}/mes-materials"),
})
WORKFLOW_ROUTES = MATERIAL_REFERENCE_ROUTES | MES_ORDER_ROUTES | frozenset(
    {
        ("GET", "/access"),
        ("GET", "/assets/{asset_name}"),
        ("GET", "/handbook"),
        ("GET", "/dashboard"),
        ("GET", "/objects/{kind}"),
        ("GET", "/tasks"),
        ("GET", "/material-control/overview"),
        ("GET", "/projects/{project_id}/material-control"),
        ("GET", "/projects/{project_id}/material-tasks"),
        ("GET", "/material-control/tasks"),
        ("GET", "/projects/{project_id}/material-control/assignees"),
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
        ("GET", "/projects/{project_id}/assistance"),
        ("GET", "/reusable-assets"),
        ("GET", "/projects/{project_id}/integration-intents"),
        ("GET", "/projects/{project_id}/integration-links"),
        ("GET", "/projects/{project_id}/systems"),
        ("GET", "/projects/{project_id}/systems/{system}/facts"),
        ("GET", "/projects/{project_id}/systems/{system}/facts/{object_type}/{external_id}"),
        ("GET", "/projects/{project_id}/systems/product_test/artifacts/{artifact_id}"),
    }
)
PRODUCTION_TOOLS_ROUTES = frozenset(
    {
        ("GET", "/production-tools/status"),
        ("GET", "/production-tools/projects"),
        ("GET", "/production-tools/projects/{source_project_id}/status"),
        ("GET", "/production-tools/projects/{source_project_id}/observations"),
        ("GET", "/production-tools/projects/{source_project_id}/catalog/{kind}"),
        ("GET", "/production-tools/projects/{source_project_id}/evidence"),
        ("GET", "/production-tools/projects/{source_project_id}/evidence/{source_log_id}/content"),
        ("GET", "/production-tools/projects/{source_project_id}/statistics"),
        ("GET", "/production-tools/projects/{source_project_id}/snapshots/{snapshot_id}"),
    }
)
PRODUCTION_TOOLS_ROUTES |= frozenset({
    ("GET", "/production-tools/overview"),
    ("GET", "/production-tools/projects/{source_project_id}/analysis"),
    ("GET", "/production-tools/projects/{source_project_id}/availability"),
    ("GET", "/production-tools/projects/{source_project_id}/investigations"),
    ("POST", "/production-tools/projects/{source_project_id}/investigations"),
    ("GET", "/production-tools/projects/{source_project_id}/investigations/{investigation_id}"),
    ("PUT", "/production-tools/projects/{source_project_id}/investigations/{investigation_id}"),
    ("GET", "/production-tools/projects/{source_project_id}/investigations/{investigation_id}/resolve"),
    ("POST", "/production-tools/projects/{source_project_id}/investigations/{investigation_id}/handoffs"),
    ("GET", "/production-tools/handoffs/{handoff_id}"),
    ("GET", "/production-tools/handoffs/{handoff_id}/resolve"),
    ("POST", "/production-tools/handoffs/{handoff_id}/fork"),
})
WORKFLOW_ROUTES |= PRODUCTION_TOOLS_ROUTES
WORKFLOW_AUTH_ROUTES = frozenset(
    (method, MANUFACTURING_PREFIX + "/workflow" + path)
    for method, path in WORKFLOW_ROUTES
) | frozenset({("GET", "/manufacturing"), ("GET", "/manufacturing/")})
