"""v7 owned HTTP, authenticated session and signed worker; no model provider."""
from uuid import UUID,uuid4
import json
import httpx
import pytest
from test_hr_tools_v6_http import tool_loop, scoped_database, control_database


def post(loop,signer,grant,endpoint,body):
    path='/api/v1/execution-worker/hr/v7/'+endpoint
    raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
    headers=signer.sign('POST',path,raw)
    headers.update({'Content-Type':'application/json','X-Hr-Tool-Grant':str(grant.grant_id),'Authorization':'Bearer '+grant.bearer_token})
    return httpx.post(loop.origin+path,content=raw,headers=headers,timeout=5)

@pytest.mark.parametrize('tool_loop',['v7'],indirect=True)
def test_v7_candidate_result_is_owned_and_registered(tool_loop):
    loop,signer,grant,service,lease,ids=tool_loop
    read=post(loop,signer,grant,'query',{'tool':'hr.read_context','operationId':str(uuid4()),
        'resourceKind':'confirmed_standard','resourceId':str(ids['position']),'readMode':'recorded','versionRef':None})
    assert read.status_code==200,read.text
    value=read.json()
    result={'schemaId':'hr.candidate-analysis.v2','title':'候选人分析','markdown':'项目贡献需要核验',
        'positionCandidateIds':[str(ids['relation'])],
        'baselineRefs':[{'kind':'confirmed_standard','contextVersionId':str(ids['context']),
            'readId':value['readId'],'contentSha256':value['contentSha256']}],
        'sourceRefs':[{'readId':value['readId'],'contentSha256':value['contentSha256']}], 'methodSteps':[],
        'evidence':[{'positionCandidateId':str(ids['relation']),'dimension':'项目贡献','evidence':'材料陈述',
            'assessment':'uncertain','verification':'追问本人负责部分'}]}
    body={'tool':'hr.submit_result','operationId':str(uuid4()),'result':result}
    response=post(loop,signer,grant,'results',body)
    assert response.status_code==200,response.text
    assert post(loop,signer,grant,'results',body).json()==response.json()
    ref=response.json()['resultRef']
    saved=loop.client.get('/api/v1/hr/results/'+ref['resultId'])
    assert saved.status_code==200,saved.text
    assert saved.json()['positionCandidateIds']==[str(ids['relation'])]
    assert saved.json()['candidateDerived'] is True
    bad={**body,'operationId':str(uuid4()),'result':{**result,'positionCandidateIds':[str(uuid4())],'evidence':[{**result['evidence'][0],'positionCandidateId':str(uuid4())}]}}
    assert post(loop,signer,grant,'results',bad).status_code==400
    missing={**body,'operationId':str(uuid4()),'result':{**result,'baselineRefs':[]}}
    assert post(loop,signer,grant,'results',missing).status_code==400


