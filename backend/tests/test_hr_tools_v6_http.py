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
    from app.hr.channel_identity import HrChannelIdentityService,attach_channel_worker_route,build_channel_user_router
    from app.agent_brain.conversation_repository import ConversationRepository
    from app.agent_brain.conversation_service import ConversationCommandService
    channels=HrChannelIdentityService(HrToolService(relay),ConversationCommandService(ConversationRepository(database_url,content_codec=_codec(),worker_direct_enabled=True),v2_enabled=False),AgentUseAuthorization(database_url))
    channel_router=APIRouter(prefix='/api/v1/execution-worker')
    attach_channel_worker_route(channel_router,authenticate,channels)
    app.include_router(channel_router)
    app.include_router(build_channel_user_router(channels,access))


class ToolLoop(WebLoop):
    configure_app = staticmethod(configure_tools)


@pytest.fixture()
def tool_loop(scoped_database, tmp_path, request):
    env = scoped_database
    from app.hr.role_package import HrRolePackages
    from hashlib import sha256
    commit = 'a'*40
    root = tmp_path/'roles'/commit
    files = []
    for name in ['bots/hr/CLAUDE.md','shared/base-rules.md','shared/orbbec-context.md','shared/web-research.md']:
        target = root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text('Disposable role fixture')
        files.append({'path':name,'sha256':sha256(target.read_bytes()).hexdigest()})
    (root/'role-package.json').write_text(json.dumps({'format':'hr-role-package-v1','teamCommit':commit,'catalogRelease':commit,'cwd':'bots/hr','files':files}))
    roles = HrRolePackages(root.parent,commit)
    ids = _seed_candidate_scope(env)
    if getattr(request,'param',False):
        with psycopg.connect(env['urls']['platform_control_app']) as connection:
            connection.execute('select platform_hr.confirm_candidate_draft_v70(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)',
                (ids['owner'],ids['draft'],uuid4(),2,ids['candidate'],None,ids['document'],ids['relation'],ids['context'],'Fixture Candidate','{"skills":["Python"]}'))
    attachment_grants=None
    if getattr(request,'param',None)=='candidate_attachment':
        from app.attachments.conversation_repository import attachment_name_subject,attachment_object_subject
        from app.attachments.grant_service import AttachmentGrantService,TaskGrantRepository
        source_chat=repository(env).ensure_direct_conversation_shell(ids['owner'],uuid4(),direct_agent_id='hr-bot',title='Original resume conversation')
        codec=_codec();name=codec.seal_json(attachment_name_subject(ids['attachment']),{'original_name':'Fixture Resume.pdf'})
        obj=codec.seal_json(attachment_object_subject(ids['attachment']),{'object_ref':'fixture/resume.pdf'})
        with psycopg.connect(env['admin']) as connection:
            connection.execute("update platform_attachments.attachments set original_name_ciphertext=%s,original_name_key_version=%s,object_ref_ciphertext=%s,object_ref_key_version=%s,detected_mime='application/pdf',size_bytes=128,conversation_id=%s where attachment_id=%s",
                (name.ciphertext,name.key_version,obj.ciphertext,obj.key_version,source_chat.conversation_id,ids['attachment']))
        attachment_grants=AttachmentGrantService(TaskGrantRepository(env['urls']['platform_control_app'],content_codec=codec),None)
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
    ready=observation()
    ready['version']='hr_v6_readiness_v1'
    ready['service'].update(contractVersion='core_chat_collaboration_v6',rolePackage=roles.select().model_dump(mode='json',by_alias=True),
        toolCapabilities=['hr.read_context','hr.submit_result','hr.confirm_standard'])
    record_observation(relay,worker_id,ready)
    loop = ToolLoop(env,ids['owner'],conversation.conversation_id)
    loop.roles = roles
    try:
        response = loop.client.post(f'/api/v1/conversations/{conversation.conversation_id}/messages',
            json={'text':'按当前岗位分析','scope':{'positionId':str(ids['position']),'positionCandidateIds':[str(ids['relation'])] if getattr(request,'param',False) else [],'attachmentIds':[str(ids['attachment'])] if getattr(request,'param',None)=='candidate_attachment' else []}},
            headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code in (200,201),response.text
        attempts = TurnAttemptRepository(env['urls']['platform_control_app'],_codec())
        lease = attempts.claim_due(uuid4(),120)
        assert lease is not None
        bindings = DirectCommandBindingRepository(relay)
        adapter = DirectMissionAdapter(attempts,bindings,ConversationContextBuilder(repo),TurnResultProjector(attempts,bindings),role_packages=loop.roles,attachment_grants=attachment_grants)
        binding = adapter.prepare(lease)
        assert binding.frozen.document['contractVersion']=='core_chat_collaboration_v6'
        assert binding.frozen.document['scope']['positionId']==str(ids['position'])
        assert 'official_facts' not in binding.frozen.document['prompt']
        assert adapter.prepare(lease).frozen.command_hash == binding.frozen.command_hash
        loop.prepared=binding
        with attempts.transaction() as connection:
            loop.input_grants=bindings.materials(bindings._transport_row(lease,connection))['inputAttachmentGrants']
        service = HrToolService(relay)
        with attempts.transaction() as connection:
            grant = service.issue_grant(connection,lease.attempt_id,worker_id,lease.lease_epoch)
        yield loop, WorkerRequestSigner(worker_id,'worker-v1',key), grant, service, lease, ids
    finally:
        loop.close()
        # Dispose unfinished fixture attempts so a later test cannot claim them.
        # This is fixture cleanup, not execution recovery or a successful terminal.
        with psycopg.connect(env['admin']) as cleanup:
            cleanup.execute("update platform_control.execution_workers set status='revoked',revoked_at=clock_timestamp() where worker_id=%s",(worker_id,))
            cleanup.execute("update platform_control.turn_attempts a set status='cancelled' from platform_control.conversation_turns t join platform_control.conversations c using(conversation_id) where a.turn_id=t.turn_id and c.owner_internal_user_id=%s and a.status not in ('succeeded','failed','cancelled')",(ids['owner'],))
            cleanup.execute("update platform_control.conversation_turns t set status='cancelled' from platform_control.conversations c where c.conversation_id=t.conversation_id and c.owner_internal_user_id=%s and t.status not in ('succeeded','failed','cancelled')",(ids['owner'],))


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


def confirm_selected_standard(loop,signer,grant,service,lease,ids,*,channel=False):
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
    if channel:
        path='/api/v1/execution-worker/hr/v6/channel-messages'
        message={'tenantId':'confirm-tenant','appId':'confirm-app','openId':'confirm-person','chatId':'confirm-private','messageId':'confirm-message','text':''}
        def send_channel(text):
            raw=json.dumps({**message,'text':text},ensure_ascii=False).encode()
            return httpx.post(loop.origin+path,content=raw,headers={**signer.sign('POST',path,raw),'Content-Type':'application/json'},timeout=10)
        link=loop.client.post('/api/v1/hr/channel-link')
        assert link.status_code==200,link.text
        assert send_channel(link.json()['command']).status_code==200
        from app.hr.standard_consent import normalize_consent
        next_turn=send_channel(normalize_consent(consent).channel_message_text())
        assert next_turn.status_code==200,next_turn.text
        assert send_channel(normalize_consent(consent).channel_message_text()).json()==next_turn.json()
    else:
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
    adapter=DirectMissionAdapter(attempts,bindings,ConversationContextBuilder(repository(env)),TurnResultProjector(attempts,bindings),role_packages=loop.roles)
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


@pytest.mark.parametrize('tool_loop',[True],indirect=True)
def test_candidate_read_scoped_and_official_verification_fallback(tool_loop):
    from datetime import datetime,timezone
    from app.hr.position_intelligence_models import ProjectOfficialVersion
    from app.hr.position_intelligence_repository import PositionIntelligenceRepository
    from test_hr_position_importers import _job
    loop,signer,grant,service,lease,ids=tool_loop
    candidate={'tool':'hr.read_context','operationId':str(uuid4()),'resourceKind':'candidate',
        'resourceId':str(ids['relation']),'readMode':'recorded','versionRef':None}
    response=post(loop,signer,grant,'query',candidate)
    assert response.status_code==200,response.text
    facts=json.loads(response.json()['contentText'])
    assert facts['position_candidate_id']==str(ids['relation']) and facts['documents']==[]
    wrong=post(loop,signer,grant,'query',{**candidate,'operationId':str(uuid4()),'resourceId':str(uuid4())})
    assert wrong.status_code==409 and wrong.json()['code']=='scope_mismatch'
    now=datetime.now(timezone.utc)
    with psycopg.connect(loop.environment['admin']) as connection:
        connection.execute("update platform_hr.positions set source_kind='official_site',official_job_id='J11014',official_status='active' where position_id=%s",(ids['position'],))
    repository=PositionIntelligenceRepository(loop.environment['urls']['platform_control_app'])
    repository.project_official_version(ProjectOfficialVersion(uuid4(),ids['owner'],ids['position'],uuid4(),'J11014',
        'Engineer','研发',('深圳',),'研发',None,1,None,'全职','面议','Build.','Test.','registry-1',now,'a'*64,
        now,now,'active','published',{'snapshot':'registry-1'}))
    read={**candidate,'operationId':str(uuid4()),'resourceKind':'official_position','resourceId':str(ids['position']),'readMode':'reverify'}
    reply=post(loop,signer,grant,'official-verifications',{'request':read,'observation':{'verification':'unavailable'}})
    assert reply.status_code==200,reply.text
    assert reply.json()['verification']=='degraded'
    job=_job();content_hash=job.pop('contentHash')
    observation={'job':job,'registryVersion':'registry-2','jobContentHash':content_hash,
        'lastSuccessfulSyncAt':now.isoformat(),'verification':'current','health':{'status':'healthy','warnings':[]}}
    read['operationId']=str(uuid4())
    reply=post(loop,signer,grant,'official-verifications',{'request':read,'observation':observation})
    assert reply.status_code==200,reply.text
    assert reply.json()['verification']=='current' and reply.json()['versionRef']=='registry-2'
    read['operationId']=str(uuid4());observation['job']['canonicalId']='J99999'
    rejected=post(loop,signer,grant,'official-verifications',{'request':read,'observation':observation})
    assert rejected.status_code==400,rejected.text


@pytest.mark.parametrize('tool_loop',['candidate_attachment'],indirect=True)
def test_selected_candidate_document_uses_grants_without_conversation_rebinding(tool_loop):
    from app.attachments.grant_service import TaskGrantRepository,TaskGrantUnavailable,bearer_token_sha256
    loop,signer,grant,service,lease,ids=tool_loop
    command=loop.prepared.frozen.document
    inputs=loop.input_grants
    assert len(inputs)==1 and inputs[0]['attachmentId']==str(ids['attachment'])
    repository=TaskGrantRepository(loop.environment['urls']['platform_control_app'],content_codec=_codec())
    with pytest.raises(TaskGrantUnavailable):
        repository.consume_read(token_sha256=bearer_token_sha256('x'*43),attachment_id=ids['attachment'])
    asset=repository.consume_read(token_sha256=bearer_token_sha256(inputs[0]['bearerToken']),attachment_id=ids['attachment'])
    assert asset.attachment_id==ids['attachment'] and asset.conversation_id!=loop.conversation_id
    read={'tool':'hr.read_context','operationId':str(uuid4()),'resourceKind':'candidate',
        'resourceId':str(ids['relation']),'readMode':'recorded','versionRef':None}
    response=post(loop,signer,grant,'query',read)
    assert response.status_code==200,response.text
    assert json.loads(response.json()['contentText'])['documents'][0]['content_sha256']==('68'*32)
    with psycopg.connect(loop.environment['admin']) as connection:
        assert connection.execute("select count(*) from platform_hr.position_conversations where conversation_id=%s",(loop.conversation_id,)).fetchone()[0]==0
        connection.execute("update platform_attachments.attachments set retained_until=clock_timestamp()-interval '1 microsecond' where attachment_id=%s",(ids['attachment'],))
    with pytest.raises(TaskGrantUnavailable):
        repository.consume_read(token_sha256=bearer_token_sha256(inputs[0]['bearerToken']),attachment_id=ids['attachment'])


def test_channel_identity_and_idempotent_scoped_intake(tool_loop):
    from app.agent_brain.conversation_repository import message_subject
    from app.execution_relay.content_crypto import SealedContent
    loop,signer,grant,service,lease,ids=tool_loop
    path='/api/v1/execution-worker/hr/v6/channel-messages'
    message={'tenantId':'fixture-tenant','appId':'fixture-app','openId':'fixture-person','chatId':'fixture-private','messageId':'fixture-message','text':'/标准 J11014'}
    def send(value, signed=True):
        raw=json.dumps(value,ensure_ascii=False).encode()
        headers={'Content-Type':'application/json'}
        if signed:headers.update(signer.sign('POST',path,raw))
        return httpx.post(loop.origin+path,content=raw,headers=headers,timeout=10)
    assert send(message,False).status_code==401
    assert send(message).status_code==403
    code=loop.client.post('/api/v1/hr/channel-link')
    assert code.status_code==200,code.text
    linked=send({**message,'text':code.json()['command']})
    assert linked.status_code==200,linked.text
    assert send({**message,'text':code.json()['command']}).status_code==200
    assert send({**message,'openId':'other-person','text':code.json()['command']}).status_code==403
    with psycopg.connect(loop.environment['admin']) as c:
        c.execute("update platform_hr.positions set source_kind='official_site',official_job_id='J11014',official_status='active' where position_id=%s",(ids['position'],))
        other=_seed_candidate_scope(loop.environment)
        c.execute("update platform_hr.positions set source_kind='official_site',official_job_id='J11014',official_status='active' where position_id=%s",(other['position'],))
    standard=send(message)
    assert standard.status_code==200,standard.text
    assert str(ids['context']) in standard.json()['text'] and str(other['context']) not in standard.json()['text']
    assert send({**message,'text':'/标准 J99999'}).status_code==403
    intake={**message,'messageId':'fixture-intake','text':'J11014 请基于已确认标准继续校准需求'}
    accepted=send(intake)
    assert accepted.status_code==200,accepted.text
    assert send(intake).json()==accepted.json()
    assert send({**intake,'text':'J11014 换一条输入'}).status_code==409
    with service.repository._connection() as c:
        row=c.execute('select m.* from platform_control.conversation_turns t join platform_control.conversation_messages m on m.message_id=t.user_message_id where t.turn_id=%s',(UUID(accepted.json()['turnId']),)).fetchone()
        value=service.codec.unseal_json(message_subject(row['conversation_id'],row['message_id']),SealedContent(bytes(row['content_ciphertext']),row['encryption_key_version']))
        assert value['text']==intake['text'] and value['trusted_channel_origin']['openId']=='fixture-person'
        scope=c.execute('select hr_input_context from platform_control.conversation_turns where turn_id=%s',(UUID(accepted.json()['turnId']),)).fetchone()
        assert scope['hr_input_context']['scope']['positionId']==str(ids['position'])
    assert loop.client.delete('/api/v1/hr/channel-link').status_code==200
    assert send(message).status_code==403


def test_channel_confirmation_keeps_real_owner_and_partial_selection(tool_loop):
    confirm_selected_standard(*tool_loop,channel=True)
