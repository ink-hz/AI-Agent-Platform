import hashlib
import json
import os

import pytest

from app.sdk_workbench.ci_evidence import CiEvidenceError, verify_ci_run


def run(tmp_path, **changes):
    args = {'candidate_digest': 'a' * 64, 'run_id': 'job-1', 'attempt': 1,
                'exit_code': 0, 'artifacts': ['sdk.tgz'], 'suites': {'unit': ('unit.xml', 1)}}
    args.update(changes)
    return verify_ci_run(tmp_path, **args)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / 'sdk.tgz').write_bytes(b'real artifact')
    (tmp_path / 'unit.xml').write_text('<testsuite><testcase name="a"/></testsuite>')
    return tmp_path


def test_local_verified_receipt(workspace):
    (workspace / 'integration.xml').write_text('<testsuites><testsuite tests="999"><testcase name="b"/></testsuite></testsuites>')
    report = run(workspace, suites={'unit': ('unit.xml', 1), 'integration': ('integration.xml', 1)})
    assert report['state'] == 'local_verified'
    assert report['promotion_eligible'] is False
    assert report['trust_state'] == 'runner_local_unattested'
    assert [s['total'] for s in report['suites']] == [1, 1]
    assert report['artifacts'][0]['sha256'] == hashlib.sha256(b'real artifact').hexdigest()
    digest = report.pop('receipt_sha256')
    assert digest == hashlib.sha256(json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


@pytest.mark.parametrize(('change', 'reason'), [
    ({'exit_code': 1}, 'process_failed'),
    ({'suites': {}}, 'suite_missing'),
    ({'artifacts': ['absent']}, 'artifact_missing'),
    ({'suites': {'unit': ('absent', 1)}}, 'suite_missing'),
    ({'suites': {'unit': ('unit.xml', 2)}}, 'cases_below_minimum'),
])
def test_blocked_inputs(workspace, change, reason):
    report = run(workspace, **change)
    assert report['state'] == 'blocked'
    assert reason in report['reasons']


@pytest.mark.parametrize(('xml', 'reason'), [
    ('<testsuite/>', 'suite_empty'),
    ('<testsuite><testcase><failure/></testcase></testsuite>', 'tests_failed'),
    ('<testsuite><testcase><error/></testcase></testsuite>', 'tests_failed'),
    ('<testsuite><testcase><skipped/></testcase></testsuite>', 'tests_skipped'),
])
def test_actual_cases_determine_result(workspace, xml, reason):
    (workspace / 'unit.xml').write_text(xml)
    assert reason in run(workspace)['reasons']


def test_empty_artifact(workspace):
    (workspace / 'sdk.tgz').write_bytes(b'')
    assert 'artifact_empty' in run(workspace)['reasons']


@pytest.mark.parametrize('path', ['../escape', '/tmp/escape'])
def test_outside_path(workspace, path):
    with pytest.raises(CiEvidenceError) as error:
        run(workspace, artifacts=[path])
    assert error.value.code == 'path_outside_workspace'


def test_symlink_rejected(workspace):
    (workspace / 'link').symlink_to(workspace / 'sdk.tgz')
    with pytest.raises(CiEvidenceError):
        run(workspace, artifacts=['link'])


@pytest.mark.parametrize('xml', ['broken', '<!DOCTYPE testsuite><testsuite/>', '<other/>'])
def test_invalid_xml(workspace, xml):
    (workspace / 'unit.xml').write_text(xml)
    with pytest.raises(CiEvidenceError) as error:
        run(workspace)
    assert error.value.code == 'junit_invalid'


@pytest.mark.parametrize('change', [{'attempt': True}, {'attempt': 0}, {'candidate_digest': 'bad'}, {'run_id': '../bad'}, {'exit_code': False}, {'suites': {'unit': ('unit.xml', -1)}}])
def test_invalid_input(workspace, change):
    with pytest.raises(CiEvidenceError) as error:
        run(workspace, **change)
    assert error.value.code == 'input_invalid'


def test_oversized_xml(workspace):
    (workspace / 'unit.xml').write_bytes(b' ' * (10 * 1024 * 1024 + 1))
    with pytest.raises(CiEvidenceError) as error:
        run(workspace)
    assert error.value.code == 'junit_invalid'


def test_nonregular_file(workspace):
    with pytest.raises(CiEvidenceError) as error:
        run(workspace, artifacts=['.'])
    assert error.value.code == 'input_invalid'


def test_parent_symlink_rejected(workspace):
    (workspace / 'linkdir').symlink_to(workspace, target_is_directory=True)
    with pytest.raises(CiEvidenceError):
        run(workspace, artifacts=['linkdir/sdk.tgz'])


@pytest.mark.parametrize(('changes', 'field'), [
    ({'artifacts': ['sdk\x00.tgz']}, 'artifacts[0]'),
    ({'suites': {'unit': ('unit\x00.xml', 1)}}, 'suites.unit.path'),
])
def test_nul_path_is_input_invalid(workspace, changes, field):
    with pytest.raises(CiEvidenceError) as error:
        run(workspace, **changes)
    assert error.value.code == 'input_invalid'
    assert error.value.field == field


@pytest.mark.parametrize('encoding', ['unknown-ci-encoding', 'UTF-7'])
def test_unsupported_xml_encoding_is_junit_invalid(workspace, encoding):
    (workspace / 'unit.xml').write_bytes(
        f'<?xml version="1.0" encoding="{encoding}"?><testsuite><testcase/></testsuite>'.encode()
    )
    with pytest.raises(CiEvidenceError) as error:
        run(workspace)
    assert error.value.code == 'junit_invalid'
    assert error.value.field == 'suites.unit.path'


def test_suites_cannot_reuse_report_path(workspace):
    with pytest.raises(CiEvidenceError) as error:
        run(workspace, suites={'unit': ('unit.xml', 1), 'integration': ('./unit.xml', 1)})
    assert error.value.code == 'input_invalid'
    assert error.value.field == 'suites.integration.path'


def test_suites_cannot_reuse_hardlinked_report(workspace):
    os.link(workspace / 'unit.xml', workspace / 'integration.xml')
    with pytest.raises(CiEvidenceError) as error:
        run(workspace, suites={'unit': ('unit.xml', 1), 'integration': ('integration.xml', 1)})
    assert error.value.code == 'input_invalid'
    assert error.value.field == 'suites.integration.path'


@pytest.mark.parametrize('extra', [
    '<error message="setup failed"/>', '<failure/>', '<skipped/>',
    '<properties><error/></properties>',
    '<system-out><failure/></system-out>',
    '<testcase><testcase/></testcase>',
])
def test_result_nodes_in_invalid_structure_are_rejected(workspace, extra):
    (workspace / 'unit.xml').write_text(
        f'<testsuite><testcase name="ok"/>{extra}</testsuite>'
    )
    with pytest.raises(CiEvidenceError) as error:
        run(workspace)
    assert error.value.code == 'junit_invalid'
    assert error.value.field == 'suites.unit.path'


@pytest.mark.parametrize(('attribute', 'reason'), [
    ('errors', 'tests_failed'), ('failures', 'tests_failed'), ('skipped', 'tests_skipped'),
])
def test_aggregate_failure_attributes_block(workspace, attribute, reason):
    (workspace / 'unit.xml').write_text(
        f'<testsuites {attribute}="1"><testsuite><testcase name="ok"/></testsuite></testsuites>'
    )
    report = run(workspace)
    assert report['state'] == 'blocked'
    assert reason in report['reasons']


def test_benign_junit_metadata_is_accepted(workspace):
    (workspace / 'unit.xml').write_text(
        '<testsuites errors="0"><testsuite failures="0" skipped="0">'
        '<properties><property name="platform" value="local"/></properties>'
        '<testcase name="ok"><system-out>hello</system-out></testcase>'
        '<system-out>hello</system-out><system-err>diagnostic</system-err>'
        '</testsuite></testsuites>'
    )
    assert run(workspace)['state'] == 'local_verified'


@pytest.mark.parametrize('change', [
    {'artifacts': ['missing-other']},
    {'suites': {'unit': ('missing-other.xml', 1)}},
    {'suites': {'unit': ('missing.xml', 2)}},
])
def test_missing_configuration_changes_receipt(workspace, change):
    base = {'artifacts': ['missing'], 'suites': {'unit': ('missing.xml', 1)}}
    first = run(workspace, **base)
    second = run(workspace, **(base | change))
    assert first['receipt_sha256'] != second['receipt_sha256']
    assert first['requested_configuration_sha256'] != second['requested_configuration_sha256']
