import os

import pytest

from app import local_secrets
from app.local_secrets import SecretFileUnavailable, read_secret_file


def _secret(tmp_path, value: bytes = b"secret\n", *, mode: int = 0o600):
    path = tmp_path / "credential"
    path.write_bytes(value)
    path.chmod(mode)
    return path


def test_reads_current_user_private_regular_file(tmp_path):
    path = _secret(tmp_path)

    assert read_secret_file(str(path)) == "secret"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o666])
def test_rejects_group_or_other_access(tmp_path, mode):
    path = _secret(tmp_path, mode=mode)

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        read_secret_file(str(path))


def test_rejects_relative_path(tmp_path, monkeypatch):
    path = _secret(tmp_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        read_secret_file(path.name)


def test_rejects_symbolic_link(tmp_path):
    target = _secret(tmp_path)
    link = tmp_path / "credential-link"
    link.symlink_to(target)

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        read_secret_file(str(link))


def test_rejects_non_regular_file(tmp_path):
    directory = tmp_path / "credential-directory"
    directory.mkdir(mode=0o700)

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        read_secret_file(str(directory))


def test_rejects_file_owned_by_another_user(tmp_path, monkeypatch):
    path = _secret(tmp_path)
    owner = os.getuid()
    monkeypatch.setattr(local_secrets.os, "getuid", lambda: owner + 1)

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        read_secret_file(str(path))


@pytest.mark.parametrize("value", [b"", b" \n\t"])
def test_rejects_empty_value(tmp_path, value):
    path = _secret(tmp_path, value)

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        read_secret_file(str(path))


def test_rejects_value_larger_than_limit(tmp_path):
    path = _secret(tmp_path, b"x" * 17)

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable"):
        read_secret_file(str(path), max_bytes=16)


def test_rejects_invalid_utf8_without_leaking_content(tmp_path):
    path = _secret(tmp_path, b"\xffprivate")

    with pytest.raises(SecretFileUnavailable, match="secret file unavailable") as error:
        read_secret_file(str(path))

    assert "private" not in str(error.value)
