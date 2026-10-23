"""Local runner evidence validation; this receipt grants no release authority.

V1 receipts include the additive requested_configuration_sha256 audit field.
It binds normalized artifact paths and suite names, paths and minimums even
when files are absent; evidence records retain their original field contract.
"""

import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree

MAX_XML_BYTES = 10 * 1024 * 1024


class CiEvidenceError(ValueError):
    def __init__(self, code: str, field: str):
        self.code = code
        self.field = field
        super().__init__(f'{code}: {field}')


def _invalid(field):
    raise CiEvidenceError('input_invalid', field)


def _file(root, value, field):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        _invalid(field)
    relative = Path(value)
    path = root / relative
    if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(root):
        raise CiEvidenceError('path_outside_workspace', field)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            _invalid(field)
    if path.exists() and not path.is_file():
        _invalid(field)
    return path, relative.as_posix()


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()



def _validate_junit(xml, field, reasons):
    allowed_parents = {
        'testsuites': {'testsuites'},
        'testsuite': {'testsuites', 'testsuite'},
        'testcase': {'testsuite'},
        'failure': {'testcase'}, 'error': {'testcase'}, 'skipped': {'testcase'},
        'properties': {'testsuites', 'testsuite', 'testcase'},
        'property': {'properties'},
        'system-out': {'testsuites', 'testsuite', 'testcase'},
        'system-err': {'testsuites', 'testsuite', 'testcase'},
    }
    for parent in xml.iter():
        for child in parent:
            if parent.tag not in allowed_parents.get(child.tag, set()):
                raise CiEvidenceError('junit_invalid', field)
        if parent.tag in ('testsuite', 'testsuites'):
            for attribute, reason in (('errors', 'tests_failed'),
                                      ('failures', 'tests_failed'),
                                      ('skipped', 'tests_skipped')):
                value = parent.get(attribute, '0')
                if not re.fullmatch(r'[0-9]+', value):
                    raise CiEvidenceError('junit_invalid', field)
                if value.strip('0'):
                    reasons.add(reason)


def verify_ci_run(root: Path, *, candidate_digest: str, run_id: str, attempt: int,
                  exit_code: int, artifacts: list[str],
                  suites: dict[str, tuple[str, int]]) -> dict:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        _invalid('root')
    root = root.resolve()
    if not isinstance(candidate_digest, str) or not re.fullmatch('[0-9a-f]{64}', candidate_digest):
        _invalid('candidate_digest')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', run_id):
        _invalid('run_id')
    if type(attempt) is not int or attempt < 1:
        _invalid('attempt')
    if type(exit_code) is not int:
        _invalid('exit_code')
    if not isinstance(artifacts, list):
        _invalid('artifacts')
    if not isinstance(suites, dict):
        _invalid('suites')
    reasons = set()
    if exit_code:
        reasons.add('process_failed')
    artifact_records = []
    requested_artifacts = []
    requested_suites = []
    if not artifacts:
        reasons.add('artifact_missing')
    for index, value in enumerate(artifacts):
        path, relative = _file(root, value, f'artifacts[{index}]')
        requested_artifacts.append(relative)
        if not path.exists():
            reasons.add('artifact_missing')
            continue
        size = path.stat().st_size
        if not size:
            reasons.add('artifact_empty')
        artifact_records.append({'path': relative, 'size': size, 'sha256': _sha(path)})
    suite_records = []
    report_paths = set()
    report_files = set()
    if not suites:
        reasons.add('suite_missing')
    for name, config in suites.items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name):
            _invalid('suites.name')
        if not isinstance(config, tuple) or len(config) != 2:
            _invalid(f'suites.{name}')
        value, minimum = config
        if type(minimum) is not int or minimum < 1:
            _invalid(f'suites.{name}.minimum_executed_cases')
        field = f'suites.{name}.path'
        path, relative = _file(root, value, field)
        requested_suites.append({'name': name, 'path': relative,
                                 'minimum_executed_cases': minimum})
        report_path = path.resolve()
        if report_path in report_paths:
            _invalid(field)
        report_paths.add(report_path)
        if not path.exists():
            reasons.add('suite_missing')
            continue
        stat = path.stat()
        file_identity = (stat.st_dev, stat.st_ino)
        if file_identity in report_files:
            _invalid(field)
        report_files.add(file_identity)
        with path.open('rb') as stream:
            data = stream.read(MAX_XML_BYTES + 1)
        if len(data) > MAX_XML_BYTES or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper() or b'\x00' in data:
            raise CiEvidenceError('junit_invalid', field)
        try:
            xml = ElementTree.fromstring(data)
        except (ElementTree.ParseError, LookupError, ValueError) as exc:
            raise CiEvidenceError('junit_invalid', field) from exc
        if xml.tag not in ('testsuite', 'testsuites'):
            raise CiEvidenceError('junit_invalid', field)
        _validate_junit(xml, field, reasons)
        cases = list(xml.iter('testcase'))
        failed = sum(any(child.tag in ('failure', 'error') for child in case) for case in cases)
        skipped = sum(not any(child.tag in ('failure', 'error') for child in case)
                      and any(child.tag == 'skipped' for child in case) for case in cases)
        total = len(cases)
        if not total:
            reasons.add('suite_empty')
        if total - skipped < minimum:
            reasons.add('cases_below_minimum')
        if failed:
            reasons.add('tests_failed')
        if skipped:
            reasons.add('tests_skipped')
        suite_records.append({'name': name, 'path': relative, 'report_sha256': hashlib.sha256(data).hexdigest(),
                                  'total': total, 'passed': total - failed - skipped, 'failed': failed,
                                  'skipped': skipped, 'minimum_executed_cases': minimum})
    report = {'schema_version': 'sdk-ci-evidence-v1', 'candidate_digest': candidate_digest,
                  'run_id': run_id, 'attempt': attempt, 'exit_code': exit_code,
                  'artifacts': sorted(artifact_records, key=lambda item: item['path']),
                  'suites': sorted(suite_records, key=lambda item: item['name']),
                  'state': 'blocked' if reasons else 'local_verified',
                  'trust_state': 'runner_local_unattested', 'reasons': sorted(reasons), 'promotion_eligible': False}
    requested = {'artifacts': sorted(requested_artifacts),
                 'suites': sorted(requested_suites, key=lambda item: item['name'])}
    report['requested_configuration_sha256'] = hashlib.sha256(json.dumps(
        requested, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
    report['receipt_sha256'] = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
    return report
