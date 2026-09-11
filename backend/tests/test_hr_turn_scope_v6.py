from uuid import UUID

import pytest
from app.agent_brain.conversation_models import ConversationTurnSubmission, normalize_turn_submission
from app.agent_brain.conversation_routes import ConversationTextBody
from app.execution_relay.contracts_v6 import HrTurnScope

POSITION = UUID('00000000-0000-4000-8000-000000000001')
ATTACHMENT = UUID('00000000-0000-4000-8000-000000000002')


def test_submission_keeps_exact_hr_scope_when_normalized():
    scope = HrTurnScope(positionId=POSITION, positionCandidateIds=(), attachmentIds=())
    submission = ConversationTurnSubmission('实际用户要求', hr_scope=scope)
    assert normalize_turn_submission(submission).hr_scope == scope


def test_api_accepts_scope_for_each_message_without_conversation_binding():
    body = ConversationTextBody.model_validate({'text': '继续分析', 'scope': {
        'positionId': str(POSITION), 'positionCandidateIds': [], 'attachmentIds': []}})
    assert body.submission().hr_scope.position_id == POSITION


def test_scope_must_include_explicit_active_attachments():
    scope = HrTurnScope(positionId=POSITION, positionCandidateIds=(), attachmentIds=())
    with pytest.raises(ValueError):
        ConversationTurnSubmission('分析', (ATTACHMENT,), (ATTACHMENT,), hr_scope=scope)
