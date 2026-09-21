from uuid import UUID, uuid4
import importlib
import psycopg
import pytest
from app.control_plane.models import AuthContext, Role
from test_ai_engineering_api import client_for, assert_private

pytest_plugins = ('test_control_plane_migration',)
PREFIX = '/api/v1/ai-engineering/organization'
SIGNATURE = 'platform_control.read_organization_directory_v109(uuid,uuid,uuid,integer)'

@pytest.fixture
def directory(control_database):
    env = control_database['environments']['production']
    generation = uuid4()
    ids = [UUID(int=i) for i in range(1, 9)]
    root, dept, child, peer, *members = ids
    with psycopg.connect(env['admin'], autocommit=True) as conn:
        conn.execute("insert into platform_control.directory_generations (generation_id,status,completed_at) values (%s,'complete',now())", (generation,))
        for i, (key, parent, name) in enumerate([(root,None,'Organization'), (dept,root,'Team'), (child,dept,'Team'), (peer,root,'Team')]):
            conn.execute('insert into platform_control.directory_departments (generation_id,department_key,parent_department_key,lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name) values (%s,%s,%s,%s,1,%s,1,%s)', (generation,key,parent,bytes([i])*32,b'private',name))
        for ancestor, descendant, depth in [(root,root,0),(root,dept,1),(root,child,2),(root,peer,1),(dept,dept,0),(dept,child,1),(child,child,0),(peer,peer,0)]:
            conn.execute('insert into platform_control.department_closure values (%s,%s,%s,%s)', (generation,ancestor,descendant,depth))
        for i, member in enumerate(members):
            conn.execute("insert into platform_control.directory_members (generation_id,member_key,subject_kind,lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status) values (%s,%s,'employee',%s,1,%s,1,%s,%s)", (generation,member,bytes([i])*32,b'private',f'Member {i}', ['active','inactive','disabled','active'][i]))
        for member, department in [(members[0],dept),(members[0],child),(members[1],child),(members[2],peer)]:
            conn.execute('insert into platform_control.member_departments values (%s,%s,%s)', (generation,member,department))
        conn.execute('update platform_control.directory_state set active_generation_id=%s', (generation,))
    return env, generation, ids

