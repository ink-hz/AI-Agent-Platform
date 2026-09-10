from uuid import uuid4
import pytest
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from app.hr_agent.repository import HrAgentRepository
from app.hr_agent.types import HrAgentProblem, ModelContext, ModelReply, Usage, ToolCall, ResultQuery, validate_contract
from hr_agent_support import hr_agent_database

@pytest.fixture(scope='module')
def database():
    with hr_agent_database() as db: yield db

@pytest.fixture
def repo(database):
    with database.admin_connection() as c:
        c.execute('TRUNCATE platform_hr_agent.threads, platform_hr_agent.operations CASCADE')
    return HrAgentRepository(database.connection,ContentCodec(IdentityKeyring(1,'platform-content-encryption',{1:b'v'*32})))

def request(**updates):
    return {'thread_id':None,'text':'虚构研究','objects':[],'references':[],'budget_profile':'calibration-test',**updates}

def save_result(repo,owner,**updates):
    work=repo.submit(owner,request(budget_profile=repo.settings.budget_profile["id"] if repo.settings else "calibration-test"),uuid4())
    fence=repo.claim('views-test',60)
    ctx=ModelContext('work',({'role':'user','content':'虚构'},),(),(),100,1)
    attempt=repo.prepare_model(fence,ctx); repo.mark_model_sending(fence,attempt.attempt_id)
    args={'result_id':None,'expected_revision':None,'kind':'research','title':'虚构研究成果','body':'虚构证据与限制','objects':[],'source_refs':[],'preceding_refs':[],'base_standard_ref':None,'changes':[],'basis':[],**updates}
    ops=repo.commit_model(fence,attempt.attempt_id,ModelReply('',(ToolCall('one','save_result',args),),'stop',Usage(None,100,20,'reported')))
    result=repo.execute_local_tool(fence,ops[0])
    assert result['status']=='ok'
    repo.cancel(owner,work['work_id'],'fixture complete',uuid4())
    return work,result['data']

def test_thread_and_work_discovery_only_returns_owned_records(repo):
    owner=uuid4();work=repo.submit(owner,request(),uuid4())
    repo.submit(uuid4(),request(text='另一用户'),uuid4())
    page=repo.list_threads(owner)
    validate_contract('ThreadPage',page)
    assert [r['thread_id'] for r in page['items']]==[work['thread_id']]
    works=repo.list_works(owner,work['thread_id'])
    validate_contract('WorkPage',works)
    assert works['items'][0]['work_id']==work['work_id']
    with pytest.raises(HrAgentProblem):repo.list_works(uuid4(),work['thread_id'])

def test_result_read_list_link_replay_and_conflict(repo):
    owner=uuid4();work,result=save_result(repo,owner)
    ref=result['ref']
    assert repo.read_result(owner,ref['id'],ref['revision'])==result
    page=repo.list_results(owner,ResultQuery(thread_id=work['thread_id']))
    validate_contract('ResultPage',page)
    assert page['items'][0]['ref']==ref
    position={'kind':'position','id':str(uuid4())}
    repo.scope_validator=lambda *args: None
    body={'expected_result_revision':ref['revision'],'objects':[position]};key=uuid4()
    linked=repo.link_result(owner,ref['id'],body,key)
    validate_contract('LinkReceipt',linked)
    assert repo.link_result(owner,ref['id'],body,key)==linked
    assert repo.read_result(owner,ref['id'],ref['revision'])==result
    assert repo.list_results(owner,ResultQuery(object_ref=position))['items'][0]['ref']==ref
    with pytest.raises(HrAgentProblem) as e:repo.link_result(owner,ref['id'],{**body,'objects':[]},key)
    assert e.value.problem['code']=='idempotency_conflict'
    with pytest.raises(HrAgentProblem) as e:repo.link_result(owner,ref['id'],{**body,'expected_result_revision':str(uuid4())},uuid4())
    assert e.value.problem['code']=='revision_conflict'

def test_result_owner_filter_and_exact_revision_before_decryption(repo):
    owner=uuid4();work,result=save_result(repo,owner);ref=result['ref']
    class Bomb:
        def unseal_json(self,*args):pytest.fail('wrong owner reached decrypt')
    secured=HrAgentRepository(repo.connection_factory,Bomb())
    with pytest.raises(HrAgentProblem) as e:secured.read_result(uuid4(),ref['id'],ref['revision'])
    assert e.value.http_status==404
    with pytest.raises(HrAgentProblem):repo.read_result(owner,ref['id'],'current')


