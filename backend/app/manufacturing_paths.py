"""Exact host authorization contract shared by enabled and unavailable routers."""
MANUFACTURING_PREFIX = '/api/v1/manufacturing'
MANUFACTURING_ROUTES = frozenset({
    ('GET', '/access'), ('GET', '/health'), ('GET', '/catalog'),
    ('GET', '/assets/{asset_name}'), ('GET', '/lines/{line_id}'),
    ('GET', '/lines/{line_id}/stages/{stage_id}'),
    ('PUT', '/lines/{line_id}/stages/{stage_id}'),
    ('POST', '/lines/{line_id}/stages/{stage_id}/tasks'),
    ('PATCH', '/lines/{line_id}/stages/{stage_id}/tasks/{task_id}'),
    ('GET', '/dashboard'), ('GET', '/projects'), ('POST', '/projects'),
    ('GET', '/projects/{project_id}'), ('PATCH', '/projects/{project_id}'),
    ('PUT', '/projects/{project_id}/stages/{stage_id}'),
    ('POST', '/projects/{project_id}/tasks'),
    ('PATCH', '/projects/{project_id}/tasks/{task_id}'),
    ('GET', '/project-tasks'), ('GET', '/handbook'),
})
MANUFACTURING_AUTH_ROUTES = frozenset(
    (method, MANUFACTURING_PREFIX + path) for method, path in MANUFACTURING_ROUTES
) | frozenset({('GET', '/manufacturing'), ('GET', '/manufacturing/')})
