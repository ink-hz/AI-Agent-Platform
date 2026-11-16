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
        ("GET", "/assets/{asset_name}"),
        ("GET", "/handbook"),
    }
)
MANUFACTURING_ROUTES |= NATIVE_ACCESS_ROUTES
MANUFACTURING_AUTH_ROUTES = frozenset(
    (method, MANUFACTURING_PREFIX + path) for method, path in MANUFACTURING_ROUTES
)

SAP_MATERIAL_ROUTES = frozenset({
    ("GET", "/material-control/sap/status"),
    ("GET", "/material-control/sap/production-order"),
    ("POST", "/material-control/sap/production-order"),
    ("GET", "/material-control/sap/stock-requirements"),
    ("POST", "/material-control/sap/stock-requirements"),
})
MES_SOURCE_ROUTES = frozenset({
    ("GET", "/material-control/mes-source/status"),
    ("GET", "/material-control/mes-source/orders"),
    ("POST", "/material-control/mes-source/orders"),
    ("GET", "/material-control/mes-source/order-materials"),
    ("POST", "/material-control/mes-source/order-materials"),
    ("GET", "/material-control/mes-source/priority"),
    ("POST", "/material-control/mes-source/priority"),
    ("GET", "/material-control/mes-source/iqc"),
    ("POST", "/material-control/mes-source/iqc"),
    ("GET", "/material-control/mes-source/material-iqc"),
    ("POST", "/material-control/mes-source/material-iqc"),
    ("GET", "/material-control/mes-source/order-reports"),
    ("POST", "/material-control/mes-source/order-reports"),
})
EXPEDITE_ROUTES = frozenset({
    ("GET", "/expedites"),
    ("POST", "/expedites"),
    ("GET", "/expedites/{expedite_id}"),
    ("POST", "/expedites/{expedite_id}/notes"),
    ("POST", "/expedites/{expedite_id}/close"),
})
HANDOFF_ROUTES = frozenset({
    ("GET", "/handoffs"),
    ("POST", "/handoffs"),
    ("GET", "/handoffs/{handoff_id}"),
    ("POST", "/handoffs/{handoff_id}/receive"),
    ("POST", "/handoffs/{handoff_id}/resolve"),
})
WORKFLOW_ROUTES = MES_SOURCE_ROUTES | SAP_MATERIAL_ROUTES | EXPEDITE_ROUTES | HANDOFF_ROUTES | frozenset(
    {
        ("GET", "/access"),
        ("GET", "/assets/{asset_name}"),
        ("GET", "/handbook"),
        ("GET", "/me"),
        ("GET", "/members"),
        ("GET", "/members/directory"),
        ("PUT", "/members/{user_id}"),
        ("GET", "/todos"),
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
