"""Exact host authorization contract shared by enabled and unavailable routers."""
MANUFACTURING_PREFIX = '/api/v1/manufacturing'
MANUFACTURING_ROUTES = frozenset({
    ('GET', '/access'), ('GET', '/health'), ('GET', '/catalog'),
    ('GET', '/assets/{asset_name}'), ('GET', '/lines/{line_id}'),
    ('GET', '/lines/{line_id}/stages/{stage_id}'),
    ('PUT', '/lines/{line_id}/stages/{stage_id}'),
    ('POST', '/lines/{line_id}/stages/{stage_id}/tasks'),
    ('PATCH', '/lines/{line_id}/stages/{stage_id}/tasks/{task_id}'),
})
MANUFACTURING_AUTH_ROUTES = frozenset(
    (method, MANUFACTURING_PREFIX + path) for method, path in MANUFACTURING_ROUTES
) | frozenset({('GET', '/manufacturing'), ('GET', '/manufacturing/')})
