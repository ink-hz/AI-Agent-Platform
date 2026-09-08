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

    @staticmethod
    def _authorize(connection, worker_id, grant_id, token):
        if not isinstance(token, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
            raise HrToolError('permission_expired', '业务工具授权失效')
        value = connection.execute('select platform_hr.authorize_tool_grant_v6(%s,%s,%s) as scope',
            (grant_id, worker_id, digest(token))).fetchone()['scope']
        owner, conversation, turn = (UUID(value[key]) for key in ('ownerId','conversationId','turnId'))
        allowed = connection.execute("select allowed from platform_control.resolve_agent_use_decision_v41(%s,'hr-bot')", (owner,)).fetchone()
        if allowed is None or allowed['allowed'] is not True:
            raise HrToolError('permission_expired', 'HR 使用权限已失效')
        return load_authorized_turn_scope(owner, conversation, turn, connection=connection), value

    def _decode(self, row):
        return self.codec.unseal_json(f"hr-tool:{row['receipt_id']}", SealedContent(row['payload_ciphertext'], row['encryption_key_version']))

    def execute(self, worker_id, grant_id, token, request):
        try:
            with self.repository._connection() as connection, connection.transaction():
                scope, identity = self._authorize(connection, worker_id, grant_id, token)
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
                    reply = self._read(connection, scope, request, receipt_id)
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
                return self._decode(row)['reply']
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.NoDataFound):
            raise HrToolError('permission_expired', '业务工具授权或所选材料已失效') from None
        except psycopg.errors.UniqueViolation:
            raise HrToolError('idempotency_conflict', '操作编号已用于其他内容') from None
        except psycopg.errors.SerializationFailure:
            raise HrToolError('version_conflict', '岗位标准已变化，请读取后再确认') from None

    def _read(self, connection, scope, request, receipt_id):
        position = scope.scope.position_id
        resource_id = request.resource_id or position
        if request.resource_kind in {'official_position', 'confirmed_standard'} and (
            position is None or resource_id != position):
            raise HrToolError('scope_mismatch', '只能读取本轮所选岗位')
        if request.resource_kind == 'material' and resource_id not in scope.scope.attachment_ids:
            raise HrToolError('scope_mismatch', '材料不在本轮授权范围内')
        # Recorded means the first successful read in this turn, even after an Attempt retry.
        if request.read_mode == 'recorded':
            recorded = connection.execute('select * from platform_hr.tool_operations_v6 where turn_id=%s '
                "and tool='hr.read_context' and resource_kind=%s and (%s::uuid is null or resource_id=%s) "
                'and (%s::text is null or version_ref=%s) order by created_at,receipt_id limit 1',
                (scope.turn_id, request.resource_kind, resource_id, resource_id, request.version_ref, request.version_ref)).fetchone()
            if recorded:
                prior = self._decode(recorded)['reply']
                return HrReadContextReply.model_validate_json(canonical({**prior, 'readId':str(receipt_id), 'verification':'recorded'}))
            if request.version_ref is not None:
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
            if self.jd_verifier is not None:
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

    def result(self, owner_id, result_id):
        with self.repository._connection() as connection:
            row = connection.execute('select * from platform_hr.tool_operations_v6 where receipt_id=%s '
                "and owner_internal_user_id=%s and tool='hr.submit_result'", (result_id,owner_id)).fetchone()
            if row is None:
                raise HrToolError('result_unregistered', '结果不存在')
            load_authorized_turn_scope(owner_id,row['conversation_id'],row['turn_id'],connection=connection)
            value = self._decode(row)
            connection.execute('select platform_hr.record_result_presentation_v6(%s,%s)', (owner_id,result_id))
            return {**value, 'conversationId':str(row['conversation_id']), 'turnId':str(row['turn_id'])}
