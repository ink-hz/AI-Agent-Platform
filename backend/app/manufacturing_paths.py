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
    ("GET", "/material-control/sap/mrp-list"),
    ("POST", "/material-control/sap/mrp-list"),
    ("GET", "/material-control/sap/material-detail"),
    ("POST", "/material-control/sap/material-detail"),
    ("GET", "/material-control/sap/order-status"),
    ("POST", "/material-control/sap/order-status"),
    ("GET", "/material-control/sap/order-confirmations"),
    ("POST", "/material-control/sap/order-confirmations"),
    ("GET", "/material-control/sap/confirmation-detail"),
    ("POST", "/material-control/sap/confirmation-detail"),
    ("GET", "/material-control/sap/goods-movement"),
    ("POST", "/material-control/sap/goods-movement"),
    ("GET", "/material-control/sap/purchase-orders"),
    ("POST", "/material-control/sap/purchase-orders"),
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
    ("GET", "/material-control/mes-source/order-operations"),
    ("POST", "/material-control/mes-source/order-operations"),
    ("GET", "/material-control/mes-source/material-demand"),
    ("POST", "/material-control/mes-source/material-demand"),
    ("GET", "/material-control/mes-source/order-priority"),
    ("POST", "/material-control/mes-source/order-priority"),
    ("GET", "/material-control/mes-source/warehouses"),
    ("POST", "/material-control/mes-source/warehouses"),
    ("GET", "/material-control/mes-source/inventory"),
    ("POST", "/material-control/mes-source/inventory"),
    ("GET", "/material-control/mes-source/order-header"),
    ("POST", "/material-control/mes-source/order-header"),
    ("GET", "/material-control/mes-source/material-receipts"),
    ("POST", "/material-control/mes-source/material-receipts"),
    ("GET", "/material-control/mes-source/batch-receipts"),
    ("POST", "/material-control/mes-source/batch-receipts"),
    ("GET", "/material-control/mes-source/order-requisitions"),
    ("POST", "/material-control/mes-source/order-requisitions"),
    ("GET", "/material-control/mes-source/requisition-lines"),
    ("POST", "/material-control/mes-source/requisition-lines"),
    ("GET", "/material-control/mes-source/order-stock"),
    ("POST", "/material-control/mes-source/order-stock"),
    ("GET", "/material-control/mes-source/batch-stock"),
    ("POST", "/material-control/mes-source/batch-stock"),
    ("GET", "/material-control/mes-source/sn"),
    ("POST", "/material-control/mes-source/sn"),
    ("GET", "/material-control/mes-source/sn-route"),
    ("POST", "/material-control/mes-source/sn-route"),
    ("GET", "/material-control/mes-source/sn-station"),
    ("POST", "/material-control/mes-source/sn-station"),
    ("GET", "/material-control/mes-source/sn-components"),
    ("POST", "/material-control/mes-source/sn-components"),
    ("GET", "/material-control/mes-source/batch-consumers"),
    ("POST", "/material-control/mes-source/batch-consumers"),
    ("GET", "/material-control/mes-source/sn-defects"),
    ("POST", "/material-control/mes-source/sn-defects"),
    ("GET", "/material-control/mes-source/sn-repairs"),
    ("POST", "/material-control/mes-source/sn-repairs"),
    ("GET", "/material-control/mes-source/sn-packing"),
    ("POST", "/material-control/mes-source/sn-packing"),
    ("GET", "/material-control/mes-source/packing-sns"),
    ("POST", "/material-control/mes-source/packing-sns"),
    ("GET", "/material-control/mes-source/sn-markings"),
    ("POST", "/material-control/mes-source/sn-markings"),
    ("GET", "/material-control/mes-source/markings"),
    ("POST", "/material-control/mes-source/markings"),
    ("GET", "/material-control/mes-source/marking-sns"),
    ("POST", "/material-control/mes-source/marking-sns"),
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
ARRIVAL_ROUTES = frozenset({
    ("GET", "/arrivals"),
    ("POST", "/arrivals"),
    ("GET", "/arrivals/{arrival_id}"),
    ("POST", "/arrivals/{arrival_id}/notes"),
    ("POST", "/arrivals/{arrival_id}/assign"),
    ("POST", "/arrivals/{arrival_id}/convert"),
    ("POST", "/arrivals/{arrival_id}/close"),
})
SUPPLY_PLAN_ROUTES = frozenset({
    ("GET", "/supply-plans"),
    ("POST", "/supply-plans"),
    ("GET", "/supply-plans/{plan_id}"),
    ("POST", "/supply-plans/{plan_id}/receipts"),
    ("POST", "/supply-plans/{plan_id}/close"),
})
WORKBENCH_ROUTES = frozenset({
    ("GET", "/watches"),
    ("POST", "/watches"),
    ("POST", "/watches/remove"),
    ("GET", "/workbench"),
})
# r23 问题反馈: open to every authenticated user; the manufacturing release limits
# each report to its submitter, assignee and administrators.
FEEDBACK_ROUTES = frozenset({
    ("GET", "/feedback"),
    ("POST", "/feedback"),
    ("GET", "/feedback/{feedback_id}"),
    ("POST", "/feedback/{feedback_id}/comments"),
    ("POST", "/feedback/{feedback_id}/assign"),
    ("POST", "/feedback/{feedback_id}/status"),
    ("POST", "/feedback/{feedback_id}/photos"),
    ("GET", "/feedback/{feedback_id}/photos/{photo_id}"),
})
# r25 设置 → 产品族映射: one administrator write (OEM / 自研 / 未分类 rules, platform-local);
# the editor and the reconciliation panel read through GET /workbench?view=... .
PRODUCT_TAG_ROUTES = frozenset({
    ("POST", "/product-tag-rules"),
})
WORKFLOW_ROUTES = (
    MES_SOURCE_ROUTES | SAP_MATERIAL_ROUTES | EXPEDITE_ROUTES | HANDOFF_ROUTES
    | ARRIVAL_ROUTES | SUPPLY_PLAN_ROUTES | WORKBENCH_ROUTES | FEEDBACK_ROUTES
    | PRODUCT_TAG_ROUTES
) | frozenset(
    {
        ("GET", "/access"),
        ("GET", "/assets/{asset_name}"),
        ("GET", "/handbook"),
        ("GET", "/me"),
        ("GET", "/members"),
        ("GET", "/members/directory"),
        ("GET", "/members/search"),
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
