"""New HR input/baseline wire boundaries, no provider calls."""
import importlib.util
import json
from copy import deepcopy
import pytest
from test_execution_contract_v6 import command as old_command, IDS


def module():
    assert importlib.util.find_spec('app.execution_relay.contracts_v7'), 'v7 contract is not implemented'
    from app.execution_relay import contracts_v7
    return contracts_v7


def command():
    v7=module(); value=old_command()
    value.update(contractVersion='core_chat_collaboration_v7',inputResultRefs=[
        {'resultId':IDS[8],'schemaId':'hr.analysis.v1','contentSha256':'a'*64}])
    model=v7.CoreChatCommandV7.model_validate_json(json.dumps(value))
    value['contextHash']=v7.core_chat_context_hash(model)
    model=v7.CoreChatCommandV7.model_validate_json(json.dumps(value))
    value['commandHash']=v7.core_chat_command_hash(model)
    return value


def test_input_references_are_required_and_immutable():
    v7=module(); value=command()
    assert v7.parse_v7_command(value).input_result_refs
    for mutation in ('missing','hash','duplicate'):
        changed=deepcopy(value)
        if mutation=='missing': changed.pop('inputResultRefs')
        if mutation=='hash': changed['inputResultRefs'][0]['contentSha256']='b'*64
        if mutation=='duplicate': changed['inputResultRefs']*=2
        with pytest.raises(ValueError): v7.parse_v7_command(changed)
    from app.execution_relay.core_contract import parse_core_command
    assert parse_core_command(value).contract_version=='core_chat_collaboration_v7'
    assert parse_core_command(old_command()).contract_version=='core_chat_collaboration_v6'


def test_candidate_baseline_nonempty_and_typed():
    v7=module()
    result={'schemaId':'hr.candidate-analysis.v2','title':'证据分析','markdown':'待验证',
        'positionCandidateIds':[IDS[8]],'evidence':[], 'sourceRefs':[],'methodSteps':[],
        'baselineRefs':[{'kind':'user_material','attachmentId':IDS[9],'readId':IDS[10],'contentSha256':'a'*64}]}
    # Evidence is mandatory; a baseline alone is not a completed analysis.
    result['evidence']=[{'positionCandidateId':IDS[8],'dimension':'项目贡献','evidence':'简历自述',
        'assessment':'uncertain','verification':'核验具体贡献'}]
    wire={'tool':'hr.submit_result','operationId':IDS[0],'result':result}
    assert v7.parse_v7_tool_request(wire).result.baseline_refs
    for baseline in ([],[None],[{**result['baselineRefs'][0],'contextVersionId':IDS[0]}]):
        with pytest.raises(ValueError):
            v7.parse_v7_tool_request({**wire,'result':{**result,'baselineRefs':baseline}})
