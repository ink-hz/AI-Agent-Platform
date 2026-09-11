"""Disposable local PostgreSQL; no existing service or business data is used."""
import base64
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import psycopg
from app.control_plane.migrate import migrate_control_database
from psycopg import sql


@dataclass
class HrTestDatabase:
    dsn: str = field(repr=False)
    admin_dsn: str = field(repr=False)
    migrator_dsn: str = field(repr=False)
    def connection(self):
        return psycopg.connect(self.dsn)
    def admin_connection(self):
        return psycopg.connect(self.admin_dsn)

@contextmanager
def hr_agent_database(*, migrate_hr=True, cutover_phase="cloud"):
    if cutover_phase not in {"legacy", "cloud"}:
        raise ValueError("test cutover phase invalid")
    binary=Path(os.environ.get('HR_TEST_POSTGRES_BIN','/opt/homebrew/opt/postgresql@17/bin'))
    if not (binary/'initdb').is_file():
        raise RuntimeError('local PostgreSQL binaries required for HR tests')
    with tempfile.TemporaryDirectory(prefix='hr-agent-a1-',dir='/tmp') as temp:
        root=Path(temp)
        data=root/'pg'
        sock=root/'socket'; sock.mkdir()
        subprocess.run([str(binary/'initdb'),'-D',str(data),'-U','postgres','-A','trust','--no-locale','-E','UTF8'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),'-o',f"-k {sock} -h '' -p 5432",'-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            base=f'host={sock} port=5432'
            with psycopg.connect(base+' dbname=postgres user=postgres',autocommit=True) as conn:
                roles=('platform_control_owner','platform_control_migrator','platform_control_app','platform_control_maintenance','platform_directory_worker','platform_stream_ingest','platform_audit_append','platform_brain_worker')
                for role in roles:
                    conn.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)))
                    conn.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role+'_preview')))
                conn.execute('GRANT platform_control_owner TO platform_control_migrator')
                conn.execute('CREATE DATABASE agent_platform_control OWNER platform_control_owner')
            dbbase=base+' dbname=agent_platform_control'
            db=HrTestDatabase(dbbase+' user=platform_control_app', dbbase+' user=postgres', dbbase+' user=platform_control_migrator')
            migrations=Path(__file__).parents[1]/'control_migrations'
            migrate_control_database(db.migrator_dsn,migrations,owner_role='platform_control_owner')
            if migrate_hr:
                migrate_control_database(db.migrator_dsn,migrations/'hr_web',owner_role='platform_control_owner')
                migrate_control_database(db.migrator_dsn,migrations/'hr_agent',owner_role='platform_control_owner')
                with db.admin_connection() as connection:
                    connection.execute(
                        "select platform_control.initialize_hr_execution_cutover_v102(%s)",
                        (uuid4(),),
                    )
                    if cutover_phase == "cloud":
                        connection.execute(
                            "select platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)",
                            (uuid4(),),
                        )
                        connection.execute(
                            "select platform_control.transition_hr_execution_cutover_v102('cloud',%s)",
                            (uuid4(),),
                        )
            yield db
        finally:
            subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-m','immediate','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)

def make_hr_settings(tmp_path, **overrides):
    from app.hr_agent.config import load_hr_agent_settings
    root=Path(tmp_path); root.mkdir(exist_ok=True,parents=True)
    work=root/'work'; work.mkdir(mode=0o700,exist_ok=True)
    knowledge=root/'knowledge'; knowledge.mkdir(exist_ok=True)
    credential=root/'credential'; credential.write_text('fake-local-secret'); credential.chmod(0o600)
    documents={
        'CONTENT_KEYRING_FILE':{'purpose':'platform-content-encryption','active_version':1,'keys':{'1':base64.b64encode(b'x'*32).decode()}},
        'PROVIDER_PROFILE_FILE':{'id':'local-fake','revision':'local-v1','protocol':'openai_chat_sse','endpoint':'http://127.0.0.1:1/v1/chat/completions','model':'fake','credential_file':str(credential),'tokenizer':'conservative_utf8','context_window_tokens':100000},
        'BUDGET_PROFILE_FILE':{'id':'test','service_limits':{'model_calls':64,'total_tokens':1200000,'active_seconds':1800},'limits':{'model_calls':32,'total_tokens':600000,'active_seconds':900},'reserve':{'model_calls':2,'total_tokens':16000,'active_seconds':30},'max_output_tokens':4096,'input_target_tokens':8000,'input_trigger_tokens':12000,'work_retention_seconds':3600},
        'DIAGNOSTIC_PROFILE_FILE':{'enabled':False},
    }
    env={'PLATFORM_HR_AGENT_ENABLED':'1','PLATFORM_EXECUTION_RELAY_ENABLED':'0','PLATFORM_HR_AGENT_WORK_DIR':str(work),'PLATFORM_HR_AGENT_KNOWLEDGE_DIR':str(knowledge)}
    for key,doc in documents.items():
        path=root/(key.lower()+'.json'); path.write_text(json.dumps(doc)); path.chmod(0o600)
        env['PLATFORM_HR_AGENT_'+key]=str(path)
    env.update(overrides)
    return load_hr_agent_settings(env)