def next_turn(loop,service,lease,ids,signer,body):
    """Retire a never-dispatched fixture; no fabricated model success."""
    import psycopg
    from test_hr_tools_v6_http import TurnAttemptRepository,DirectCommandBindingRepository,DirectMissionAdapter,ConversationContextBuilder,TurnResultProjector,repository,_codec
    attempts=TurnAttemptRepository(loop.environment['urls']['platform_control_app'],_codec())
    bindings=DirectCommandBindingRepository(service.repository)
    with attempts.transaction() as connection:
        assert bindings.retire_unoffered(lease,connection=connection)
    with psycopg.connect(loop.environment['admin']) as connection:
        connection.execute("update platform_control.turn_attempts set status='cancelled' where attempt_id=%s",(lease.attempt_id,))
        connection.execute("update platform_control.conversation_turns set status='cancelled' where turn_id=(select turn_id from platform_control.turn_attempts where attempt_id=%s)",(lease.attempt_id,))
    response=loop.client.post(f'/api/v1/conversations/{loop.conversation_id}/messages',json=body,headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    lease=attempts.claim_due(uuid4(),120);assert lease
    adapter=DirectMissionAdapter(attempts,bindings,ConversationContextBuilder(repository(loop.environment)),TurnResultProjector(attempts,bindings),role_packages=loop.roles,attachment_grants=loop.attachment_grants)
    prepared=adapter.prepare(lease)
    assert prepared.frozen.document['inputResultRefs']==body.get('inputResultRefs',[])
    with attempts.transaction() as connection:
        grant=service.issue_grant(connection,lease.attempt_id,signer.worker_id,lease.lease_epoch)
        message=connection.execute('select user_message_id from platform_control.conversation_turns where turn_id=(select turn_id from platform_control.turn_attempts where attempt_id=%s)',(lease.attempt_id,)).fetchone()['user_message_id']
    return lease,grant,message


@pytest.mark.parametrize('tool_loop',['v7'],indirect=True)
def test_prior_result_input_and_body_review_are_enforced(tool_loop):
    loop,signer,grant,service,lease,ids=tool_loop
    body={'tool':'hr.submit_result','operationId':str(uuid4()),'result':{
        'schemaId':'hr.standard-proposal.v1','title':'岗位协作要求','baseContextVersionId':str(ids['context']),
        'changes':[{'changeId':'mission','module':'mission','markdown':'根据项目证据明确职责'}],'sourceRefs':[]}}
    saved=post(loop,signer,grant,'results',body);assert saved.status_code==200,saved.text
    ref=saved.json()['resultRef']
    shown=loop.client.get('/api/v1/hr/results/'+ref['resultId']);assert shown.status_code==200
    # Candidate-derived standard remains a position-level proposal; no candidate needed to confirm it.
    assert shown.json()['candidateDerived'] is True
    assert shown.json()['positionCandidateIds']==[]
    scope={'positionId':str(ids['position']),'positionCandidateIds':[],'attachmentIds':[]}
    lease,grant,message=next_turn(loop,service,lease,ids,signer,{
        'text':'继续校准这份岗位建议','scope':scope,'inputResultRefs':[ref]})
    query={'tool':'hr.read_context','operationId':str(uuid4()),'resourceKind':'result',
        'resourceId':ref['resultId'],'readMode':'recorded','versionRef':ref['contentSha256']}
    read=post(loop,signer,grant,'query',query);assert read.status_code==200,read.text
    assert json.loads(read.json()['contentText'])['result']==body['result']
    assert post(loop,signer,grant,'query',{**query,'operationId':str(uuid4()),'resourceId':str(uuid4())}).status_code==409
    assert post(loop,signer,grant,'query',{**query,'operationId':str(uuid4()),'versionRef':'0'*64}).status_code==409
    consent={'proposalResultId':ref['resultId'],'proposalContentSha256':ref['contentSha256'],
        'expectedContextVersionId':str(ids['context']),'selectedChangeIds':['mission']}
    # A real user message with legacy generic confirmation is insufficient for v7.
    lease,grant,message=next_turn(loop,service,lease,ids,signer,{
        'text':'确认所选的 1 项岗位标准。','scope':scope,'standardConsent':consent})
    confirm={'tool':'hr.confirm_standard','operationId':str(uuid4()),**consent,'confirmationMessageId':str(message)}
    rejected=post(loop,signer,grant,'confirmations',confirm)
    assert rejected.status_code==409 and rejected.json()['code']=='confirmation_required',rejected.text
    consent['bodyReviewed']=True
    lease,grant,message=next_turn(loop,service,lease,ids,signer,{
        'text':'确认所选的 1 项岗位标准。已核对所选正文，仅保留岗位级标准，不含候选人个人信息或逐人评价。',
        'scope':scope,'inputResultRefs':[], 'standardConsent':consent})
    confirm.update(operationId=str(uuid4()),confirmationMessageId=str(message))
    accepted=post(loop,signer,grant,'confirmations',confirm)
    assert accepted.status_code==200,accepted.text
    assert accepted.json()['confirmedBy']==str(ids['owner'])


@pytest.mark.parametrize('tool_loop',['v7'],indirect=True)
def test_interview_plan_to_record_uses_exact_input_and_material(tool_loop):
    loop,signer,grant,service,lease,ids=tool_loop
    def material():
        read=post(loop,signer,grant,'query',{'tool':'hr.read_context','operationId':str(uuid4()),'resourceKind':'material',
            'resourceId':str(ids['attachment']),'readMode':'recorded','versionRef':None})
        assert read.status_code==200,read.text
        r=read.json();return {'kind':'user_material','attachmentId':str(ids['attachment']),'readId':r['readId'],'contentSha256':r['contentSha256']}
    baseline=material()
    plan={'schemaId':'hr.candidate-interview-plan.v1','title':'项目贡献面试方案','markdown':'追问具体贡献',
        'positionCandidateIds':[str(ids['relation'])],'baselineRefs':[baseline], 'sourceRefs':[], 'methodSteps':[],
        'objectives':['核验独立贡献'],'recordTemplate':'问题、回答证据、未知项',
        'questions':[{'questionId':'contribution','dimension':'独立贡献','question':'你具体负责哪部分？','followUps':['如何验证效果？'],
            'verificationGoal':'区分个人与团队贡献','strongEvidence':'可复述决策和验证过程','ordinaryAnswer':'仅概述团队结果',
            'riskSignals':'无法说明个人任务','technicalChecks':'验证技术选择与约束'}]}
    saved=post(loop,signer,grant,'results',{'tool':'hr.submit_result','operationId':str(uuid4()),'result':plan})
    assert saved.status_code==200,saved.text
    ref=saved.json()['resultRef']
    scope={'positionId':str(ids['position']),'positionCandidateIds':[str(ids['relation'])],'attachmentIds':[str(ids['attachment'])]}
    lease,grant,_=next_turn(loop,service,lease,ids,signer,{'text':'整理本次面试记录','scope':scope,'inputResultRefs':[ref]})
    record={'schemaId':'hr.candidate-interview-record.v1','title':'项目贡献面试记录','markdown':'根据用户提供记录整理',
        'positionCandidateIds':[str(ids['relation'])],'sourceRefs':[], 'methodSteps':[], 'planRef':ref,'recordMaterialRefs':[material()],
        'answers':[{'questionId':'contribution','answerEvidence':'用户记录的项目分工','assessment':'尚需技术核验','unknowns':'效果指标来源','nextVerification':'询问验证过程'}]}
    body={'tool':'hr.submit_result','operationId':str(uuid4()),'result':record}
    missing=post(loop,signer,grant,'results',body)
    assert missing.status_code==409 and missing.json()['code']=='result_unregistered',missing.text
    read=post(loop,signer,grant,'query',{'tool':'hr.read_context','operationId':str(uuid4()),'resourceKind':'result',
        'resourceId':ref['resultId'],'readMode':'recorded','versionRef':ref['contentSha256']})
    assert read.status_code==200,read.text
    saved_record=post(loop,signer,grant,'results',body)
    assert saved_record.status_code==200,saved_record.text
    result=loop.client.get('/api/v1/hr/results/'+saved_record.json()['resultRef']['resultId'])
    assert result.json()['result']['planRef']==ref
    assert result.json()['positionCandidateIds']==scope['positionCandidateIds']
    # Revoking original material invalidates subsequent reads of the referenced result.
    import psycopg
    with psycopg.connect(loop.environment['admin']) as connection:
        connection.execute("update platform_attachments.attachments set deleted_at=clock_timestamp() where attachment_id=%s",(ids['attachment'],))
    unavailable=post(loop,signer,grant,'query',{'tool':'hr.read_context','operationId':str(uuid4()),'resourceKind':'result',
        'resourceId':ref['resultId'],'readMode':'recorded','versionRef':ref['contentSha256']})
    assert unavailable.status_code==401,unavailable.text
