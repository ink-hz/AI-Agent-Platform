"""Owned HTTP process, real session/signature/lease/DB; no model provider."""
from uuid import UUID, uuid4
import json

import httpx
import psycopg
import pytest
from fastapi import APIRouter, Request, HTTPException
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from test_hr_turn_scope_v6_database import scoped_database, control_database, _seed_candidate_scope, repository
from tests.helpers.hr_web_loop import WebLoop
from tests.helpers.v5_readiness import observation
from test_agent_brain_conversation_repository import _codec
from app.execution_relay.repository import ExecutionRelayRepository
from app.execution_relay.worker_auth import WorkerRequestSigner, WorkerRequestVerifier
from app.execution_relay.readiness_v5 import record_observation
from app.execution_relay.routes import _authenticate, ExecutionWorkerRequestLimiter
from app.agent_brain.turn_attempts import TurnAttemptRepository
from app.agent_brain.direct_command_binding import DirectCommandBindingRepository
from app.agent_brain.direct_mission_adapter import DirectMissionAdapter
from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.turn_result_projection import TurnResultProjector
from app.hr.tool_routes import attach_hr_tool_routes, build_hr_result_router
from app.hr.tool_service import HrToolService


def configure_tools(app, database_url):
    relay = ExecutionRelayRepository(database_url, content_codec=_codec())
    verifier, limiter = WorkerRequestVerifier(database_url), ExecutionWorkerRequestLimiter()
    async def authenticate(request):
        return await _authenticate(request, verifier, limiter, 1048576)
    router = APIRouter(prefix='/api/v1/execution-worker')
    attach_hr_tool_routes(router, authenticate, HrToolService(relay))
    app.include_router(router)
    from app.hr.routes import _auth_context
    from app.agent_brain.authorization import AgentUseAuthorization
    def access(request: Request):
        user = _auth_context(request).internal_user_id
        if not AgentUseAuthorization(database_url).decide_for_user_id(user,'hr-bot').allowed:
            raise HTTPException(403,'HR use denied')
        return user
    app.include_router(build_hr_result_router(HrToolService(relay),access))


class ToolLoop(WebLoop):
    configure_app = staticmethod(configure_tools)


