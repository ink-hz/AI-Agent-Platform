"""Existing authenticated HTTP/PG routes; no business-object mutation."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_attachment_erasure_production_canary import api

_FIXTURES = (api,)
PRODUCTION = Path(__file__).parents[2] / 'artifacts/2026-09-13-hr-launch/production'


def module():
    path = PRODUCTION / 'owner_preflight.py'
    assert path.is_file(), 'owner preflight entry missing'
    spec = importlib.util.spec_from_file_location('owner_preflight_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def config_file(tmp_path, config):
    path = tmp_path / 'owner.json'
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    return path


def counts(api):
    with api['db'].admin_connection() as c:
        return tuple(c.execute('select count(*) from platform_attachments.' + table).fetchone()[0]
                     for table in ('attachments', 'uploads', 'upload_write_attempts', 'processing_jobs', 'erasure_jobs'))


def test_real_owner_csrf_rejected_before_validated_empty_body(api, tmp_path):
    mod = module()
    before = counts(api)
    result = mod.preflight(config_file(tmp_path, api['config']), tmp_path / 'receipt.json',
                           run_id='6dc1b038-5c2b-4109-973c-2a45f230e4bc', client=api['client'])
    assert result['status'] == 'verified'
    assert result['owner_verified'] and result['csrf_verified']
    assert [r['status'] for r in result['requests']] == [200, 403, 422]
    assert counts(api) == before
    raw = (tmp_path / 'receipt.json').read_text()
    assert api['config']['session_cookie'] not in raw
    assert api['config']['csrf'] not in raw


def test_wrong_csrf_never_passes(api, tmp_path):
    mod = module()
    config = dict(api['config'], csrf='wrong-token')
    before = counts(api)
    result = mod.preflight(config_file(tmp_path, config), tmp_path / 'receipt.json',
                           run_id='6dc1b038-5c2b-4109-973c-2a45f230e4bc', client=api['client'])
    assert result['status'] == 'failed'
    assert result['csrf_verified'] is False
    assert counts(api) == before


def test_wrong_owner_stops_before_post(api, tmp_path):
    mod = module()
    config = dict(api['config'], owner_id='d2bb175d-c5b7-4d0b-a173-e62b07c9a166')
    result = mod.preflight(config_file(tmp_path, config), tmp_path / 'receipt.json',
                           run_id='6dc1b038-5c2b-4109-973c-2a45f230e4bc', client=api['client'])
    assert result['status'] == 'failed'
    assert len(result['requests']) == 1


def test_config_mode_rejected_without_http(tmp_path):
    mod = module()
    path = tmp_path / 'config.json'
    path.write_text('{}')
    path.chmod(0o644)
    result = mod.preflight(path, tmp_path / 'receipt.json',
                           run_id='6dc1b038-5c2b-4109-973c-2a45f230e4bc', client=None)
    assert result['status'] == 'failed'
    assert result['requests'] == []


def test_existing_invalid_body_shape(api):
    response = api['client'].request('POST', 'https://localhost/api/v1/attachments/uploads',
        headers={'Cookie': '__Host-platform_session=' + api['config']['session_cookie'],
                 'Origin': 'https://localhost', 'X-CSRF-Token': api['config']['csrf']}, json={})
    assert response.status_code == 422
    assert response.json() == {'detail': [{'loc': ['body', key], 'type': 'missing'} for key in sorted(module().MISSING)]}
