import json
from pathlib import Path
import pytest

BASE = Path(__file__).parents[2]

def test_runtime_contracts_exist_and_validate_documented_cases():
    from app.hr_agent.types import validate_contract, HrAgentProblem
    for case in json.loads((BASE/'docs/superpowers/specs/hr-cloud-loop/contract-examples.json').read_text()):
        if case['valid']:
            assert validate_contract(case['definition'],case['value']) == case['value']
        else:
            with pytest.raises(HrAgentProblem):
                validate_contract(case['definition'],case['value'])

def test_rejects_model_owner_field():
    from app.hr_agent.types import validate_tool_arguments, HrAgentProblem
    with pytest.raises(HrAgentProblem):
        validate_tool_arguments('ask_user', {'question':'hello','options':[], 'owner_id':'untrusted'})
    with pytest.raises(HrAgentProblem):
        validate_tool_arguments('bash', {})

def test_runtime_contract_bundle_matches_documentation():
    assert json.loads((BASE/'backend/app/hr_agent/contracts.schema.json').read_text()) == json.loads((BASE/'docs/superpowers/specs/hr-cloud-loop/contracts.schema.json').read_text())

def test_internal_types_reject_invalid_event_and_usage():
    from app.hr_agent.types import ModelEvent, Usage
    with pytest.raises(ValueError): ModelEvent('arbitrary',{})
    with pytest.raises(ValueError): Usage(None,-1,0,'reported')

def test_read_range_does_not_claim_reverse_coverage():
    from app.hr_agent.types import validate_contract,HrAgentProblem
    cases=json.loads((BASE/'docs/superpowers/specs/hr-cloud-loop/contract-examples.json').read_text())
    case=next(c for c in cases if c['valid'] and c['definition']=='ResourceText')
    value=case['value'].copy()
    value['offset']=20; value['end']=10
    with pytest.raises(HrAgentProblem): validate_contract('ResourceText',value)

@pytest.mark.parametrize('timestamp',['2026-02-30T12:00:00Z','2026-13-01T12:00:00Z','2026-01-01T25:00:00Z','2026-01-01','2026-01-01T12:00:00','2026-01-01T12:00:00+25:00'])
def test_runtime_rejects_invalid_rfc3339_calendar(timestamp):
    from app.hr_agent.types import validate_contract,HrAgentProblem
    cases=json.loads((BASE/'docs/superpowers/specs/hr-cloud-loop/contract-examples.json').read_text())
    value=next(c['value'].copy() for c in cases if c['valid'] and c['definition']=='Event')
    value['at']=timestamp
    with pytest.raises(HrAgentProblem): validate_contract('Event',value)

@pytest.mark.parametrize('timestamp',['2024-02-29T23:59:59Z','2026-01-01t12:00:00z','2026-01-01T12:00:00.123+08:00'])
def test_runtime_accepts_valid_rfc3339_calendar(timestamp):
    from app.hr_agent.types import validate_contract
    cases=json.loads((BASE/'docs/superpowers/specs/hr-cloud-loop/contract-examples.json').read_text())
    value=next(c['value'].copy() for c in cases if c['valid'] and c['definition']=='Event')
    value['at']=timestamp
    assert validate_contract('Event',value)['at']==timestamp
