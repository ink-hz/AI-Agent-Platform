"""Observe approved local Git objects without consulting or changing worktrees."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

_COMMIT = re.compile(r'[0-9a-f]{40}\Z')
_HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', re.MULTILINE)
_MAX_BLOB = 256 * 1024
_MAX_DIFF = 128 * 1024
_MAX_FILES = 50
_DIFF_OPTIONS = ('--no-ext-diff', '--no-textconv', '--no-renames', '--no-color',
                 '--diff-algorithm=myers', '--no-indent-heuristic', '--inter-hunk-context=0',
                 '--ignore-submodules=none', '--no-relative')


class SourceObservationError(ValueError):
    """Stable error codes, never raw Git stderr or local paths."""


def _commit(value: str) -> str:
    if not isinstance(value, str) or not _COMMIT.fullmatch(value):
        raise ValueError('source_commit_invalid')
    return value


def _path(value: str, *, allow_dot: bool = False) -> str:
    if allow_dot and value == '.':
        return value
    if (not isinstance(value, str) or not value or '\\' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts
            or '.git' in PurePosixPath(value).parts or str(PurePosixPath(value)) != value):
        raise ValueError('source_path_invalid')
    return value


def _safe_directory(path: Path) -> Path:
    path = path.absolute()
    if '..' in path.parts:
        raise ValueError('source_root_invalid')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink() or not current.is_dir():
            raise ValueError('source_root_invalid')
    return path


class GitSourceReader:
    def __init__(self, source_root: Path, repositories: list[dict]):
        root = _safe_directory(Path(source_root))
        self._repositories: dict[str, tuple[Path, str]] = {}
        for row in repositories:
            name, relative = row['repository'], _path(row['relative_path'], allow_dot=True)
            if not isinstance(name, str) or not name or name in self._repositories:
                raise ValueError('source_repository_invalid')
            _commit(row['commit'])
            self._repositories[name] = (_safe_directory(root / relative), relative)
        if not self._repositories:
            raise ValueError('source_repository_invalid')

    def _repo(self, repository: str) -> tuple[Path, str]:
        if not isinstance(repository, str) or repository not in self._repositories:
            raise ValueError('source_repository_invalid')
        return self._repositories[repository]

    def _git(self, repository: str, *args: str) -> bytes:
        root, _ = self._repo(repository)
        # Environment overrides must not redirect the approved object database.
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update({'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C',
                    'GIT_LITERAL_PATHSPECS': '1', 'GIT_NO_REPLACE_OBJECTS': '1',
                    'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': ''})
        try:
            result = subprocess.run(
                # The explicit option also fails closed on Git versions that do not support it.
                ['git', '--no-pager', '--no-lazy-fetch', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                 '-c', 'core.quotePath=false', '-C', str(root), *args],
                env=env, capture_output=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SourceObservationError('source_observation_unavailable') from None
        if result.returncode:
            raise SourceObservationError('source_object_unavailable')
        return result.stdout

    def _require_commit(self, repository: str, commit: str) -> None:
        _commit(commit)
        try:
            kind = self._git(repository, 'cat-file', '-t', commit).strip()
        except SourceObservationError:
            raise SourceObservationError('source_commit_unavailable') from None
        if kind != b'commit':
            raise SourceObservationError('source_commit_unavailable')

    def _blob(self, repository: str, commit: str, path: str) -> bytes | None:
        raw = self._git(repository, 'ls-tree', '-z', commit, '--', path)
        if not raw:
            return None
        try:
            metadata, actual_path = raw.removesuffix(b'\0').split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split(' ')
            if actual_path.decode('utf-8') != path:
                raise ValueError
        except (ValueError, UnicodeError):
            raise SourceObservationError('source_tree_invalid') from None
        if mode not in {'100644', '100755'} or kind != 'blob':
            raise SourceObservationError('source_path_type_unsupported')
        size = int(self._git(repository, 'cat-file', '-s', oid).strip())
        if size > _MAX_BLOB:
            raise SourceObservationError('source_blob_too_large')
        return self._git(repository, 'cat-file', 'blob', oid)

    def _repo_path(self, repository: str, path: str) -> str:
        _, prefix = self._repo(repository)
        _path(path)
        if prefix == '.':
            return path
        if not path.startswith(prefix + '/'):
            raise ValueError('source_path_invalid')
        return _path(path[len(prefix) + 1:])

    def blob_sha256(self, repository: str, commit: str, path: str) -> str | None:
        self._require_commit(repository, commit)
        data = self._blob(repository, commit, self._repo_path(repository, path))
        return None if data is None else hashlib.sha256(data).hexdigest()

    def compare(self, repository: str, base_commit: str, target_commit: str) -> dict:
        _, prefix = self._repo(repository)
        self._require_commit(repository, base_commit)
        self._require_commit(repository, target_commit)
        raw = self._git(repository, 'diff', *_DIFF_OPTIONS,
                        '--name-status', '-z', base_commit, target_commit, '--')
        if len(raw) > 256 * 1024:
            raise SourceObservationError('source_change_too_large')
        fields = raw.removesuffix(b'\0').split(b'\0') if raw else []
        if len(fields) % 2:
            raise SourceObservationError('source_diff_invalid')
        if len(fields) // 2 > _MAX_FILES:
            raise SourceObservationError('source_change_too_large')
        files, total = [], 0
        for index in range(0, len(fields), 2):
            try:
                status, path = fields[index].decode('ascii'), _path(fields[index + 1].decode('utf-8'))
            except (ValueError, UnicodeError):
                raise SourceObservationError('source_diff_invalid') from None
            if status not in {'A', 'M', 'D'}:
                raise SourceObservationError('source_path_type_unsupported')
            old, new = self._blob(repository, base_commit, path), self._blob(repository, target_commit, path)
            binary = any(b'\0' in data for data in (old, new) if data is not None)
            # Blob bytes determine binary status; local attributes cannot hide text hunks.
            text_options = () if binary else ('--text',)
            diff = self._git(repository, 'diff', *_DIFF_OPTIONS, *text_options,
                             '--unified=0', base_commit, target_commit, '--', path)
            total += len(diff)
            if total > _MAX_DIFF:
                raise SourceObservationError('source_change_too_large')
            try:
                text = diff.decode('utf-8')
            except UnicodeError:
                binary, text = True, ''
            hunks = [] if binary else [
                {'old_start': int(m[1]), 'old_lines': int(m[2] or 1),
                 'new_start': int(m[3]), 'new_lines': int(m[4] or 1)} for m in _HUNK.finditer(text)
            ]
            files.append({'path': path if prefix == '.' else f'{prefix}/{path}', 'repo_path': path,
                          'status': {'A': 'added', 'M': 'modified', 'D': 'deleted'}[status],
                          'old_sha256': None if old is None else hashlib.sha256(old).hexdigest(),
                          'new_sha256': None if new is None else hashlib.sha256(new).hexdigest(),
                          'binary': binary, 'hunks': hunks, 'diff': '' if binary else text})
        return {'repository': repository, 'base_commit': base_commit, 'target_commit': target_commit,
                'scope': 'tracked_git_objects', 'files': files}
