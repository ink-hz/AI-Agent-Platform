import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import app.attachments.erasure as erasure

source = Path(erasure.__file__).read_bytes()
output = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "scope": "production attachment census independent of erasure queue; SELECT aggregates only",
    "erasure_source_sha256": hashlib.sha256(source).hexdigest(),
    "legacy_claim_expansion": b"select (platform_attachments.claim_attachment_erasure_job_v64(%s)).*" in source.lower(),
    "fixed_claim_from": b"select * from platform_attachments.claim_attachment_erasure_job_v64(%s)" in source.lower(),
    "claim_sql_lines": [line.strip() for line in source.decode().splitlines() if "claim_attachment_erasure_job_v64" in line],
}
dsn = Path(os.environ["PLATFORM_ATTACHMENT_MAINTENANCE_DATABASE_URL_FILE"]).read_text().strip()
queries = {
    "database_identity": "SELECT current_database() AS database,current_user AS role",
    "tables_rls": "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('platform_attachments.attachments'::regclass,'platform_attachments.erasure_jobs'::regclass) ORDER BY relname",
    "attachment_states": "SELECT state,deleted_at IS NOT NULL AS has_deleted_at,count(*) AS attachments,coalesce(sum(size_bytes),0) AS declared_bytes FROM platform_attachments.attachments GROUP BY state,deleted_at IS NOT NULL ORDER BY state,has_deleted_at",
    "attachment_totals": "SELECT count(*) AS attachments,count(*) FILTER (WHERE state='deleted') AS state_deleted,count(*) FILTER (WHERE deleted_at IS NOT NULL) AS timestamp_deleted,count(*) FILTER (WHERE state='deleted' AND deleted_at IS NULL OR state<>'deleted' AND deleted_at IS NOT NULL) AS inconsistent_markers,count(*) FILTER (WHERE retained_until<=now() AND state<>'deleted') AS retention_due FROM platform_attachments.attachments",
    "queue_states": "SELECT state,count(*) AS jobs,min(created_at) AS oldest,max(created_at) AS newest FROM platform_attachments.erasure_jobs GROUP BY state ORDER BY state",
    "uploads_unique_constraints": "SELECT pg_get_constraintdef(oid) AS constraint_definition FROM pg_constraint WHERE conrelid='platform_attachments.uploads'::regclass AND contype='u'",
}

with psycopg.connect(dsn, options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000") as conn:
    output["transaction_read_only"] = conn.execute("SHOW transaction_read_only").fetchone()[0]
    for name, query in queries.items():
        try:
            with conn.transaction():
                cursor = conn.execute(query)
                names = [column.name for column in cursor.description]
                output[name] = [dict(zip(names, row)) for row in cursor.fetchall()]
        except psycopg.Error as error:
            output[name] = {"unavailable_sqlstate": error.sqlstate}
    conn.rollback()
print(json.dumps(output, default=str, indent=2))
