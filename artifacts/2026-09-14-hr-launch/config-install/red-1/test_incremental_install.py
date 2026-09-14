"""Local causal tests; synthetic bytes, no Docker/SSH/model or production claims."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('incremental_install', Path(__file__).with_name('incremental_install.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Host:
    def __init__(self, fail=None, exists=False):
        self.events = []
        self.fail = fail
        self.exists = exists
        self.old = b'old secret and knowledge unchanged'
    def call(self, op, **kwargs):
        self.events.append(op)
        if op == self.fail:
            raise RuntimeError('synthetic private error must not escape')
        if op == 'verify_inputs':
            return {'image': m.IMAGE, 'knowledge': m.KNOWLEDGE, 'configuration': 'a'*64}
        if op == 'exists':
            return self.exists
        if op == 'verify_installed':
            return {'bytes_equal': True, 'runtime_loaded': True, 'personal_materials': False}
        return None


def test_prepare_publishes_only_after_copy_and_actual_runtime_verification():
    h = Host()
    receipt = m.install(h, 'a'*32)
    assert h.events == ['verify_inputs', 'exists', 'reserve', 'copy_generation', 'copy_volume', 'verify_installed', 'publish']
    assert receipt['status'] == 'prepared_not_started'
    assert receipt['schema_106_verified'] is False
    assert h.old == b'old secret and knowledge unchanged'

@pytest.mark.parametrize('failure', ['copy_generation', 'copy_volume', 'verify_installed', 'publish'])
def test_partial_failure_never_deletes_or_activates_old_or_new(failure):
    h = Host(fail=failure)
    with pytest.raises(m.InstallError, match='installation_incomplete'):
        m.install(h, 'b'*32)
    assert 'failure_receipt' in h.events
    assert not any(x in h.events for x in ('start', 'delete', 'switch_current'))
    if failure != 'publish':
        assert 'publish' not in h.events
    assert h.old == b'old secret and knowledge unchanged'


def test_existing_target_is_never_adopted_or_deleted():
    h = Host(exists=True)
    with pytest.raises(m.InstallError, match='target_exists'):
        m.install(h, 'c'*32)
    assert h.events == ['verify_inputs', 'exists']


def test_bad_verification_cannot_publish():
    h = Host()
    orig = h.call
    def call(op, **kwargs):
        result = orig(op, **kwargs)
        return {'bytes_equal': True, 'runtime_loaded': False, 'personal_materials': False} if op == 'verify_installed' else result
    h.call = call
    with pytest.raises(m.InstallError):
        m.install(h, 'd'*32)
    assert 'publish' not in h.events


def test_policy_recomputed_without_broadening_or_altering_provider():
    profiles = {'provider': {'model': 'synthetic'}, 'budget': {'limit': 2}, 'diagnostic': {'enabled': False}}
    policy = {'version': 1, 'scope': 'public-only', 'authorization_ref': 'synthetic authorization', 'configuration_sha256': 'old', 'usage_accounting': 'conservative_estimate_not_invoice'}
    updated = m.update_policy(profiles, policy)
    assert updated['configuration_sha256'] == m.digest(m.canonical(profiles))
    assert policy['configuration_sha256'] == 'old'
    assert updated['scope'] == 'public-only'
    policy['scope'] = 'all'
    with pytest.raises(m.InstallError):
        m.update_policy(profiles, policy)
