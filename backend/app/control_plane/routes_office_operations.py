from __future__ import annotations

import hmac
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .access_history import AccessHistoryUnavailable
from .office_operations import LAUNCH_DATE, OperationsInvalid, OperationsQuery
from .office_recipients import _loopback, _NoStoreRoute


def build_office_operations_router(service, *, bearer_secret: str) -> APIRouter:
    if len(bearer_secret.encode()) < 32:
        raise ValueError('office bearer must contain at least 32 bytes')
    expected = f'Bearer {bearer_secret}'

    def authorize(request: Request):
        if not _loopback(request) or not hmac.compare_digest(request.headers.get('authorization', ''), expected):
            raise HTTPException(404, 'not found', headers={'Cache-Control': 'no-store'})

    def filters(date_from: date = LAUNCH_DATE, date_to: date | None = None, grain: str = 'day', metric: str = 'page_view',
                scope: str = 'employee', department: str | None = None, module: str | None = None):
        try:
            return OperationsQuery(date_from, date_to or datetime.now(ZoneInfo('Asia/Shanghai')).date(), grain, metric, scope, department, module)
        except OperationsInvalid:
            raise HTTPException(400, 'operations filters invalid') from None

    router = APIRouter(route_class=_NoStoreRoute, dependencies=[Depends(authorize)], tags=['office-operations'])

    @router.get('/api/v1/internal/office/operations')
    def report(query: OperationsQuery = Depends(filters)):
        try:
            return service.report(query)
        except AccessHistoryUnavailable:
            raise HTTPException(503, 'operations unavailable') from None

    @router.get('/api/v1/internal/office/operations/export')
    def export(query: OperationsQuery = Depends(filters)):
        try:
            return Response(service.export(query), media_type='text/csv; charset=utf-8')
        except AccessHistoryUnavailable:
            raise HTTPException(503, 'operations unavailable') from None

    return router
