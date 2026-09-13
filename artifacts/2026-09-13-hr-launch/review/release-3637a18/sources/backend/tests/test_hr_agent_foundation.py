import pytest


def test_disabled_relay_can_load_hr_codec(tmp_path):
    from hr_agent_support import make_hr_settings
    settings = make_hr_settings(tmp_path)
    codec = settings.create_codec()
    sealed = codec.seal_json('hr-agent:test:id:1', {'fake':'text'})
    assert codec.unseal_json('hr-agent:test:id:1',sealed) == {'fake':'text'}
    assert 'secret' not in repr(settings)

def test_database_schema_has_contract_tables_and_restricted_app():
    from hr_agent_support import hr_agent_database
    with hr_agent_database() as db:
        with db.connection() as conn:
            assert conn.execute("select count(*) from information_schema.tables where table_schema='platform_hr_agent'").fetchone()[0] == 25
            with pytest.raises(Exception):
                conn.execute('create table platform_hr_agent.forbidden(id int)')


def test_missing_schema_does_not_create_tables():
    from app.hr_agent.config import check_schema_ready
    from hr_agent_support import hr_agent_database
    with hr_agent_database(migrate_hr=False) as db:
        assert not check_schema_ready(db)
        with db.admin_connection() as conn:
            assert conn.execute("select to_regnamespace('platform_hr_agent')").fetchone()[0] is None

def test_provider_profile_rejects_fallback_and_unknown_config(tmp_path):
    import json

    from hr_agent_support import make_hr_settings
    settings=make_hr_settings(tmp_path)
    profile=dict(settings.provider_profile, fallback='http://other.invalid', unknown='sensitive')
    path=tmp_path/'provider_profile_file.json'; path.write_text(json.dumps(profile))
    with pytest.raises(ValueError,match='^HR configuration invalid$'):
        make_hr_settings(tmp_path/'again', PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(path))

def test_scope_uses_server_eligibility_and_denies_unknown_objects():
    from types import SimpleNamespace
    from uuid import uuid4

    from app.control_plane.models import AuthContext, Role
    from app.hr_agent.access import HrAccess
    from app.hr_agent.types import HrAgentProblem
    actor=uuid4()
    class Eligibility:
        def decide_for_user_id(self, user, agent):
            assert user==actor and agent=='hr-bot'
            return SimpleNamespace(allowed=True)
    access=HrAccess(Eligibility())
    auth=AuthContext(actor,Role.MEMBER,uuid4(),False)
    assert access.authorize_user(auth,writable=True)==actor
    with pytest.raises(HrAgentProblem): access.authorize_user({'owner_id':str(actor)},writable=True)
    with pytest.raises(HrAgentProblem): access.authorize_scope(actor,({'kind':'position','id':str(uuid4())},),(),work_id=None)

def test_schema_ready_is_true_after_explicit_migration():
    from app.hr_agent.config import check_schema_ready
    from hr_agent_support import hr_agent_database
    with hr_agent_database() as db:
        assert check_schema_ready(db)

def test_database_constraints_reject_cross_owner_and_mutable_history():
    from uuid import uuid4

    import psycopg
    from hr_agent_support import hr_agent_database
    with hr_agent_database() as db:
        owner,other,thread,work=uuid4(),uuid4(),uuid4(),uuid4()
        with db.connection() as c:
            c.execute('INSERT INTO platform_hr_agent.threads(owner_id,thread_id,sealed_title,sealed_title_key_version) VALUES(%s,%s,%s,1)',(owner,thread,b'fake-encrypted-fixture'))
        with db.connection() as c:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                c.execute("INSERT INTO platform_hr_agent.works(owner_id,work_id,thread_id,state,sealed_budget,sealed_budget_key_version,sealed_checkpoint,sealed_checkpoint_key_version) VALUES(%s,%s,%s,'queued',%s,1,%s,1)",(other,work,thread,b'fake',b'fake'))
        with db.connection() as c:
            assert not c.execute("SELECT has_table_privilege(current_user,'platform_hr_agent.inputs','UPDATE')").fetchone()[0]
            assert not c.execute("SELECT has_table_privilege(current_user,'platform_hr_agent.results','DELETE')").fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute('INSERT INTO platform_hr_agent.threads(owner_id,thread_id,sealed_title,sealed_title_key_version) VALUES(%s,%s,%s,0)',(owner,uuid4(),b'fake'))

@pytest.mark.parametrize("version", [96, 97, 98, 99, 100])
def test_readiness_rejects_different_migration_checksum(version):
    from app.hr_agent.config import check_schema_ready
    from hr_agent_support import hr_agent_database
    with hr_agent_database() as db:
        with db.admin_connection() as c:
            c.execute("UPDATE platform_control.schema_migrations SET sha256=%s WHERE version=%s",('0'*64,version))
        assert not check_schema_ready(db)

@pytest.mark.parametrize('revocation', ['INSERT ON platform_hr_agent.works','UPDATE ON platform_hr_agent.works','SELECT ON platform_hr_agent.inputs','INSERT ON platform_hr_agent.entries','USAGE ON SCHEMA platform_hr_agent'])
def test_readiness_rejects_missing_application_write_grant(revocation):
    from app.hr_agent.config import check_schema_ready
    from hr_agent_support import hr_agent_database
    with hr_agent_database() as db:
        with db.admin_connection() as c:
            c.execute('REVOKE '+revocation+' FROM platform_control_app')
        assert not check_schema_ready(db)

