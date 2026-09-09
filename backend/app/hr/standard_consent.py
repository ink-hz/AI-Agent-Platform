"""Explicit user intent attached to a submitted message, never supplied by a bot."""
import json
from uuid import UUID
from typing import Literal
from pydantic import Field, model_validator
from app.execution_relay.contracts_v6 import StrictValue, SHA256


class StandardConsent(StrictValue):
    proposal_result_id: UUID = Field(alias='proposalResultId')
    proposal_content_sha256: str = Field(alias='proposalContentSha256',pattern=SHA256)
    expected_context_version_id: UUID | None = Field(alias='expectedContextVersionId')
    selected_change_ids: tuple[str,...] = Field(alias='selectedChangeIds',min_length=1,max_length=8)

    body_reviewed: Literal[True] | None = Field(default=None,alias='bodyReviewed')

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.selected_change_ids)) != len(self.selected_change_ids):
            raise ValueError('duplicate standard selection')
        return self

    def accepts_text(self, text):
        return text == self.message_text() or (self.body_reviewed is None and text == self.channel_message_text())

    def channel_message_text(self):
        return f"/确认 {self.proposal_result_id} {','.join(self.selected_change_ids)}"

    def message_text(self):
        return f'确认所选的 {len(self.selected_change_ids)} 项岗位标准。' + ('已核对所选正文，仅保留岗位级标准，不含候选人个人信息或逐人评价。' if self.body_reviewed else '')


def normalize_consent(value):
    if value is None or isinstance(value,StandardConsent):
        return value
    return StandardConsent.model_validate_json(json.dumps(value))
