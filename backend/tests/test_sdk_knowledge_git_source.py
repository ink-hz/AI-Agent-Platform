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
