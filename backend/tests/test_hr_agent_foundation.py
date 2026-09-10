from pathlib import Path
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
            assert conn.execute("select count(*) from information_schema.tables where table_schema='platform_hr_agent'").fetchone()[0] == 15
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
    from app.hr_agent.config import load_hr_agent_settings
    from hr_agent_support import make_hr_settings
    settings=make_hr_settings(tmp_path)
    profile=dict(settings.provider_profile, fallback='http://other.invalid', unknown='sensitive')
    path=tmp_path/'provider_profile_file.json'; path.write_text(json.dumps(profile))
    with pytest.raises(ValueError,match='^HR configuration invalid$'):
        make_hr_settings(tmp_path/'again', PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(path))

def test_scope_uses_server_eligibility_and_denies_unknown_objects():
    from types import SimpleNamespace
    from uuid import uuid4
    from app.hr_agent.access import HrAccess
    from app.hr_agent.types import HrAgentProblem
    from app.control_plane.models import AuthContext, Role
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

def test_readiness_rejects_different_migration_checksum():
    from app.hr_agent.config import check_schema_ready
    from hr_agent_support import hr_agent_database
    with hr_agent_database() as db:
        with db.admin_connection() as c:
            c.execute("UPDATE platform_control.schema_migrations SET sha256=%s WHERE version=96",('0'*64,))
        assert not check_schema_ready(db)
