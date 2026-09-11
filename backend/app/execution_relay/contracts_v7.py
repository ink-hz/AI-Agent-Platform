"""Explicit HR result inputs and candidate deliverables; v6 remains immutable."""
from typing import Annotated, Any, Literal
from uuid import UUID
import hashlib
from pydantic import Field, TypeAdapter, model_validator
from . import contracts_v6 as v6
from .contracts_v6 import (StrictValue, SHA256, HrSourceRef, HrMethodStep, HrCandidateEvidence,
    HrAnalysisResult, HrStandardProposal, HrConfirmStandardRequest, HrToolFailure,
    HrConfirmStandardReply, _parse, _strict_wire)
from .contracts_v5 import (canonical_command_bytes, core_chat_command_document,
    RunHeartbeatPayloadV5, ErrorEventPayloadV5, CancelledEventPayloadV5,
    InterruptedEventPayloadV5, RawProgressPayloadV5)

CONTRACT_VERSION='core_chat_collaboration_v7'
ResultSchemaId=Literal['hr.analysis.v1','hr.standard-proposal.v1','hr.candidate-analysis.v1',
    'hr.candidate-analysis.v2','hr.candidate-interview-plan.v1',
    'hr.candidate-interview-record.v1','hr.candidate-outreach-draft.v1']

class HrResultRef(v6.HrResultRef):
    schema_id: ResultSchemaId=Field(alias='schemaId')

class CoreChatCommandV7(v6.CoreChatCommandV6):
    contract_version: Literal['core_chat_collaboration_v7']=Field(alias='contractVersion')
    input_result_refs: tuple[HrResultRef,...]=Field(alias='inputResultRefs',max_length=20)

    @model_validator(mode='after')
    def unique_inputs(self):
        if len({r.result_id for r in self.input_result_refs})!=len(self.input_result_refs):
            raise ValueError('duplicate result input')
        return self

class ResultEventPayloadV7(v6.ResultEventPayloadV6):
    result_refs: tuple[HrResultRef,...]=Field(alias='resultRefs',max_length=20)

class CoreChatEventV7(v6.CoreChatEventV6):
    contract_version: Literal['core_chat_collaboration_v7']=Field(alias='contractVersion')
    payload: RunHeartbeatPayloadV5|ResultEventPayloadV7|ErrorEventPayloadV5|CancelledEventPayloadV5|InterruptedEventPayloadV5|RawProgressPayloadV5

class ConfirmedBaseline(HrSourceRef):
    kind: Literal['confirmed_standard']
    context_version_id: UUID=Field(alias='contextVersionId')

class OfficialBaseline(HrSourceRef):
    kind: Literal['official_position']
    position_id: UUID=Field(alias='positionId')
    version_ref: str=Field(alias='versionRef',min_length=1,max_length=256)

class MaterialBaseline(HrSourceRef):
    kind: Literal['user_material']
    attachment_id: UUID=Field(alias='attachmentId')

BaselineRef=Annotated[ConfirmedBaseline|OfficialBaseline|MaterialBaseline,Field(discriminator='kind')]

class CandidateDeliverable(StrictValue):
    title: str=Field(min_length=1,max_length=256)
    markdown: str=Field(min_length=1,max_length=131072)
    source_refs: tuple[HrSourceRef,...]=Field(alias='sourceRefs',max_length=100)
    method_steps: tuple[HrMethodStep,...]=Field(alias='methodSteps',max_length=100)
    position_candidate_ids: tuple[UUID,...]=Field(alias='positionCandidateIds',min_length=1,max_length=10)

    @model_validator(mode='after')
    def unique_candidates(self):
        if len(set(self.position_candidate_ids))!=len(self.position_candidate_ids):
            raise ValueError('duplicate candidate')
        return self

class HrCandidateAnalysisV2(CandidateDeliverable):
    schema_id: Literal['hr.candidate-analysis.v2']=Field(alias='schemaId')
    baseline_refs: tuple[BaselineRef,...]=Field(alias='baselineRefs',min_length=1,max_length=10)
    evidence: tuple[HrCandidateEvidence,...]=Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def evidence_scope(self):
        if any(e.position_candidate_id not in self.position_candidate_ids for e in self.evidence):
            raise ValueError('candidate evidence scope invalid')
        return self

class SingleCandidateDeliverable(CandidateDeliverable):
    position_candidate_ids: tuple[UUID,...]=Field(alias='positionCandidateIds',min_length=1,max_length=1)

class InterviewQuestion(StrictValue):
    question_id: str=Field(alias='questionId',pattern=v6.IDENTIFIER)
    dimension: str=Field(min_length=1,max_length=256)
    question: str=Field(min_length=1,max_length=4096)
    follow_ups: tuple[str,...]=Field(alias='followUps',min_length=1,max_length=10)
    verification_goal: str=Field(alias='verificationGoal',min_length=1,max_length=4096)
    strong_evidence: str=Field(alias='strongEvidence',min_length=1,max_length=4096)
    ordinary_answer: str=Field(alias='ordinaryAnswer',min_length=1,max_length=4096)
    risk_signals: str=Field(alias='riskSignals',min_length=1,max_length=4096)
    technical_checks: str=Field(alias='technicalChecks',min_length=1,max_length=4096)

