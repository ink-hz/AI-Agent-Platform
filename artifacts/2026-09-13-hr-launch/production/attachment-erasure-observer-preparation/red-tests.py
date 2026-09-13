"""Observer classification tests; S3 boundary substituted, no production calls."""
import importlib.util
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).parents[2]


def module(monkeypatch):
    folder = ROOT / 'artifacts/2026-09-13-hr-launch/production'
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('observer', folder / 'attachment_erasure_observe.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class S3:
    def __init__(self, versions=(), error=None):
        self.versions = list(versions)
        self.error = error
        self.calls = []

    def list_object_versions(self, **kwargs):
        self.calls.append(kwargs)
        return {'IsTruncated': False, 'Versions': [
            {'Key': 'owned', 'VersionId': value} for value in self.versions
        ]}

    def head_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise ClientError({'Error': {'Code': self.error}, 'ResponseMetadata': {'HTTPStatusCode': 403}}, 'HeadObject')
        if kwargs.get('VersionId') in self.versions:
            return {'ContentLength': 10, 'VersionId': kwargs['VersionId']}
        raise ClientError({'Error': {'Code': '404'}, 'ResponseMetadata': {'HTTPStatusCode': 404}}, 'HeadObject')


def test_deleted_key_does_not_hide_old_version(monkeypatch):
    observer = module(monkeypatch)
    assert observer.verify_absence(S3(['old']), 'bucket', {'owned': ['old']})['status'] == 'remaining'


def test_head_forbidden_is_unknown(monkeypatch):
    observer = module(monkeypatch)
    with pytest.raises(observer.Unknown):
        observer.verify_absence(S3(error='AccessDenied'), 'bucket', {'owned': ['old']})


def test_complete_enumeration_and_exact_version_absent(monkeypatch):
    observer = module(monkeypatch)
    result = observer.verify_absence(S3(), 'bucket', {'owned': ['old']})
    assert result['status'] == 'absent'
    assert result['checked_versions'] == 1


def test_pagination_must_finish_and_filter_exact_key(monkeypatch):
    observer = module(monkeypatch)

    class Paged(S3):
        def list_object_versions(self, **kwargs):
            self.calls.append(kwargs)
            if 'KeyMarker' not in kwargs:
                return {'IsTruncated': True, 'Versions': [{'Key': 'owned-other', 'VersionId': 'unrelated'}], 'NextKeyMarker': 'owned', 'NextVersionIdMarker': 'v1'}
            return {'IsTruncated': False, 'Versions': [{'Key': 'owned', 'VersionId': 'v2'}]}

    client = Paged()
    assert observer.inventory(client, 'bucket', 'owned') == ['v2']
    assert client.calls[1]['VersionIdMarker'] == 'v1'
    assert all(call['Prefix'] == 'owned' for call in client.calls)


def test_missing_pagination_cursor_unknown(monkeypatch):
    observer = module(monkeypatch)
    client = S3()
    client.list_object_versions = lambda **_: {'IsTruncated': True}
    with pytest.raises(observer.Unknown):
        observer.inventory(client, 'bucket', 'owned')


def test_missing_reference_set_unknown(monkeypatch):
    observer = module(monkeypatch)
    with pytest.raises(observer.Unknown):
        observer.verify_absence(S3(), 'bucket', {})