def test_result_transitive_revocation_prevents_read_and_listing(repo):
    owner=uuid4();work,result=save_result(repo,owner);ref=result['ref']
    source={'kind':'method','id':'fake-method','revision':'fake-v1','sha256':'a'*64}
    with repo.transaction() as c:repo._edges(c,owner,'result',ref['id'],ref['revision'],[source])
    def deny(*args):raise HrAgentProblem({'code':'dependency_revoked','message':'revoked','retryable':False,'details':{}},403)
    repo.scope_validator=deny
    with pytest.raises(HrAgentProblem):repo.read_result(owner,ref['id'],ref['revision'])
    assert repo.list_results(owner,ResultQuery(thread_id=work['thread_id']))['items']==[]


def test_cursor_is_authenticated_and_owner_query_bound(repo):
    owner=uuid4()
    for i in range(51):repo.submit(owner,request(text=f'虚构{i}'),uuid4())
    page=repo.list_threads(owner);cursor=page['next_cursor']
    assert len(page['items'])==50 and cursor
    last=repo.list_threads(owner,cursor)
    assert len(last['items'])==1 and last['next_cursor'] is None
    assert not ({r['thread_id'] for r in page['items']} & {r['thread_id'] for r in last['items']})
    for bad_owner,bad_cursor in [(uuid4(),cursor),(owner,cursor[:-3]+'xyz')]:
        with pytest.raises(HrAgentProblem) as e:repo.list_threads(bad_owner,bad_cursor)
        assert e.value.problem['code']=='revision_conflict'
    with pytest.raises(HrAgentProblem):repo.list_works(owner,page['items'][0]['thread_id'],cursor)

def test_revoked_source_never_reaches_result_decryption_or_link_replay(repo):
    owner=uuid4();work,result=save_result(repo,owner);ref=result['ref']
    repo.scope_validator=lambda *args:None
    body={'expected_result_revision':ref['revision'],'objects':[]};key=uuid4()
    repo.link_result(owner,ref['id'],body,key)
    source={'kind':'method','id':'private-method','revision':'v1','sha256':'b'*64}
    with repo.transaction() as c:repo._edges(c,owner,'result',ref['id'],ref['revision'],[source])
    def revoked(owner,objects,refs,work):
        if refs:raise HrAgentProblem({'code':'dependency_revoked','message':'revoked','retryable':False,'details':{}},403)
    repo.scope_validator=revoked
    original=repo._unseal
    def guard(table,*args):
        if table=='result_revisions':pytest.fail('revoked result reached decrypt')
        return original(table,*args)
    repo._unseal=guard
    with pytest.raises(HrAgentProblem):repo.read_result(owner,ref['id'],ref['revision'])
    with pytest.raises(HrAgentProblem):repo.link_result(owner,ref['id'],body,key)


def test_corrupted_document_hash_is_not_returned(repo,database):
    owner=uuid4();work,result=save_result(repo,owner);ref=result['ref']
    with database.admin_connection() as c:
        c.execute('UPDATE platform_hr_agent.result_revisions SET sha256=%s WHERE revision_id=%s',('0'*64,ref['revision']))
    with pytest.raises(HrAgentProblem) as e:repo.read_result(owner,ref['id'],ref['revision'])
    assert e.value.problem['code']=='hash_mismatch'


def test_link_rejects_unrelated_candidate_and_rolls_back(repo,database):
    owner=uuid4();work,result=save_result(repo,owner);ref=result['ref']
    repo.scope_validator=lambda *args:None
    body={'expected_result_revision':ref['revision'],'objects':[{'kind':'candidate','id':str(uuid4())}]}
    with pytest.raises(HrAgentProblem):repo.link_result(owner,ref['id'],body,uuid4())
    with database.admin_connection() as c:
        assert c.execute('SELECT COUNT(*) FROM platform_hr_agent.result_links WHERE result_id=%s',(ref['id'],)).fetchone()[0]==0

def test_user_link_can_add_explicit_position_outside_old_work_selection(repo):
    owner=uuid4();work,result=save_result(repo,owner);ref=result['ref']
    position={'kind':'position','id':str(uuid4())}
    def current_scope(owner,objects,refs,work_id):
        if work_id is not None and objects:
            raise HrAgentProblem({'code':'scope_denied','message':'old work scope','retryable':False,'details':{}},403)
    repo.scope_validator=current_scope
    receipt=repo.link_result(owner,ref['id'],{'expected_result_revision':ref['revision'],'objects':[position]},uuid4())
    assert receipt['objects']==[position]
