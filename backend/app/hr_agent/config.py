"""Fail-closed HR configuration, independent of execution-relay enablement."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import stat
import re
from urllib.parse import urlsplit
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from .types import canonical_json

MIGRATION_SHA256 = '02241fb7872b598c67ea78e84fe5301d2797683680c8f7bf79770b3ae6c8f817'

TABLES = ('threads','works','inputs','entries','model_attempts','operations','read_records','events','results','result_revisions','result_links','standards','standard_revisions','reference_edges','budget_extensions')

@dataclass(frozen=True)
class HrAgentSettings:
    enabled: bool = False
    provider_profile: dict = field(default_factory=dict, repr=False)
    budget_profile: dict = field(default_factory=dict)
    diagnostic_profile: dict = field(default_factory=dict, repr=False)
    content_keyring_file: Path | None = field(default=None, repr=False)
    knowledge_dir: Path | None = field(default=None, repr=False)
    work_dir: Path | None = field(default=None, repr=False)
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    configuration_revision: str = ''
    def create_codec(self) -> ContentCodec:
        if not self.enabled or self.content_keyring_file is None:
            raise ValueError('HR content configuration unavailable')
        return ContentCodec(IdentityKeyring.from_file(self.content_keyring_file, expected_purpose='platform-content-encryption', expected_key_length=32))

def load_hr_agent_settings(environment: Mapping[str,str]) -> HrAgentSettings:
    enabled = environment.get('PLATFORM_HR_AGENT_ENABLED','0')
    if enabled == '0':
        return HrAgentSettings()
    if enabled != '1':
        raise ValueError('HR configuration invalid')
    prefix='PLATFORM_HR_AGENT_'
    try:
        def path(key):
            p=Path(environment[prefix+key])
            if not p.is_absolute() or p.is_symlink(): raise ValueError()
            return p
        def document(key):
            value=json.loads(path(key).read_text())
            if not isinstance(value,dict): raise ValueError()
            return value
        def integer(value):
            if type(value) is not int:
                raise ValueError()
            return value
        def environment_integer(key, default):
            value=environment.get(prefix+key,default)
            if not isinstance(value,str) or re.fullmatch(r'[0-9]+',value) is None:
                raise ValueError()
            return int(value)
        provider=document('PROVIDER_PROFILE_FILE')
        budget=document('BUDGET_PROFILE_FILE')
        diagnostic=document('DIAGNOSTIC_PROFILE_FILE')
        for name in ('id','revision','endpoint','model','credential_file','tokenizer','context_window_tokens'):
            if not provider.get(name): raise ValueError()
        allowed={'id','revision','protocol','endpoint','model','credential_file','tokenizer','context_window_tokens','timeout_seconds'}
        if set(provider)-allowed: raise ValueError()
        if provider.get('protocol') not in {'openai_chat_sse','anthropic_messages_sse'}: raise ValueError()
        endpoint=urlsplit(provider['endpoint'])
        port=endpoint.port
        if port is not None and port==0: raise ValueError()
        if endpoint.scheme not in {'http','https'} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment: raise ValueError()
        credential=Path(provider['credential_file'])
        if not credential.is_absolute() or credential.is_symlink() or not credential.is_file() or stat.S_IMODE(credential.stat().st_mode)!=0o600: raise ValueError()
        if not 0<integer(provider.get('timeout_seconds',120))<=120: raise ValueError()
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',provider[k]) for k in ('id','revision')): raise ValueError()
        window=integer(provider['context_window_tokens'])
        output=integer(budget['max_output_tokens'])
        target=integer(budget['input_target_tokens'])
        trigger=integer(budget['input_trigger_tokens'])
        if not 0<target<trigger<window-output: raise ValueError()
        for name in ('model_calls','total_tokens','active_seconds'):
            if integer(budget['limits'][name])<=0: raise ValueError()
            if not 0<=integer(budget['reserve'][name])<budget['limits'][name]: raise ValueError()
        if output<=0 or type(diagnostic.get('enabled')) is not bool: raise ValueError()
        if diagnostic['enabled'] and (not diagnostic.get('roles') or integer(diagnostic.get('retention_seconds',0))<=0 or not diagnostic.get('directory')): raise ValueError()
        lease=environment_integer('LEASE_SECONDS','60')
        heartbeat=environment_integer('HEARTBEAT_SECONDS','15')
        if not 0<heartbeat<lease/2: raise ValueError()
        knowledge=path('KNOWLEDGE_DIR')
        work=path('WORK_DIR')
        if not knowledge.is_dir() or not work.is_dir() or stat.S_IMODE(work.stat().st_mode)!=0o700: raise ValueError()
        if integer(budget.get('work_retention_seconds',0))<=0: raise ValueError()
        settings=HrAgentSettings(True,provider,budget,diagnostic,path('CONTENT_KEYRING_FILE'),knowledge,work,lease,heartbeat,hashlib.sha256(canonical_json({'provider':provider,'budget':budget,'diagnostic':diagnostic}).encode()).hexdigest())
        settings.create_codec()
        return settings
    except Exception:
        raise ValueError('HR configuration invalid') from None

def check_schema_ready(connection_factory) -> bool:
    """Read-only readiness. No startup path owns DDL or runs a migration."""
    try:
        connect=getattr(connection_factory,'connection',connection_factory)
        with connect() as connection:
            row=connection.execute("select has_schema_privilege(current_user, 'platform_hr_agent', 'USAGE')").fetchone()
            if row is None or not row[0]: return False
            immutable={'inputs','entries','result_revisions','standard_revisions','reference_edges','budget_extensions','read_records','events'}
            for name in TABLES:
                row=connection.execute('select to_regclass(%s)',('platform_hr_agent.'+name,)).fetchone()
                if row is None or row[0] is None: return False
                privileges=('SELECT','INSERT') if name in immutable else ('SELECT','INSERT','UPDATE')
                for privilege in privileges:
                    row=connection.execute('select has_table_privilege(current_user,%s,%s)',('platform_hr_agent.'+name,privilege)).fetchone()
                    if row is None or not row[0]: return False
            row=connection.execute('select sha256 from platform_control.schema_migrations where version=96').fetchone()
            return row is not None and row[0] == MIGRATION_SHA256
    except Exception:
        return False