@pytest.mark.parametrize('document,field,value', [
    ('budget','model_calls',32.5), ('budget','model_calls',True),
    ('budget','total_tokens',600000.0), ('budget','active_seconds',900.5),
    ('budget','max_output_tokens',4096.0), ('budget','input_target_tokens','8000'),
    ('budget','input_trigger_tokens',12000.5), ('budget','work_retention_seconds',True),
    ('provider','context_window_tokens',32768.5), ('provider','timeout_seconds',1.5),
])
def test_config_requires_integer_numeric_units(tmp_path,document,field,value):
    import json

    from hr_agent_support import make_hr_settings
    settings=make_hr_settings(tmp_path)
    profile=dict(settings.budget_profile if document=='budget' else settings.provider_profile)
    if field in ('model_calls','total_tokens','active_seconds'):
        profile['limits']=dict(profile['limits'],**{field:value})
    else: profile[field]=value
    path=tmp_path/'invalid.json'; path.write_text(json.dumps(profile))
    key='PLATFORM_HR_AGENT_'+('BUDGET' if document=='budget' else 'PROVIDER')+'_PROFILE_FILE'
    with pytest.raises(ValueError,match='^HR configuration invalid$'):
        make_hr_settings(tmp_path/'again',**{key:str(path)})

@pytest.mark.parametrize('key,value',[('LEASE_SECONDS','60.5'),('HEARTBEAT_SECONDS','1e1'),('LEASE_SECONDS',True)])
def test_config_requires_integer_environment_time_units(tmp_path,key,value):
    from hr_agent_support import make_hr_settings
    with pytest.raises(ValueError,match='^HR configuration invalid$'):
        make_hr_settings(tmp_path,**{'PLATFORM_HR_AGENT_'+key:value})


@pytest.mark.parametrize('endpoint',['http://127.0.0.1:PRIVATE_PORT_SENTINEL/v1','http://127.0.0.1:65536/v1','http://127.0.0.1:0/v1'])
def test_config_rejects_invalid_endpoint_port_without_secret_echo(tmp_path,endpoint):
    import json

    from hr_agent_support import make_hr_settings
    settings=make_hr_settings(tmp_path)
    path=tmp_path/'invalid-provider.json'
    path.write_text(json.dumps({**settings.provider_profile,'endpoint':endpoint}))
    with pytest.raises(ValueError,match='^HR configuration invalid$'):
        make_hr_settings(tmp_path/'again',PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(path))


def test_hr_model_environment_is_effective_and_changes_frozen_configuration(tmp_path):
    from hr_agent_support import make_hr_settings
    base=make_hr_settings(tmp_path)
    selected=make_hr_settings(tmp_path, PLATFORM_HR_AGENT_MODEL='claude-opus-5')
    assert selected.provider_profile['model']=='claude-opus-5'
    assert selected.configuration_revision!=base.configuration_revision


@pytest.mark.parametrize('value',[' ', '\nclaude-opus-5', 'bad\x00model'])
def test_hr_model_environment_rejects_invalid_value(tmp_path,value):
    from hr_agent_support import make_hr_settings
    with pytest.raises(ValueError,match='^HR configuration invalid$'):
        make_hr_settings(tmp_path,PLATFORM_HR_AGENT_MODEL=value)


def test_readiness_rejects_missing_erasure_migration_receipt():
    from hr_agent_support import hr_agent_database
    from app.hr_agent.config import check_schema_ready
    with hr_agent_database() as db:
        with db.admin_connection() as c:
            c.execute("DELETE FROM platform_control.schema_migrations WHERE version=100")
        assert not check_schema_ready(db)


@pytest.mark.parametrize("table,column", [
    ("uploads", "attachment_id"), ("uploads", "write_attempt_id"),
    ("upload_write_attempts", "attachment_id"), ("upload_write_attempts", "attempt_id"),
    ("upload_write_attempts", "object_ref_ciphertext"), ("upload_write_attempts", "object_ref_key_version"),
])
def test_readiness_rejects_missing_erasure_maintenance_column(table, column):
    from psycopg import sql
    from hr_agent_support import hr_agent_database
    from app.hr_agent.config import check_schema_ready
    with hr_agent_database() as db:
        with db.admin_connection() as c:
            c.execute(sql.SQL("REVOKE SELECT ({}) ON platform_attachments.{} FROM platform_control_maintenance").format(sql.Identifier(column),sql.Identifier(table)))
        assert not check_schema_ready(db)


@pytest.mark.parametrize('input_capacity', [28672, 88191, 88192])
def test_config_reserves_full_unicode_read_capacity_before_start(tmp_path, input_capacity):
    import json
    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / 'seed')
    path = tmp_path / 'provider.json'
    path.write_text(json.dumps({
        **settings.provider_profile,
        'context_window_tokens': input_capacity + settings.budget_profile['max_output_tokens'],
    }))
    path.chmod(0o600)
    if input_capacity < 88192:
        with pytest.raises(ValueError, match='^HR configuration invalid$'):
            make_hr_settings(tmp_path / 'load', PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(path))
    else:
        loaded = make_hr_settings(tmp_path / 'load', PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(path))
        assert loaded.provider_profile['context_window_tokens'] == input_capacity + 4096

@pytest.mark.parametrize("version", [102, 103, 104, 105])
@pytest.mark.parametrize("receipt", [None, "0" * 64])
def test_schema_readiness_requires_exact_cutover_migration(version, receipt):
    from app.hr_agent.config import check_schema_ready
    from hr_agent_support import hr_agent_database
    with hr_agent_database() as db:
        with db.admin_connection() as c:
            if receipt is None:
                c.execute("DELETE FROM platform_control.schema_migrations WHERE version=%s", (version,))
            else:
                c.execute("UPDATE platform_control.schema_migrations SET sha256=%s WHERE version=%s", (receipt, version))
        assert not check_schema_ready(db)
