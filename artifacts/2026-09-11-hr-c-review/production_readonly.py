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
    "scope": "production attachment worker; SELECT aggregates only",
    "erasure_source_sha256": hashlib.sha256(source).hexdigest(),
    "legacy_claim_expansion": b"select (platform_attachments.claim_attachment_erasure_job_v64(%s)).*" in source.lower(),
    "fixed_claim_from": b"select * from platform_attachments.claim_attachment_erasure_job_v64(%s)" in source.lower(),
    "claim_sql_lines": [line.strip() for line in source.decode().splitlines() if "claim_attachment_erasure_job_v64" in line],
}
dsn = Path(os.environ["PLATFORM_ATTACHMENT_MAINTENANCE_DATABASE_URL_FILE"]).read_text().strip()
queries = {
    "database_identity": "SELECT current_database() AS database,current_user AS role",
    "erasure_rls": "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='platform_attachments.erasure_jobs'::regclass",
    "erasure_policies": "SELECT policyname,roles,qual FROM pg_policies WHERE schemaname='platform_attachments' AND tablename='erasure_jobs'",
    "queue_states": "SELECT state,count(*) AS jobs,min(created_at) AS oldest,max(created_at) AS newest FROM platform_attachments.erasure_jobs GROUP BY state ORDER BY state",
    "queue_attempts": "SELECT state,count(*) AS jobs,min(attempt_count) AS min_attempts,max(attempt_count) AS max_attempts,min(claimed_at) AS oldest_claim FROM platform_attachments.erasure_jobs GROUP BY state ORDER BY state",
    "state_pairs": "SELECT j.state AS job_state,a.state AS attachment_state,count(*) AS pairs FROM platform_attachments.erasure_jobs j JOIN platform_attachments.attachments a USING(attachment_id) GROUP BY j.state,a.state ORDER BY j.state,a.state",
    "worker_permissions": "SELECT has_column_privilege(current_user,'platform_attachments.uploads','attachment_id','SELECT') AS uploads_attachment,has_column_privilege(current_user,'platform_attachments.uploads','write_attempt_id','SELECT') AS uploads_attempt,has_column_privilege(current_user,'platform_attachments.upload_write_attempts','attachment_id','SELECT') AS attempts_attachment,has_column_privilege(current_user,'platform_attachments.upload_write_attempts','attempt_id','SELECT') AS attempts_id,has_column_privilege(current_user,'platform_attachments.upload_write_attempts','object_ref_ciphertext','SELECT') AS attempts_ciphertext,has_column_privilege(current_user,'platform_attachments.upload_write_attempts','object_ref_key_version','SELECT') AS attempts_key_version",
    "migration_100": "SELECT version,sha256,applied_at FROM platform_control.schema_migrations WHERE version=100",
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
