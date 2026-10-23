import hashlib
import subprocess

import pytest

from app.sdk_knowledge.git_source import GitSourceReader, SourceObservationError


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / 'SDK'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    git(repo, 'config', 'user.name', 'fixture')
    (repo / 'CMakeLists.txt').write_text('project(SDK VERSION 1.0.0)\n')
    (repo / 'same.txt').write_text('unchanged\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    base = git(repo, 'rev-parse', 'HEAD').decode().strip()
    (repo / 'CMakeLists.txt').write_text('project(SDK VERSION 1.0.1)\n')
    git(repo, 'commit', '-qam', 'next')
    target = git(repo, 'rev-parse', 'HEAD').decode().strip()
    reader = GitSourceReader(tmp_path, [{'repository': 'SDK', 'relative_path': 'SDK', 'commit': target}])
    return repo, reader, base, target


def test_exact_git_objects_and_hunks_ignore_uncommitted_worktree(source):
    repo, reader, base, target = source
    (repo / 'CMakeLists.txt').write_text('uncommitted private content\n')
    before = git(repo, 'status', '--porcelain=v1')
    result = reader.compare('SDK', base, target)
    assert result['base_commit'] == base and result['target_commit'] == target
    assert result['scope'] == 'tracked_git_objects'
    assert len(result['files']) == 1
    row = result['files'][0]
    assert row['path'] == 'SDK/CMakeLists.txt' and row['repo_path'] == 'CMakeLists.txt'
    assert row['old_sha256'] == hashlib.sha256(b'project(SDK VERSION 1.0.0)\n').hexdigest()
    assert row['new_sha256'] == hashlib.sha256(b'project(SDK VERSION 1.0.1)\n').hexdigest()
    assert row['hunks'] == [{'old_start': 1, 'old_lines': 1, 'new_start': 1, 'new_lines': 1}]
    assert '-project(SDK VERSION 1.0.0)' in row['diff']
    assert '+project(SDK VERSION 1.0.1)' in row['diff']
    assert 'private' not in str(result)
    assert reader.blob_sha256('SDK', target, 'SDK/same.txt') == hashlib.sha256(b'unchanged\n').hexdigest()
    assert reader.blob_sha256('SDK', target, 'SDK/missing.txt') is None
    assert git(repo, 'status', '--porcelain=v1') == before
    assert git(repo, 'rev-parse', 'HEAD').decode().strip() == target


@pytest.mark.parametrize('commit', ['HEAD', 'main', '-x', 'a' * 39, 'a' * 41, 'G' * 40])
def test_only_full_commit_ids_are_accepted(source, commit):
    _, reader, base, _ = source
    with pytest.raises(ValueError):
        reader.compare('SDK', base, commit)


def test_missing_commit_unknown_repository_and_unsafe_paths_fail_closed(source):
    _, reader, base, target = source
    with pytest.raises(SourceObservationError, match='source_commit_unavailable'):
        reader.compare('SDK', base, 'f' * 40)
    with pytest.raises(ValueError):
        reader.compare('other', base, target)
    for path in ('../secret', '/tmp/secret', 'SDK/../secret', 'SDK/a\\b', 'other/a', 'SDK/.git/config'):
        with pytest.raises(ValueError):
            reader.blob_sha256('SDK', target, path)


def test_add_delete_and_rename_are_explicit_and_binary_is_not_text(source):
    repo, reader, _, base = source
    (repo / 'CMakeLists.txt').rename(repo / 'renamed.txt')
    (repo / 'same.txt').unlink()
    (repo / 'sample.bin').write_bytes(b'\0\xffbinary')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'rename delete binary')
    target = git(repo, 'rev-parse', 'HEAD').decode().strip()
    rows = {row['repo_path']: row for row in reader.compare('SDK', base, target)['files']}
    assert rows['CMakeLists.txt']['status'] == 'deleted' and rows['CMakeLists.txt']['new_sha256'] is None
    assert rows['renamed.txt']['status'] == 'added' and rows['renamed.txt']['old_sha256'] is None
    assert rows['same.txt']['status'] == 'deleted'
    assert rows['sample.bin']['binary'] is True and rows['sample.bin']['hunks'] == []
    assert reader.compare('SDK', target, target)['files'] == []


def test_limits_and_checkout_symlinks_do_not_silently_reduce_scope(source, tmp_path):
    repo, _, base, _ = source
    (repo / 'large.txt').write_bytes(b'x' * (256 * 1024 + 1))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'large')
    target = git(repo, 'rev-parse', 'HEAD').decode().strip()
    reader = GitSourceReader(tmp_path, [{'repository': 'SDK', 'relative_path': 'SDK', 'commit': target}])
    with pytest.raises(SourceObservationError, match='source_blob_too_large'):
        reader.compare('SDK', base, target)
    linked = tmp_path / 'linked'
    linked.symlink_to(repo, target_is_directory=True)
    with pytest.raises(ValueError):
        GitSourceReader(tmp_path, [{'repository': 'SDK', 'relative_path': 'linked', 'commit': target}])


def test_every_git_command_disables_lazy_fetch_and_all_transport(source, monkeypatch):
    _, reader, base, target = source
    run = subprocess.run
    commands = []
    monkeypatch.setenv('GIT_NO_LAZY_FETCH', '0')
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'file:https')
    monkeypatch.setenv('GIT_DIR', '/unapproved/object/database')

    def guarded_run(command, **kwargs):
        commands.append(command)
        assert '--no-lazy-fetch' in command
        assert kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
        assert kwargs['env']['GIT_ALLOW_PROTOCOL'] == ''
        assert 'GIT_DIR' not in kwargs['env']
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', guarded_run)
    assert reader.compare('SDK', base, target)['files'][0]['hunks']
    assert reader.blob_sha256('SDK', target, 'SDK/same.txt')
    assert commands


