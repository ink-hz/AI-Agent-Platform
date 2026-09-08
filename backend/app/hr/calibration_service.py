"""Confirm real user-selected changes into the existing full context version chain."""
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from app.execution_relay.content_crypto import SealedContent
from app.execution_relay.contracts_v6 import HrConfirmStandardReply, HrStandardProposal
from app.agent_brain.conversation_repository import message_subject
from .standard_consent import normalize_consent
from .tool_service import HrToolError, canonical


class HrCalibrationService:
    def __init__(self, tools):
        self.tools = tools

    def confirm(self, connection, scope, request):
        # A tool cannot grant consent. The current user message must carry the
        # precise selection submitted through the authenticated conversation API.
        row = connection.execute('select m.* from platform_control.conversation_turns t '
            'join platform_control.conversation_messages m on m.message_id=t.user_message_id '
            'where t.turn_id=%s and m.message_id=%s and m.conversation_id=%s and m.role=\'user\'',
            (scope.turn_id,request.confirmation_message_id,scope.conversation_id)).fetchone()
        if row is None:
            raise HrToolError('confirmation_required','需要当前用户消息明确确认建议条目')
        message = self.tools.codec.unseal_json(message_subject(scope.conversation_id,row['message_id']),
            SealedContent(bytes(row['content_ciphertext']),row['encryption_key_version']))
        consent = normalize_consent(message.get('standard_consent'))
        if consent is None or not consent.accepts_text(message['text']) or any((
            consent.proposal_result_id != request.proposal_result_id,
            consent.proposal_content_sha256 != request.proposal_content_sha256,
            consent.expected_context_version_id != request.expected_context_version_id,
            consent.selected_change_ids != request.selected_change_ids,
        )):
            raise HrToolError('confirmation_required','用户尚未确认这份建议及具体条目')
        proposal = connection.execute('select o.* from platform_hr.tool_operations_v6 o '
            'join platform_hr.result_presentations_v6 shown on shown.receipt_id=o.receipt_id '
            'and shown.owner_internal_user_id=o.owner_internal_user_id '
            'join platform_hr.position_task_records r on r.turn_id=o.turn_id '
            'and r.owner_internal_user_id=o.owner_internal_user_id '
            'where o.receipt_id=%s and o.owner_internal_user_id=%s and o.conversation_id=%s '
            "and o.schema_id='hr.standard-proposal.v1' and o.tool='hr.submit_result' and r.position_id=%s "
            'and shown.presented_at<=%s and o.created_at<=%s',
            (request.proposal_result_id,scope.owner_id,scope.conversation_id,scope.scope.position_id,row['created_at'],row['created_at'])).fetchone()
        if proposal is None or proposal['content_sha256'] != request.proposal_content_sha256:
            raise HrToolError('confirmation_required','需要先查看本岗位的具体建议版本')
        value = HrStandardProposal.model_validate_json(canonical(self.tools._decode(proposal)['result']))
        changes = {change.change_id:change for change in value.changes}
        if value.base_context_version_id != request.expected_context_version_id or not set(request.selected_change_ids).issubset(changes):
            raise HrToolError('version_conflict','建议版本或确认条目已变化')
        position = connection.execute('select current_context_version_id,current_official_version_id from platform_hr.positions '
            'where position_id=%s and owner_internal_user_id=%s', (scope.scope.position_id,scope.owner_id)).fetchone()
        if position is None or position['current_context_version_id'] != request.expected_context_version_id:
            raise HrToolError('version_conflict','当前标准已有新版本，请重新查看建议')
        draft_id=uuid5(request.proposal_result_id,'confirmed-selection:'+str(request.operation_id))
        selected=[changes[item] for item in request.selected_change_ids]
        # Preserve original source turn and all inherited baseline modules. The
        # existing confirmation function owns version numbering and metadata.
        draft=connection.execute('select * from platform_hr.create_context_draft_v69(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (draft_id,scope.owner_id,scope.scope.position_id,draft_id,value.base_context_version_id,
             position['current_official_version_id'],Jsonb({item.module:{'markdown':item.markdown} for item in selected}),
             value.title,scope.conversation_id,proposal['turn_id'],None,[], 'hr-bot',None,scope.owner_id)).fetchone()
        confirmed=connection.execute('select * from platform_hr.confirm_context_modules_v69(%s,%s,%s,%s,%s,%s,%s,%s)',
            (scope.owner_id,scope.scope.position_id,draft_id,uuid5(scope.turn_id,str(request.operation_id)),
             request.expected_context_version_id,draft['row_version'],[item.module for item in selected],scope.owner_id)).fetchone()
        return HrConfirmStandardReply(status='ok',tool='hr.confirm_standard',
            contextVersionId=confirmed['context_version_id'],confirmedBy=confirmed['confirmed_by'],
            confirmedAt=confirmed['confirmed_at'],selectedChangeIds=request.selected_change_ids)
