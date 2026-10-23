"""Real subprocess checks for the runner-local receipt CLI."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / 'backend/tools/sdk_ci_evidence.py'


@pytest.fixture
def run_files(tmp_path):
    (tmp_path / 'package.zip').write_bytes(b'package')
    (tmp_path / 'junit.xml').write_text('<testsuite><testcase name="ok"/></testsuite>')
    return tmp_path, {'workspace_root': str(tmp_path), 'candidate_digest': 'a' * 64,
                      'run_id': 'run-1', 'attempt': 1, 'exit_code': 0, 'artifacts': ['package.zip'],
                      'suites': {'unit': {'path': 'junit.xml', 'minimum_executed_cases': 1}}}


def invoke(root, data, output=None):
    source = root / 'run.json'
    source.write_text(json.dumps(data))
    output = output or root / 'receipt.json'
    result = subprocess.run([sys.executable, str(CLI), '--input', str(source),
                             '--output', str(output)], cwd=REPO, capture_output=True, text=True, check=False)
    return result, output


def test_success_receipt_digest(run_files):
    root, data = run_files
    result, output = invoke(root, data)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(output.read_text())
    digest = receipt.pop('receipt_sha256')
    assert digest == hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(',', ':'),
                                               ensure_ascii=False).encode()).hexdigest()
    assert receipt['artifacts'][0]['sha256'] == hashlib.sha256(b'package').hexdigest()
    assert receipt['state'] == 'local_verified'
    assert receipt['promotion_eligible'] is False


@pytest.mark.parametrize('case,reason', [('suites', 'suite_missing'), ('artifacts', 'artifact_missing'),
                                        ('zero', 'suite_empty'), ('empty', 'artifact_empty')])
def test_blocked_receipt_retained(run_files, case, reason):
    root, data = run_files
    if case in ('suites', 'artifacts'):
        data[case] = {} if case == 'suites' else []
    elif case == 'zero':
        (root / 'junit.xml').write_text('<testsuite/>')
    else:
        (root / 'package.zip').write_bytes(b'')
    result, output = invoke(root, data)
    assert result.returncode == 2, result.stderr
    receipt = json.loads(output.read_text())
    assert reason in receipt['reasons']
    assert receipt['state'] == 'blocked'
    assert receipt['promotion_eligible'] is False


@pytest.mark.parametrize('case', ['extra', 'missing', 'negative', 'bool', 'relative', 'escape',
                                 'suite_extra', 'suite_type', 'json'])
def test_invalid_removes_previous_success(run_files, case):
    root, data = run_files
    _, output = invoke(root, data)
    assert output.exists()
    if case == 'extra':
        data['extra'] = 1
    elif case == 'missing':
        del data['attempt']
    elif case == 'negative':
        data['exit_code'] = -1
    elif case == 'bool':
        data['attempt'] = True
    elif case == 'relative':
        data['workspace_root'] = '.'
    elif case == 'escape':
        data['artifacts'] = ['../outside.zip']
    elif case == 'suite_extra':
        data['suites']['unit']['extra'] = 1
    elif case == 'suite_type':
        data['suites'] = []
    if case == 'json':
        (root / 'run.json').write_text('{')
        result = subprocess.run([sys.executable, str(CLI), '--input', str(root / 'run.json'),
                                 '--output', str(output)], cwd=REPO, capture_output=True, text=True, check=False)
    else:
        result, output = invoke(root, data)
    assert result.returncode == 3, result.stderr
    assert not output.exists()
    assert str(root) not in result.stderr


def test_same_input_output_rejected(run_files):
    root, data = run_files
    result, output = invoke(root, data, root / 'run.json')
    assert result.returncode == 3
    assert json.loads(output.read_text()) == data


def test_output_parent_must_exist(run_files):
    root, data = run_files
    result, output = invoke(root, data, root / 'missing' / 'receipt.json')
    assert result.returncode == 3
    assert not output.parent.exists()