@pytest.mark.parametrize('pollution', ['worktree_attributes', 'info_attributes', 'configured_attributes', 'color', 'driver'])
def test_text_hunks_and_direct_claims_ignore_local_rendering(source, tmp_path, pollution):
    from types import SimpleNamespace
    from app.sdk_knowledge.analysis import model_context
    from app.sdk_knowledge.service import KnowledgeService

    repo, reader, base, target = source
    page = {'page_id': 'KB-FIXTURE', 'claims': [{'claim_id': 'C1', 'evidence_ids': ['E1']}],
            'evidence': [{'evidence_id': 'E1', 'repository': 'SDK', 'commit': target,
                          'path': 'SDK/CMakeLists.txt', 'line_start': 1, 'line_end': 1,
                          'sha256': hashlib.sha256(b'project(SDK VERSION 1.0.1)\n').hexdigest()}]}
    corpus = SimpleNamespace(baseline_id='fixture', source_pages=lambda *_: [page])
    service = KnowledgeService(corpus, reader)
    expected = service.inspect_change('SDK', base, target)
    assert expected['affected_pages'][0]['changed_references'][0]['line_overlap'] is True
    assert model_context(expected)['affected_pages'][0]['direct_claim_ids'] == ['C1']
    if pollution == 'color':
        git(repo, 'config', 'color.ui', 'always')
    elif pollution == 'driver':
        (repo / '.gitattributes').write_text('CMakeLists.txt diff=fixture\n')
        git(repo, 'config', 'diff.fixture.binary', 'true')
        # Executable attributes/config remain disabled even while forcing text output.
        git(repo, 'config', 'diff.fixture.command', 'touch driver-started')
        git(repo, 'config', 'diff.fixture.textconv', 'touch textconv-started')
    else:
        attributes = {'worktree_attributes': repo / '.gitattributes',
                      'info_attributes': repo / '.git/info/attributes',
                      'configured_attributes': tmp_path / 'attributes'}[pollution]
        attributes.write_text('CMakeLists.txt -diff\n')
        if pollution == 'configured_attributes':
            git(repo, 'config', 'core.attributesFile', str(attributes))
    actual = service.inspect_change('SDK', base, target)
    assert actual['observation']['files'][0]['binary'] is False
    assert actual['observation']['files'][0]['hunks'] == expected['observation']['files'][0]['hunks']
    assert actual == expected
    assert model_context(actual)['affected_pages'][0]['direct_claim_ids'] == ['C1']
    assert not (repo / 'driver-started').exists()
    assert not (repo / 'textconv-started').exists()


@pytest.mark.parametrize('missing', ['commit', 'tree', 'blob'])
def test_missing_promisor_objects_do_not_fetch_or_change_repository(source, tmp_path, monkeypatch, missing):
    import os

    repo, reader, base, target = source
    # This helper only writes a local sentinel; it cannot make a network request.
    marker = tmp_path / 'transport-started'
    helper = tmp_path / 'git-remote-sdkfixture'
    helper.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
    helper.chmod(0o755)
    git(repo, 'config', 'extensions.partialClone', 'origin')
    git(repo, 'config', 'remote.origin.promisor', 'true')
    git(repo, 'config', 'remote.origin.partialCloneFilter', 'blob:none')
    git(repo, 'config', 'remote.origin.url', 'sdkfixture::local-sentinel')
    oid = {'commit': target,
           'tree': git(repo, 'rev-parse', target + '^{tree}').decode().strip(),
           'blob': git(repo, 'rev-parse', target + ':CMakeLists.txt').decode().strip()}[missing]
    (repo / '.git/objects' / oid[:2] / oid[2:]).unlink()

    def snapshot():
        return {str(p.relative_to(repo)): p.read_bytes() for p in (repo / '.git').rglob('*') if p.is_file()}

    before = snapshot()
    run = subprocess.run
    trace = tmp_path / 'git-trace'
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])

    def traced_run(command, **kwargs):
        # Keep an unsafe regression from ever starting transport, even in this local fixture.
        assert '--no-lazy-fetch' in command
        assert kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
        assert kwargs['env']['GIT_ALLOW_PROTOCOL'] == ''
        kwargs['env']['GIT_TRACE'] = str(trace)
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', traced_run)
    error = 'source_commit_unavailable' if missing == 'commit' else 'source_object_unavailable'
    with pytest.raises(SourceObservationError, match=error):
        reader.compare('SDK', base, target)
    with pytest.raises(SourceObservationError, match=error):
        reader.blob_sha256('SDK', target, 'SDK/CMakeLists.txt')
    assert not marker.exists()
    assert ' fetch ' not in trace.read_text()
    assert 'remote-sdkfixture' not in trace.read_text()
    assert snapshot() == before


def test_git_without_no_lazy_fetch_support_fails_without_retry(source, monkeypatch):
    _, reader, _, target = source
    commands = []

    def unsupported(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 129, b'', b'unknown option: --no-lazy-fetch')

    monkeypatch.setattr(subprocess, 'run', unsupported)
    with pytest.raises(SourceObservationError, match='source_commit_unavailable'):
        reader.blob_sha256('SDK', target, 'SDK/CMakeLists.txt')
    assert len(commands) == 1
    assert '--no-lazy-fetch' in commands[0]