@pytest.mark.postgres
def test_sql_privileges(control_database):
    for env in control_database['environments'].values():
        with psycopg.connect(env['admin']) as conn:
            assert conn.execute('select to_regprocedure(%s)', (SIGNATURE,)).fetchone()[0] is not None
            for role in ['public', *control_database['environments']['production']['roles'], *control_database['environments']['preview']['roles']]:
                assert conn.execute("select has_function_privilege(%s,%s,'execute')", (role,SIGNATURE)).fetchone()[0] == (role == env['roles'][1])
            row = conn.execute('select prosecdef,proconfig from pg_proc where oid=%s::regprocedure', (SIGNATURE,)).fetchone()
            assert row == (True, ['search_path=pg_catalog, platform_control'])
        with psycopg.connect(env['urls'][env['roles'][1]]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute('select * from platform_control.directory_members')

@pytest.mark.postgres
def test_snapshot_tree_counts_pagination_and_errors(directory):
    env, generation, ids = directory
    module = importlib.import_module('app.ai_engineering.organization')
    repo = module.OrganizationDirectoryRepository(env['urls'][env['roles'][1]])
    root, dept, child, peer, *members = ids
    tree = repo.tree()
    assert set(tree) == {'generation_id','completed_at','freshness','scope','root_id','departments'}
    assert tree['generation_id'] == str(generation)
    assert tree['root_id'] == str(root)
    assert tree['freshness'] == 'fresh'
    assert tree['scope'] == 'visible_directory'
    assert len(tree['departments']) == 4
    assert all(set(d) == {'id','parent_id','name'} for d in tree['departments'])
    detail = repo.department(dept, generation, limit=1)
    assert detail['direct_count'] == 1 and detail['total_count'] == 2
    assert detail['status_counts'] == {'active':1,'inactive':1,'disabled':0}
    assert detail['position_available'] is False
    assert detail['next_cursor'] == str(members[0])
    assert len(detail['members'][0]['departments']) == 2
    assert set(detail['members'][0]) == {'id','name','status','departments'}
    page = repo.department(dept, generation, cursor=detail['next_cursor'], limit=1)
    assert [m['id'] for m in page['members']] == [str(members[1])]
    assert page['next_cursor'] is None
    company = repo.department(root,generation)
    assert company['total_count'] == 4 and company['direct_count'] == 0
    assert company['members'][-1]['departments'] == []
    for selected, gen, expected in [(uuid4(),generation,404),(dept,uuid4(),409)]:
        with pytest.raises(module.OrganizationDirectoryError) as error:
            repo.department(selected,gen)
        assert error.value.status_code == expected
    for hours, freshness in [(9,'warning'),(25,'hard_stale')]:
        with psycopg.connect(env['admin']) as conn:
            conn.execute("update platform_control.directory_generations set completed_at=now()-%s*interval '1 hour' where generation_id=%s", (hours,generation))
        assert repo.tree()['freshness'] == freshness
    with psycopg.connect(env['admin']) as conn:
        conn.execute("update platform_control.directory_generations set status='failed' where generation_id=%s", (generation,))
    with pytest.raises(module.OrganizationDirectoryError) as error:
        repo.tree()
    assert error.value.status_code == 503

@pytest.mark.parametrize('role', list(Role))
def test_api_role_matrix_and_revocation(tmp_path, monkeypatch, role):
    client, auth = client_for(tmp_path,monkeypatch,role)
    class Repository:
        def tree(self): return {'generation_id':'test'}
        def department(self,*args,**kwargs): return {'department_id':str(args[0])}
    client.app.state.organization_directory = Repository()
    paths = [PREFIX, PREFIX + f'/departments/{uuid4()}?generation_id={uuid4()}']
    for path in paths:
        response = client.get(path)
        assert response.status_code == 401
        assert_private(response)
    client.cookies.set(auth.cookie_name,'valid-cookie')
    for path in paths:
        response = client.get(path)
        assert response.status_code == (200 if role in {Role.PLATFORM_ADMIN,Role.PLATFORM_OWNER} else 403)
        assert_private(response)
    auth.context = AuthContext(auth.context.internal_user_id,Role.MEMBER,auth.context.session_id,False)
    assert client.get(PREFIX).status_code == 403

@pytest.mark.parametrize('query', ['', '?generation_id=bad', f'?generation_id={uuid4()}&limit=0', f'?generation_id={uuid4()}&limit=101', f'?generation_id={uuid4()}&cursor=bad'])
def test_api_invalid_input_private(tmp_path,monkeypatch,query):
    client,auth = client_for(tmp_path,monkeypatch)
    client.cookies.set(auth.cookie_name,'valid-cookie')
    response = client.get(PREFIX + f'/departments/{uuid4()}' + query)
    assert response.status_code == 422
    assert_private(response)

@pytest.mark.postgres
def test_real_http_generation_errors_and_empty_department(directory,tmp_path,monkeypatch):
    env,generation,ids = directory
    module = importlib.import_module('app.ai_engineering.organization')
    repo = module.OrganizationDirectoryRepository(env['urls'][env['roles'][1]])
    client,auth = client_for(tmp_path,monkeypatch)
    client.app.state.organization_directory = repo
    client.cookies.set(auth.cookie_name,'valid-cookie')
    tree = client.get(PREFIX)
    assert tree.status_code == 200
    assert_private(tree)
    root,dept,child,peer,*members = ids
    path = PREFIX + f'/departments/{dept}?generation_id={generation}'
    response = client.get(path + '&limit=1')
    assert response.status_code == 200
    assert response.json()['total_count'] == 2
    assert_private(response)
    response = client.get(path + f'&cursor={members[1]}')
    assert response.json()['members'] == [] and response.json()['next_cursor'] is None
    for path,code in [(PREFIX + f'/departments/{uuid4()}?generation_id={generation}',404),
                      (PREFIX + f'/departments/{dept}?generation_id={uuid4()}',409)]:
        response = client.get(path)
        assert response.status_code == code
        assert_private(response)
    with psycopg.connect(env['admin']) as conn:
        conn.execute('delete from platform_control.member_departments where generation_id=%s and department_key=%s', (generation,peer))
    empty = repo.department(peer,generation)
    assert empty['direct_count'] == empty['total_count'] == 0
    assert empty['members'] == [] and empty['next_cursor'] is None
    assert repo.department(root,generation)['total_count'] == 4
    with psycopg.connect(env['admin']) as conn:
        conn.execute('update platform_control.directory_state set active_generation_id=null')
    response = client.get(PREFIX)
    assert response.status_code == 503
    assert_private(response)
    assert 'total_count' not in response.json()

@pytest.mark.postgres
@pytest.mark.parametrize('condition', ['no_departments','null_completed','staging'])
def test_invalid_or_empty_snapshots_are_unavailable(directory,condition):
    env,generation,ids = directory
    module = importlib.import_module('app.ai_engineering.organization')
    repo = module.OrganizationDirectoryRepository(env['urls'][env['roles'][1]])
    with psycopg.connect(env['admin']) as conn:
        if condition == 'no_departments':
            conn.execute('delete from platform_control.directory_departments where generation_id=%s',(generation,))
        elif condition == 'null_completed':
            conn.execute('update platform_control.directory_generations set completed_at=null where generation_id=%s',(generation,))
        else:
            conn.execute("update platform_control.directory_generations set status='staging' where generation_id=%s",(generation,))
    with pytest.raises(module.OrganizationDirectoryError) as error:
        repo.tree()
    assert error.value.status_code == 503

@pytest.mark.postgres
def test_sql_bounds_and_denied_execution(directory,control_database):
    env,generation,ids = directory
    with psycopg.connect(env['urls'][env['roles'][1]]) as conn:
        for limit in [None,0,101]:
            assert conn.execute('select platform_control.read_organization_directory_v109(%s,%s,null,%s)',(ids[0],generation,limit)).fetchone()[0] == {'error':'invalid_input'}
        assert conn.execute('select platform_control.read_organization_directory_v109(%s,null,null,50)',(ids[0],)).fetchone()[0] == {'error':'invalid_input'}
    for role in env['roles']:
        if role == env['roles'][1]:
            continue
        with psycopg.connect(env['urls'][role]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute('select platform_control.read_organization_directory_v109(null,null,null,50)')


def test_repository_rejects_owner_and_worker_credentials():
    module = importlib.import_module('app.ai_engineering.organization')
    for role in ['platform_control_owner','platform_directory_worker','platform_control_app_preview']:
        with pytest.raises(ValueError, match='exact control app DSN required'):
            module.OrganizationDirectoryRepository(f'postgresql://{role}@localhost/agent_platform_control')


def test_api_unconfigured_directory_is_private_unavailable(tmp_path,monkeypatch):
    client,auth = client_for(tmp_path,monkeypatch)
    client.cookies.set(auth.cookie_name,'valid-cookie')
    response = client.get(PREFIX)
    assert response.status_code == 503
    assert_private(response)

@pytest.mark.postgres
def test_promoted_snapshot_invalidates_previous_member_cursor(directory):
    env,generation,ids = directory
    module = importlib.import_module('app.ai_engineering.organization')
    repo = module.OrganizationDirectoryRepository(env['urls'][env['roles'][1]])
    first = repo.department(ids[0],generation,limit=1)
    next_generation = uuid4()
    with psycopg.connect(env['admin']) as conn:
        conn.execute("insert into platform_control.directory_generations (generation_id,status,completed_at) values (%s,'complete',now())",(next_generation,))
        conn.execute('insert into platform_control.directory_departments (generation_id,department_key,parent_department_key,lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name) select %s,department_key,parent_department_key,lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name from platform_control.directory_departments where generation_id=%s',(next_generation,generation))
        conn.execute('update platform_control.directory_state set active_generation_id=%s',(next_generation,))
    assert repo.tree()['generation_id'] == str(next_generation)
    with pytest.raises(module.OrganizationDirectoryError) as error:
        repo.department(ids[0],generation,cursor=first['next_cursor'])
    assert error.value.status_code == 409
    assert repo.department(ids[0],next_generation)['total_count'] == 0

@pytest.mark.postgres
def test_future_snapshot_timestamp_is_unavailable(directory):
    env,generation,_ = directory
    module = importlib.import_module('app.ai_engineering.organization')
    repo = module.OrganizationDirectoryRepository(env['urls'][env['roles'][1]])
    with psycopg.connect(env['admin']) as conn:
        conn.execute("update platform_control.directory_generations set completed_at=now()+interval '1 hour' where generation_id=%s",(generation,))
    with pytest.raises(module.OrganizationDirectoryError) as error:
        repo.tree()
    assert error.value.status_code == 503

@pytest.mark.postgres
@pytest.mark.parametrize('invalid_parent', ['orphan','cycle','multiple_roots'])
def test_invalid_tree_is_unavailable_without_repair(directory,invalid_parent):
    env,generation,ids = directory
    module = importlib.import_module('app.ai_engineering.organization')
    repo = module.OrganizationDirectoryRepository(env['urls'][env['roles'][1]])
    parent = {'orphan':uuid4(),'cycle':ids[2],'multiple_roots':None}[invalid_parent]
    with psycopg.connect(env['admin']) as conn:
        conn.execute('update platform_control.directory_departments set parent_department_key=%s where generation_id=%s and department_key=%s',(parent,generation,ids[1]))
    with pytest.raises(module.OrganizationDirectoryError) as error:
        repo.tree()
    assert error.value.status_code == 503