@pytest.fixture()
def tool_loop(scoped_database):
    env = scoped_database
    ids = _seed_candidate_scope(env)
    repo = repository(env)
    conversation = repo.ensure_direct_conversation_shell(ids['owner'], uuid4(), direct_agent_id='hr-bot',title='HR tool fixture')
    key = Ed25519PrivateKey.generate()
    worker_id = 'tools-' + uuid4().hex
    with psycopg.connect(env['admin']) as connection:
        connection.execute("insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) values(%s,array['hr-bot'],'active')",(worker_id,))
        connection.execute("insert into platform_control.execution_worker_keys(worker_id,key_id,public_key,status) values(%s,'worker-v1',%s,'active')",
            (worker_id,key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)))
        connection.execute("update platform_control.conversations set execution_owner='worker_direct',route_epoch=7 where conversation_id=%s",(conversation.conversation_id,))
    relay = ExecutionRelayRepository(env['urls']['platform_control_app'],content_codec=_codec())
    record_observation(relay,worker_id,observation())
    loop = ToolLoop(env,ids['owner'],conversation.conversation_id)
    try:
        response = loop.client.post(f'/api/v1/conversations/{conversation.conversation_id}/messages',
            json={'text':'按当前岗位分析','scope':{'positionId':str(ids['position']),'positionCandidateIds':[],'attachmentIds':[]}},
            headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code in (200,201),response.text
        attempts = TurnAttemptRepository(env['urls']['platform_control_app'],_codec())
        lease = attempts.claim_due(uuid4(),120)
        assert lease is not None
        bindings = DirectCommandBindingRepository(relay)
        adapter = DirectMissionAdapter(attempts,bindings,ConversationContextBuilder(repo),TurnResultProjector(attempts,bindings))
        assert adapter.prepare(lease) is not None
        service = HrToolService(relay)
        with attempts.transaction() as connection:
            grant = service.issue_grant(connection,lease.attempt_id,worker_id,lease.lease_epoch)
        yield loop, WorkerRequestSigner(worker_id,'worker-v1',key), grant, service, lease, ids
    finally:
        loop.close()


def post(loop,signer,grant,endpoint,body,*,token=None,signed=True):
    path='/api/v1/execution-worker/hr/v6/'+endpoint
    raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
    headers=signer.sign('POST',path,raw) if signed else {}
    headers.update({'Content-Type':'application/json','X-Hr-Tool-Grant':str(grant.grant_id),
        'Authorization':'Bearer '+(token or grant.bearer_token)})
    return httpx.post(loop.origin+path,content=raw,headers=headers,timeout=5)


def test_signed_read_submit_replay_and_rejection(tool_loop):
    loop,signer,grant,service,lease,ids=tool_loop
    read={'tool':'hr.read_context','operationId':str(uuid4()),'resourceKind':'confirmed_standard',
        'resourceId':str(ids['position']),'readMode':'recorded','versionRef':None}
    unauth=post(loop,signer,grant,'query',read,signed=False)
    assert unauth.status_code==401,unauth.text
    wrong=post(loop,signer,grant,'query',read,token='x'*43)
    assert wrong.status_code==401,wrong.text
    response=post(loop,signer,grant,'query',read)
    assert response.status_code==200,response.text
    value=response.json()
    assert value['versionRef']==str(ids['context'])
    existing=json.loads(value['contentText'])
    assert existing['modules']=={'jd':{}}
    assert existing['confirmed_by']==str(ids['owner'])
    result={'tool':'hr.submit_result','operationId':str(uuid4()),'result':{
        'schemaId':'hr.analysis.v1','title':'本轮分析','markdown':'缺少已确认标准，先澄清实际要求。',
        'sourceRefs':[{'readId':value['readId'],'contentSha256':value['contentSha256']}],'methodSteps':[]}}
    submitted=post(loop,signer,grant,'results',result)
    assert submitted.status_code==200,submitted.text
    repeated=post(loop,signer,grant,'results',result)
    assert repeated.json()==submitted.json()
    saved=service.result(ids['owner'],UUID(submitted.json()['resultRef']['resultId']))
    assert saved['result']==result['result']
    result['result']['markdown']='用同一个操作编号更换内容'
    conflict=post(loop,signer,grant,'results',result)
    assert conflict.status_code==409 and conflict.json()['code']=='idempotency_conflict',conflict.text
    cross={**read,'operationId':str(uuid4()),'resourceId':str(uuid4())}
    mismatch=post(loop,signer,grant,'query',cross)
    assert mismatch.status_code==409 and mismatch.json()['code']=='scope_mismatch',mismatch.text
    lease,grant = confirm_selected_standard(loop,signer,grant,service,lease,ids)
    with psycopg.connect(loop.environment['admin']) as connection:
        connection.execute("update platform_control.turn_attempts set lease_expires_at=clock_timestamp()-interval '1 second' where attempt_id=%s",(lease.attempt_id,))
    expired=post(loop,signer,grant,'query',read)
    assert expired.status_code==401,expired.text


def confirm_selected_standard(loop,signer,grant,service,lease,ids):
    proposal={'tool':'hr.submit_result','operationId':str(uuid4()),'result':{
        'schemaId':'hr.standard-proposal.v1','title':'协作标准建议','baseContextVersionId':str(ids['context']),
        'changes':[{'changeId':'mission-1','module':'mission','markdown':'做好技术招聘协作'},
                   {'changeId':'unknown-1','module':'unknowns','markdown':'尚待讨论的预算'}], 'sourceRefs':[]}}
    submitted=post(loop,signer,grant,'results',proposal)
    assert submitted.status_code==200,submitted.text
    ref=submitted.json()['resultRef']
    shown=loop.client.get('/api/v1/hr/results/'+ref['resultId'])
    assert shown.status_code==200,shown.text
    consent={'proposalResultId':ref['resultId'],'proposalContentSha256':ref['contentSha256'],
        'expectedContextVersionId':str(ids['context']),'selectedChangeIds':['mission-1']}
    with psycopg.connect(loop.environment['admin']) as connection:
        turn=connection.execute('select t.turn_id,t.user_message_id from platform_control.turn_attempts a join platform_control.conversation_turns t using(turn_id) where a.attempt_id=%s',
            (lease.attempt_id,)).fetchone()
    false_confirmation={'tool':'hr.confirm_standard','operationId':str(uuid4()),**consent,'confirmationMessageId':str(turn[1])}
    denied=post(loop,signer,grant,'confirmations',false_confirmation)
    assert denied.status_code==409 and denied.json()['code']=='confirmation_required',denied.text
    attempts0=TurnAttemptRepository(loop.environment['urls']['platform_control_app'],_codec())
    bindings0=DirectCommandBindingRepository(service.repository)
    with attempts0.transaction() as connection:
        assert bindings0.retire_unoffered(lease,connection=connection)
    # A cancelled-turn fixture boundary; this is not executor recovery or model-success evidence.
    with psycopg.connect(loop.environment['admin']) as connection:
        connection.execute("update platform_control.turn_attempts set status='cancelled' where attempt_id=%s",(lease.attempt_id,))
        connection.execute("update platform_control.conversation_turns set status='cancelled' where turn_id=%s",(turn[0],))
    next_turn=loop.client.post(f'/api/v1/conversations/{loop.conversation_id}/messages',
        json={'text':'确认所选的 1 项岗位标准。','standardConsent':consent,
              'scope':{'positionId':str(ids['position']),'positionCandidateIds':[],'attachmentIds':[]}},
        headers={'Idempotency-Key':str(uuid4())})
    assert next_turn.status_code in (200,201),next_turn.text
    env=loop.environment
    attempts=TurnAttemptRepository(env['urls']['platform_control_app'],_codec())
    lease=attempts.claim_due(uuid4(),120)
    assert lease is not None
    relay=ExecutionRelayRepository(env['urls']['platform_control_app'],content_codec=_codec())
    bindings=DirectCommandBindingRepository(relay)
    adapter=DirectMissionAdapter(attempts,bindings,ConversationContextBuilder(repository(env)),TurnResultProjector(attempts,bindings))
    assert adapter.prepare(lease) is not None
    with attempts.transaction() as connection:
        grant=service.issue_grant(connection,lease.attempt_id,signer.worker_id,lease.lease_epoch)
        message=connection.execute('select user_message_id from platform_control.conversation_turns where turn_id=(select turn_id from platform_control.turn_attempts where attempt_id=%s)',(lease.attempt_id,)).fetchone()['user_message_id']
    request={'tool':'hr.confirm_standard','operationId':str(uuid4()),**consent,'confirmationMessageId':str(message)}
    confirmed=post(loop,signer,grant,'confirmations',request)
    assert confirmed.status_code==200,confirmed.text
    again=post(loop,signer,grant,'confirmations',request)
    assert again.json()==confirmed.json()
    with psycopg.connect(env['admin']) as connection:
        saved=connection.execute('select modules,base_context_version_id,confirmed_by from platform_hr.position_context_versions where context_version_id=%s',
            (UUID(confirmed.json()['contextVersionId']),)).fetchone()
        assert saved[0]=={'jd':{},'mission':{'markdown':'做好技术招聘协作'}}
        assert saved[1:]==(ids['context'],ids['owner'])
    return lease,grant
