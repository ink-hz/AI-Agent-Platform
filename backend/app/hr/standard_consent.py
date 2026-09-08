"""Explicit user intent attached to a submitted message, never supplied by a bot."""
import json
from uuid import UUID
from pydantic import Field, model_validator
from app.execution_relay.contracts_v6 import StrictValue, SHA256


class StandardConsent(StrictValue):
    proposal_result_id: UUID = Field(alias='proposalResultId')
    proposal_content_sha256: str = Field(alias='proposalContentSha256',pattern=SHA256)
    expected_context_version_id: UUID | None = Field(alias='expectedContextVersionId')
    selected_change_ids: tuple[str,...] = Field(alias='selectedChangeIds',min_length=1,max_length=8)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.selected_change_ids)) != len(self.selected_change_ids):
            raise ValueError('duplicate standard selection')
        return self

    def message_text(self):
        return f'确认所选的 {len(self.selected_change_ids)} 项岗位标准。'


def normalize_consent(value):
    if value is None or isinstance(value,StandardConsent):
        return value
    return StandardConsent.model_validate_json(json.dumps(value))