class HrCandidateInterviewPlan(SingleCandidateDeliverable):
    schema_id: Literal['hr.candidate-interview-plan.v1']=Field(alias='schemaId')
    baseline_refs: tuple[BaselineRef,...]=Field(alias='baselineRefs',min_length=1,max_length=10)
    objectives: tuple[str,...]=Field(min_length=1,max_length=20)
    questions: tuple[InterviewQuestion,...]=Field(min_length=1,max_length=50)
    record_template: str=Field(alias='recordTemplate',min_length=1,max_length=16384)

    @model_validator(mode='after')
    def unique_questions(self):
        if len({q.question_id for q in self.questions})!=len(self.questions):
            raise ValueError('duplicate question')
        return self

class InterviewAnswer(StrictValue):
    question_id: str=Field(alias='questionId',pattern=v6.IDENTIFIER)
    answer_evidence: str=Field(alias='answerEvidence',min_length=1,max_length=8192)
    assessment: str=Field(min_length=1,max_length=8192)
    unknowns: str=Field(min_length=1,max_length=8192)
    next_verification: str=Field(alias='nextVerification',min_length=1,max_length=8192)

class HrCandidateInterviewRecord(SingleCandidateDeliverable):
    schema_id: Literal['hr.candidate-interview-record.v1']=Field(alias='schemaId')
    plan_ref: HrResultRef|None=Field(alias='planRef')
    record_material_refs: tuple[MaterialBaseline,...]=Field(alias='recordMaterialRefs',min_length=1,max_length=10)
    answers: tuple[InterviewAnswer,...]=Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def valid_plan(self):
        if self.plan_ref and self.plan_ref.schema_id!='hr.candidate-interview-plan.v1':
            raise ValueError('interview plan type invalid')
        return self

class HrCandidateOutreachDraft(SingleCandidateDeliverable):
    schema_id: Literal['hr.candidate-outreach-draft.v1']=Field(alias='schemaId')
    purpose: str=Field(min_length=1,max_length=4096)
    draft: str=Field(min_length=1,max_length=16384)
    unverified_promises: tuple[str,...]=Field(alias='unverifiedPromises',max_length=20)
    source_refs: tuple[HrSourceRef,...]=Field(alias='sourceRefs',min_length=1,max_length=100)

HrResult=Annotated[HrAnalysisResult|HrStandardProposal|HrCandidateAnalysisV2|HrCandidateInterviewPlan|HrCandidateInterviewRecord|HrCandidateOutreachDraft,Field(discriminator='schema_id')]

class HrReadContextRequest(v6.HrReadContextRequest):
    resource_kind: Literal['official_position','confirmed_standard','material','intelligence','candidate','result']=Field(alias='resourceKind')
    @model_validator(mode='after')
    def result_identity(self):
        if self.resource_kind=='result' and self.resource_id is None:
            raise ValueError('result identity required')
        return self

class HrSubmitResultRequest(v6.HrSubmitResultRequest):
    result: HrResult

class HrReadContextReply(v6.HrReadContextReply):
    resource_kind: Literal['official_position','confirmed_standard','material','intelligence','candidate','result']=Field(alias='resourceKind')

class HrSubmitResultReply(v6.HrSubmitResultReply):
    result_ref: HrResultRef=Field(alias='resultRef')

HrToolRequest=Annotated[HrReadContextRequest|HrSubmitResultRequest|HrConfirmStandardRequest,Field(discriminator='tool')]
_TOOL_ADAPTER=TypeAdapter(HrToolRequest)


def core_chat_context_hash(command):
    wire=command.model_dump(mode='json',by_alias=True)
    return hashlib.sha256(canonical_command_bytes({key:wire[key] for key in
        ('prompt','scope','rolePackage','methodSelection','inputResultRefs')})).hexdigest()


def core_chat_command_hash(command):
    document=core_chat_command_document(command)
    wire=command.model_dump(mode='json',by_alias=True)
    for key in ('scope','rolePackage','methodSelection','toolCapabilities','inputResultRefs'):
        document[key]=wire[key]
    return hashlib.sha256(canonical_command_bytes(document)).hexdigest()


def parse_v7_command(value):
    command=_parse(value,CoreChatCommandV7.model_validate_json,'command')
    if core_chat_context_hash(command)!=command.context_hash or core_chat_command_hash(command)!=command.command_hash:
        raise ValueError('v7 command hash invalid')
    return command


def parse_v7_event(value):
    return _parse(value,CoreChatEventV7.model_validate_json,'event')


def parse_v7_tool_request(value):
    return _parse(value,_TOOL_ADAPTER.validate_json,'tool request')


def contract_schemas():
    schemas={'command.schema.json':CoreChatCommandV7.model_json_schema(by_alias=True),
        'callback.schema.json':CoreChatEventV7.model_json_schema(by_alias=True),
        'business-tools.schema.json':_TOOL_ADAPTER.json_schema(by_alias=True)}
    definitions=schemas['business-tools.schema.json']['$defs']
    for reply in (HrToolFailure,HrReadContextReply,HrSubmitResultReply,HrConfirmStandardReply):
        schema=reply.model_json_schema(by_alias=True)
        definitions.update(schema.pop('$defs',{}));definitions[reply.__name__]=schema
    def normalize(node):
        if isinstance(node,dict):
            if node.get('type')=='object' and 'properties' in node: node['required']=list(node['properties'])
            if node.get('format')=='uuid': node['pattern']=v6.UUID_WIRE.pattern.replace('\\Z','$')
            node.pop('default',None)
            for child in node.values(): normalize(child)
        elif isinstance(node,list):
            for child in node: normalize(child)
    for name,schema in schemas.items():
        normalize(schema)
        schema['$schema']='https://json-schema.org/draft/2020-12/schema'
        schema['$id']=f'https://agent.orbbec.com.cn/contracts/hr-execution/v7/{name}'
    return schemas
