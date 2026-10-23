from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Callable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .access_history import AccessHistoryForbidden, AccessHistoryInvalid, AccessHistoryUnavailable
from .dsn import validate_control_dsn
from .models import AuthContext

LAUNCH_DATE = date(2026, 9, 14)


class OperationsInvalid(ValueError):
    pass


@dataclass(frozen=True)
class OperationsQuery:
    date_from: date
    date_to: date
    grain: str = 'day'
    metric: str = 'page_view'
    scope: str = 'employee'
    department: str | None = None
    module: str | None = None

    def __post_init__(self):
        if (self.date_from < LAUNCH_DATE or self.date_to < self.date_from
            or (self.date_to - self.date_from).days > 3660
            or self.grain not in {'day', 'month'}
            or self.metric not in {'page_view', 'entry_click'}
            or self.scope not in {'employee', 'admin', 'all'}
            or self.department is not None and not 1 <= len(self.department) <= 160
            or self.module is not None and not 1 <= len(self.module) <= 96):
            raise OperationsInvalid('operations filters invalid')


def trusted_department(context: AuthContext, resolver: Callable | None) -> str:
    if resolver is None:
        raise AccessHistoryUnavailable('department snapshot unavailable')
    try:
        result = resolver(context)
    except Exception:
        raise AccessHistoryUnavailable('department snapshot unavailable') from None
    if isinstance(result, dict):
        primary = result.get('primary_department')
        departments = result.get('departments')
        result = primary or (departments[0] if isinstance(departments, (list, tuple)) and len(departments) == 1 else None)
    if result is None or result == '':
        return '未归属部门'
    if not isinstance(result, str) or len(result.strip()) > 160:
        raise AccessHistoryUnavailable('department snapshot unavailable')
    return result.strip() or '未归属部门'


class OfficeOperationsRepository:
    def __init__(self, database_url: str, *, department_resolver=None, connect=psycopg.connect):
        validate_control_dsn(database_url, purpose='app')
        self.database_url = database_url
        self.connect = connect
        self.department_resolver = department_resolver

    def _connection(self):
        return self.connect(self.database_url, connect_timeout=3,
                            options='-c statement_timeout=10000', row_factory=dict_row)

    def record_click(self, event_id: UUID, context: AuthContext, module: str):
        department = trusted_department(context, self.department_resolver)
        try:
            with self._connection() as connection:
                row = connection.execute('select * from platform_control.append_office_click_v123(%s,%s,%s,%s,%s)',
                    (event_id, context.internal_user_id, context.session_id, module, department)).fetchone()
            if row is None or row['outcome'] not in {'inserted', 'duplicate', 'rate_limited'}:
                raise AccessHistoryUnavailable('operations unavailable')
            return row['outcome']
        except psycopg.errors.InsufficientPrivilege:
            raise AccessHistoryForbidden('click rejected') from None
        except (psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation):
            raise AccessHistoryInvalid('click invalid') from None
        except psycopg.Error:
            raise AccessHistoryUnavailable('operations unavailable') from None

    def _query(self, query: OperationsQuery):
        try:
            with self._connection() as connection:
                row = connection.execute('select platform_control.read_office_operations_v123(%s,%s,%s,%s,%s,%s,%s) report',
                    tuple(asdict(query).values())).fetchone()
            if row is None or not isinstance(row['report'], dict):
                raise AccessHistoryUnavailable('operations unavailable')
            return row['report']
        except psycopg.Error:
            raise AccessHistoryUnavailable('operations unavailable') from None

    def report(self, query: OperationsQuery):
        result = self._query(query)
        result.pop('export_rows', None)
        result['generated_at'] = datetime.now(UTC).isoformat()
        result['filters'] = asdict(query) | {'date_from': query.date_from.isoformat(), 'date_to': query.date_to.isoformat()}
        return result

    def export(self, query: OperationsQuery) -> str:
        data = self._query(query)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['周期', '部门', '服务板块', '统计指标', '次数', '去重人数', '部门口径'])
        for row in data['export_rows']:
            values = [row['period'], row['department'], row['label'],
                      '页面访问' if query.metric == 'page_view' else '服务入口点击',
                      row['events'], row['users'], row['department_basis']]
            writer.writerow([("'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value)
                             if isinstance(value, str) else value for value in values])
        return '\ufeff' + output.getvalue()
