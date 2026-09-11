"""Authorized HR business operations on the existing Turn/Attempt lifetime."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5

import psycopg
from app.execution_relay.content_crypto import SealedContent
from app.execution_relay.contracts_v6 import (
    HrBusinessToolGrant, HrReadContextRequest, HrSubmitResultRequest,
    HrConfirmStandardRequest, HrCandidateAnalysis, HrStandardProposal,
    HrReadContextReply, HrSubmitResultReply, HrResultRef,
)
from .turn_scope import load_authorized_turn_scope
from app.execution_relay.contracts_v7 import (CandidateDeliverable, HrCandidateInterviewRecord,
    HrReadContextReply, HrSubmitResultReply, HrResultRef)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class HrToolError(RuntimeError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class HrToolService:
    def __init__(self, repository, *, jd_verifier=None, calibration=None):
        self.repository = repository
        self.codec = repository.content_codec
        self.jd_verifier = jd_verifier
        from .calibration_service import HrCalibrationService
        self.calibration = calibration or HrCalibrationService(self)

    def issue_grant(self, connection, attempt_id, worker_id, lease_epoch):
        token = secrets.token_urlsafe(32)
        grant = HrBusinessToolGrant(grantId=uuid4(), bearerToken=token,
            expiresAt=datetime.now(timezone.utc) + timedelta(hours=24))
        connection.execute('select platform_hr.issue_tool_grant_v6(%s,%s,%s,%s,%s,%s)',
            (grant.grant_id, attempt_id, worker_id, lease_epoch, digest(token), grant.expires_at))
        return grant

    def _authorize(self, connection, worker_id, grant_id, token):
        if not isinstance(token, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
            raise HrToolError('permission_expired', '业务工具授权失效')
        try:
            with connection.transaction():
                value = connection.execute('select platform_hr.authorize_tool_grant_v6(%s,%s,%s) as scope',
                    (grant_id, worker_id, digest(token))).fetchone()['scope']
        except psycopg.errors.InsufficientPrivilege:
            pending=connection.execute('select platform_hr.tool_grant_renewal_pending_v6(%s,%s,%s) as pending',
                (grant_id,worker_id,digest(token))).fetchone()['pending']
            if pending: raise HrToolError('permission_expired','工具授权正在恢复，请沿用操作编号重试',retryable=True) from None
            raise

        owner, conversation, turn = (UUID(value[key]) for key in ('ownerId','conversationId','turnId'))
        allowed = connection.execute("select allowed from platform_control.resolve_agent_use_decision_v41(%s,'hr-bot')", (owner,)).fetchone()
        if allowed is None or allowed['allowed'] is not True:
            raise HrToolError('permission_expired', 'HR 使用权限已失效')
        scope=load_authorized_turn_scope(owner, conversation, turn, connection=connection)
        row=connection.execute('select j.job_id,j.run_id,j.payload_ciphertext,j.encryption_key_version from platform_control.direct_command_bindings b join platform_control.execution_jobs j using(job_id) where b.attempt_id=%s',(UUID(value['attemptId']),)).fetchone()
        if row is None: raise HrToolError('permission_expired','业务工具没有对应的冻结命令')
        frozen=self.codec.unseal_json(f"execution-job:{row['job_id']}:{row['run_id']}",SealedContent(bytes(row['payload_ciphertext']),row['encryption_key_version']))['command']
        value['contractVersion']=frozen['contractVersion']
        if frozen['contractVersion']=='core_chat_collaboration_v7':
            from dataclasses import replace
            refs=tuple(HrResultRef.model_validate_json(canonical(r)) for r in frozen['inputResultRefs'])
            if refs!=(scope.input_result_refs or ()):
                raise HrToolError('scope_mismatch','冻结成果输入与本轮不一致')
            scope=replace(scope,input_result_refs=refs)
        return scope,value

    def _decode(self, row):
        return self.codec.unseal_json(f"hr-tool:{row['receipt_id']}", SealedContent(row['payload_ciphertext'], row['encryption_key_version']))

    def official_source(self, worker_id, grant_id, token, request):
        try:
            with self.repository._connection() as connection:
                scope,_=self._authorize(connection,worker_id,grant_id,token)
                if request.tool!='hr.read_context' or request.resource_kind!='official_position' or scope.scope.position_id is None or request.resource_id not in {None,scope.scope.position_id}:
                    raise HrToolError('scope_mismatch','官网岗位不在本轮范围内')
                recorded=connection.execute("select 1 from platform_hr.tool_operations_v6 where turn_id=%s and tool='hr.read_context' and resource_kind='official_position' limit 1",(scope.turn_id,)).fetchone()
                row=connection.execute('select official_job_id from platform_hr.positions where owner_internal_user_id=%s and position_id=%s',(scope.owner_id,scope.scope.position_id)).fetchone()
                return {'officialJobId':row['official_job_id'] if row else None,'verificationRequired':not (recorded and request.read_mode=='recorded')}
        except (psycopg.errors.InsufficientPrivilege,psycopg.errors.NoDataFound):
            raise HrToolError('permission_expired','业务工具授权失效') from None

    def execute(self, worker_id, grant_id, token, request, *, official_observation=None):
        try:
            with self.repository._connection() as connection, connection.transaction():
                scope, identity = self._authorize(connection, worker_id, grant_id, token)
                from app.execution_relay.contracts_v6 import parse_v6_tool_request
                from app.execution_relay.contracts_v7 import parse_v7_tool_request
                parser=parse_v7_tool_request if identity['contractVersion']=='core_chat_collaboration_v7' else parse_v6_tool_request
                request=parser(request.model_dump(mode='json',by_alias=True))
                request_hash = digest(canonical(request.model_dump(mode='json', by_alias=True)))
                prior = connection.execute('select * from platform_hr.tool_operations_v6 where turn_id=%s and operation_id=%s',
                    (scope.turn_id, request.operation_id)).fetchone()
                if prior:
                    if prior['request_sha256'] != request_hash or prior['tool'] != request.tool:
                        raise HrToolError('idempotency_conflict', '相同操作编号不能更换内容')
                    return self._decode(prior)['reply']
                receipt_id = uuid5(scope.turn_id, str(request.operation_id))
                resource_kind = resource_id = version_ref = schema_id = None
                body = None
                if isinstance(request, HrReadContextRequest):
                    reply = self._read(connection, scope, request, receipt_id, official_observation=official_observation)
                    resource_kind, resource_id, version_ref = request.resource_kind, reply.resource_id, reply.version_ref
                    content_hash = reply.content_sha256
                elif isinstance(request, HrSubmitResultRequest):
                    body = request.result.model_dump(mode='json', by_alias=True)
                    self._validate_result(connection, scope, request.result)
                    content_hash, schema_id = digest(canonical(body)), request.result.schema_id
                    reply = HrSubmitResultReply(status='ok', tool=request.tool,
                        resultRef=HrResultRef(resultId=receipt_id, schemaId=schema_id, contentSha256=content_hash))
                elif isinstance(request, HrConfirmStandardRequest):
                    if self.calibration is None:
                        raise HrToolError('confirmation_required', '需要用户确认具体建议和条目')
                    reply = self.calibration.confirm(connection, scope, request)
                    content_hash = digest(canonical(reply.model_dump(mode='json', by_alias=True)))
                else:
                    raise ValueError('HR tool request invalid')
                wire = reply.model_dump(mode='json', by_alias=True)
                sealed = self.codec.seal_json(f'hr-tool:{receipt_id}', {'reply': wire, 'result': body})
                row = connection.execute('select * from platform_hr.record_tool_operation_v6(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (grant_id, worker_id, digest(token), receipt_id, request.operation_id, request.tool,
                     request_hash, content_hash, resource_kind, resource_id, version_ref, schema_id,
                     sealed.ciphertext, sealed.key_version)).fetchone()
                if body is not None and scope.input_result_refs is not None:
                    connection.execute("select platform_hr.record_result_scope_v7(%s,%s,%s,%s,%s)",
                        (grant_id,worker_id,digest(token),receipt_id,list(getattr(request.result,"position_candidate_ids",()))))
                return self._decode(row)['reply']
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.NoDataFound):
            raise HrToolError('permission_expired', '业务工具授权或所选材料已失效') from None
        except psycopg.errors.UniqueViolation:
            raise HrToolError('idempotency_conflict', '操作编号已用于其他内容') from None
        except psycopg.errors.SerializationFailure:
            raise HrToolError('version_conflict', '岗位标准已变化，请读取后再确认') from None

    def _read(self, connection, scope, request, receipt_id, *, official_observation=None):
        position = scope.scope.position_id
        resource_id = request.resource_id or position
        if request.resource_kind in {'official_position', 'confirmed_standard'} and (
            position is None or resource_id != position):
            raise HrToolError('scope_mismatch', '只能读取本轮所选岗位')
        if request.resource_kind=='candidate' and resource_id not in scope.scope.position_candidate_ids:
            raise HrToolError('scope_mismatch','候选人不在本轮所选范围')
        if request.resource_kind == 'material' and resource_id not in scope.scope.attachment_ids:
            raise HrToolError('scope_mismatch', '材料不在本轮授权范围内')
        if request.resource_kind=='result':
            reference=next((r for r in (scope.input_result_refs or ()) if r.result_id==resource_id),None)
            if reference is None:
                raise HrToolError('scope_mismatch','成果不在本次参考中')
            if request.version_ref not in (None,reference.content_sha256):
                raise HrToolError('version_conflict','成果引用版本已变化')
        # Recorded means the first successful read in this turn, even after an Attempt retry.
        if request.read_mode == 'recorded':
            recorded = connection.execute('select * from platform_hr.tool_operations_v6 where turn_id=%s '
                "and tool='hr.read_context' and resource_kind=%s and (%s::uuid is null or resource_id=%s) "
                'and (%s::text is null or version_ref=%s) order by created_at,receipt_id limit 1',
                (scope.turn_id, request.resource_kind, resource_id, resource_id, request.version_ref, request.version_ref)).fetchone()
            if recorded:
                prior = self._decode(recorded)['reply']
                return HrReadContextReply.model_validate_json(canonical({**prior, 'readId':str(receipt_id), 'verification':'recorded'}))
            if request.version_ref is not None and request.resource_kind!='result':
                raise HrToolError('source_unavailable', '本轮未记录所请求版本')
        status = 'current'
        if request.resource_kind == 'official_position':
            row = connection.execute('select v.* from platform_hr.positions p join platform_hr.official_position_versions v '
                'on v.official_position_version_id=p.current_official_version_id and v.owner_internal_user_id=p.owner_internal_user_id '
                'where p.owner_internal_user_id=%s and p.position_id=%s', (scope.owner_id, position)).fetchone()
            if row is None:
                raise HrToolError('source_unavailable', '该岗位没有可用官网快照')
            document = {key: value for key,value in row.items() if key not in {'owner_internal_user_id','client_request_id','evidence'}}
            expired = datetime.now(timezone.utc)-row['source_snapshot_at']>timedelta(hours=24) or row['official_status']=='suspected_inactive'
            if official_observation == {'verification':'unavailable'}:
                status = 'degraded'
                document['verification_warning'] = '官网使用前校验未完成；以下为平台最后有效快照，请勿视为已核实现行事实'
            elif official_observation is not None:
                from .official_verification import validate_observation
                document=validate_observation(official_observation,row['official_job_id'])
                status=document['verification']
            elif self.jd_verifier is not None:
                # Trusted registry client, never model-provided identity or fact fields.
                document = self.jd_verifier(row['official_job_id'])
                status = 'current' if document['verification']=='current' else 'degraded'
            elif expired:
                status = 'degraded'
                document['verification_warning'] = '官网快照超过使用前校验阈值；当前刷新不可用，以下为最后有效版本'
            version = str(document.get('registryVersion',row['source_version']))
        elif request.resource_kind == 'confirmed_standard':
            row = connection.execute('select p.current_context_version_id,to_jsonb(v) as standard '
                'from platform_hr.positions p left join platform_hr.position_context_versions v '
                'on v.context_version_id=p.current_context_version_id and v.owner_internal_user_id=p.owner_internal_user_id '
                'and v.position_id=p.position_id where p.position_id=%s and p.owner_internal_user_id=%s', (position,scope.owner_id)).fetchone()
            if not row:
                raise HrToolError('scope_mismatch', '岗位不存在')
            document = row['standard']
            if row['current_context_version_id'] is not None and (not document or document['state']!='confirmed'):
                raise HrToolError('version_conflict', '当前已确认标准的版本指针不一致')
            version = str(row['current_context_version_id'] or 'unconfirmed')
            document = document or {'state':'unconfirmed','modules':{},'message':'尚无共同确认标准'}
        elif request.resource_kind == 'candidate':
            row=connection.execute('select c.candidate_id,c.stable_name,c.facts,c.updated_at,pc.position_candidate_id '
                'from platform_hr.position_candidates pc join platform_hr.candidates c using(candidate_id,owner_internal_user_id) '
                "where pc.position_candidate_id=%s and pc.owner_internal_user_id=%s and pc.position_id=%s and pc.status='active'",(resource_id,scope.owner_id,position)).fetchone()
            if row is None: raise HrToolError('scope_mismatch','候选人不可用')
            documents=connection.execute("select document_id,attachment_id,version_number,content_sha256 from platform_hr.candidate_documents where candidate_id=%s and owner_internal_user_id=%s and status='active' and attachment_id=any(%s)",(row['candidate_id'],scope.owner_id,list(scope.scope.attachment_ids))).fetchall()
            feedback=connection.execute('select feedback_id,analysis_version_id,feedback_kind,conclusion_key,correction,reason,created_at from platform_hr.human_feedback where position_candidate_id=%s and owner_internal_user_id=%s order by created_at desc limit 20',(resource_id,scope.owner_id)).fetchall()
            document={**row,'documents':documents,'human_feedback':feedback}
            version=digest(canonical(document))
        elif request.resource_kind == 'result':
            row=connection.execute("select * from platform_hr.tool_operations_v6 where receipt_id=%s and owner_internal_user_id=%s and tool='hr.submit_result'",(resource_id,scope.owner_id)).fetchone()
            if row is None or row['schema_id']!=reference.schema_id or row['content_sha256']!=reference.content_sha256:
                raise HrToolError('result_unregistered','成果引用不可用')
            load_authorized_turn_scope(scope.owner_id,row['conversation_id'],row['turn_id'],connection=connection)
            document={'resultRef':reference.model_dump(mode='json',by_alias=True),'result':self._decode(row)['result']}
            version=reference.content_sha256
        elif request.resource_kind == 'material':
            row = connection.execute("select attachment_id,encode(sha256,'hex') as sha256,coalesce(detected_mime,declared_mime) as media_type,size_bytes from platform_attachments.attachments "
                'where attachment_id=%s and owner_internal_user_id=%s', (resource_id, scope.owner_id)).fetchone()
            if not row:
                raise HrToolError('scope_mismatch', '材料不可用')
            document = {**row, 'delivery':'使用本次执行的附件授权读取已下载文件；此记录不代表已读取文件正文'}
            version = row['sha256']
        else:
            row = connection.execute('select * from platform_hr.read_current_intelligence_bundle_v85()').fetchone()
            if not row or (resource_id is not None and resource_id != row['bundle_id']):
                raise HrToolError('source_unavailable', '没有可用的已发布情报')
            resource_id, version = row['bundle_id'], row['manifest_sha256']
            document = {key:row[key] for key in ('bundle_id','generated_at','source_coverage','aggregates','analysis','evidence_index')}
        content = canonical(document)
        return HrReadContextReply(status='ok', tool=request.tool, readId=receipt_id,
            resourceKind=request.resource_kind, resourceId=resource_id, versionRef=version,
            contentSha256=digest(content), verification=status, contentText=content)

    def _validate_result(self, connection, scope, result):
        for ref in result.source_refs:
            read = connection.execute('select content_sha256 from platform_hr.tool_operations_v6 '
                "where receipt_id=%s and turn_id=%s and tool='hr.read_context'", (ref.read_id, scope.turn_id)).fetchone()
            if read is None or read['content_sha256'] != ref.content_sha256:
                raise HrToolError('result_unregistered', '结果引用的读取记录不属于本轮')
        if isinstance(result,CandidateDeliverable):
            if scope.scope.position_id is None or not set(result.position_candidate_ids).issubset(scope.scope.position_candidate_ids):
                raise HrToolError('scope_mismatch','候选人成果不在本轮所选范围')
            for baseline in (*getattr(result,'baseline_refs',()),*getattr(result,'record_material_refs',())):
                read=connection.execute("select * from platform_hr.tool_operations_v6 where receipt_id=%s and turn_id=%s and tool='hr.read_context'",(baseline.read_id,scope.turn_id)).fetchone()
                expected_kind={'confirmed_standard':'confirmed_standard','official_position':'official_position','user_material':'material'}[baseline.kind]
                if read is None or read['resource_kind']!=expected_kind or read['content_sha256']!=baseline.content_sha256:
                    raise HrToolError('result_unregistered','基准没有对应的本轮读取证据')
                if baseline.kind=='confirmed_standard':
                    if read['version_ref']!=str(baseline.context_version_id):
                        raise HrToolError('version_conflict','岗位标准基准不匹配')
                    confirmed=connection.execute("select 1 from platform_hr.position_context_versions where context_version_id=%s and owner_internal_user_id=%s and position_id=%s and confirmed_by is not null and state in ('confirmed','superseded')",(baseline.context_version_id,scope.owner_id,scope.scope.position_id)).fetchone()
                    if confirmed is None: raise HrToolError('version_conflict','基准不是已确认岗位标准')
                elif baseline.kind=='official_position':
                    if baseline.position_id!=scope.scope.position_id or baseline.version_ref!=read['version_ref']:
                        raise HrToolError('scope_mismatch','官网基准与本轮岗位不匹配')
                elif baseline.attachment_id!=read['resource_id'] or baseline.attachment_id not in scope.scope.attachment_ids:
                    raise HrToolError('scope_mismatch','基准材料不在本轮范围')
            if isinstance(result,HrCandidateInterviewRecord) and result.plan_ref is not None:
                if result.plan_ref not in (scope.input_result_refs or ()):
                    raise HrToolError('scope_mismatch','面试方案必须列在本次参考中')
                read=connection.execute("select * from platform_hr.tool_operations_v6 where turn_id=%s and tool='hr.read_context' and resource_kind='result' and resource_id=%s and version_ref=%s order by created_at limit 1",(scope.turn_id,result.plan_ref.result_id,result.plan_ref.content_sha256)).fetchone()
                if read is None: raise HrToolError('result_unregistered','尚未读取本次引用的面试方案')
                plan=json.loads(self._decode(read)['reply']['contentText'])['result']
                if plan['positionCandidateIds']!=[str(x) for x in result.position_candidate_ids]:
                    raise HrToolError('scope_mismatch','面试记录与方案候选人不一致')
                if not {a.question_id for a in result.answers}.issubset({q['questionId'] for q in plan['questions']}):
                    raise HrToolError('scope_mismatch','记录问题不属于引用的面试方案')
        if isinstance(result, HrStandardProposal) and scope.scope.position_id is None:
            raise HrToolError('scope_mismatch', '长期标准建议需要先选择岗位')
        if isinstance(result, HrCandidateAnalysis):
            if not set(result.position_candidate_ids).issubset(scope.scope.position_candidate_ids):
                raise HrToolError('scope_mismatch', '候选人不在本轮所选范围')
            baseline = connection.execute('select 1 from platform_hr.position_context_versions where context_version_id=%s '
                "and owner_internal_user_id=%s and position_id=%s and state in ('confirmed','superseded') and confirmed_by is not null",
                (result.context_version_id, scope.owner_id, scope.scope.position_id)).fetchone()
            if baseline is None:
                raise HrToolError('version_conflict', '候选人分析的岗位标准版本无效')

    def conversation(self,owner_id,conversation_id):
        with self.repository._connection() as connection:
            row=connection.execute("select 1 from platform_control.conversations where conversation_id=%s and owner_internal_user_id=%s and direct_agent_id='hr-bot'",(conversation_id,owner_id)).fetchone()
            if row is None: raise HrToolError('scope_mismatch','对话不存在')
            turns=connection.execute("select t.turn_id,t.hr_input_context,p.title from platform_control.conversation_turns t "
                "left join platform_hr.positions p on p.position_id=(t.hr_input_context->'scope'->>'positionId')::uuid and p.owner_internal_user_id=%s "
                "where t.conversation_id=%s and t.hr_input_context is not null order by t.created_at desc limit 200",(owner_id,conversation_id)).fetchall()
            receipts=connection.execute("select receipt_id,turn_id,schema_id,content_sha256 from platform_hr.tool_operations_v6 where owner_internal_user_id=%s and conversation_id=%s and tool='hr.submit_result' order by created_at desc limit 200",(owner_id,conversation_id)).fetchall()
            reads=connection.execute('select r.turn_id,r.proof from platform_hr.knowledge_reads_v6 r join platform_control.conversation_turns t using(turn_id) where t.conversation_id=%s order by t.created_at,r.seq limit 1000',(conversation_id,)).fetchall()
            return {'knowledgeReads':[{'turnId':str(r['turn_id']),**r['proof']} for r in reads], 'turns':[{'turnId':str(t['turn_id']),'scope':t['hr_input_context']['scope'],'positionTitle':t['title']} for t in turns],
                'results':[{'turnId':str(r['turn_id']),'resultId':str(r['receipt_id']),'schemaId':r['schema_id'],'contentSha256':r['content_sha256']} for r in receipts]}

    def result(self, owner_id, result_id):
        with self.repository._connection() as connection:
            row = connection.execute('select * from platform_hr.tool_operations_v6 where receipt_id=%s '
                "and owner_internal_user_id=%s and tool='hr.submit_result'", (result_id,owner_id)).fetchone()
            if row is None:
                raise HrToolError('result_unregistered', '结果不存在')
            scope=load_authorized_turn_scope(owner_id,row['conversation_id'],row['turn_id'],connection=connection)
            value = self._decode(row)
            connection.execute('select platform_hr.record_result_presentation_v6(%s,%s)', (owner_id,result_id))
            candidate_ids=row.get('result_candidate_ids') if row.get('result_candidate_ids') is not None else list(scope.scope.position_candidate_ids)
            candidates=connection.execute('select c.stable_name from platform_hr.position_candidates pc join platform_hr.candidates c using(candidate_id,owner_internal_user_id) where pc.owner_internal_user_id=%s and pc.position_candidate_id=any(%s) order by c.stable_name',(owner_id,candidate_ids)).fetchall()
            reusable=connection.execute("select a.attachment_id from platform_attachments.attachments a where a.owner_internal_user_id=%s and a.attachment_id=any(%s) and (exists(select 1 from platform_hr.position_materials m where m.attachment_id=a.attachment_id and m.owner_internal_user_id=a.owner_internal_user_id and m.position_id=%s and m.active) or exists(select 1 from platform_hr.candidate_documents d join platform_hr.position_candidates pc using(candidate_id,owner_internal_user_id) where d.attachment_id=a.attachment_id and d.owner_internal_user_id=a.owner_internal_user_id and d.status='active' and pc.position_candidate_id=any(%s) and pc.status='active')) order by a.attachment_id",(owner_id,list(scope.scope.attachment_ids),scope.scope.position_id,candidate_ids)).fetchall()
            position=connection.execute('select title from platform_hr.positions where owner_internal_user_id=%s and position_id=%s',(owner_id,scope.scope.position_id)).fetchone() if scope.scope.position_id else None
            return {**value, 'candidateNames':[c['stable_name'] for c in candidates], 'positionTitle':position['title'] if position else None, 'positionCandidateIds':[str(x) for x in (row.get('result_candidate_ids') if row.get('result_candidate_ids') is not None else getattr(scope.scope,'position_candidate_ids',()))], 'candidateDerived':bool(row.get('candidate_derived') or scope.scope.position_candidate_ids), 'attachmentIds':[str(x['attachment_id']) for x in reusable], 'conversationId':str(row['conversation_id']), 'turnId':str(row['turn_id']), 'positionId':str(scope.scope.position_id) if scope.scope.position_id else None}
