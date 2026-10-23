import base64
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[2] / 'deploy/cloud/prepare-fae-partner-secrets.py'


def test_preparation_is_private_purpose_separated_and_does_not_rotate(tmp_path):
    assert SCRIPT.is_file(), 'partner key preparation helper missing'
    result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    snapshots = {}
    keys = []
    for name, purpose in [('partner-encryption-keyring', 'partner-provider-encryption'), ('partner-hmac-keyring', 'partner-provider-lookup-hmac')]:
        path = tmp_path / name
        snapshots[name] = path.read_bytes()
        assert path.stat().st_mode & 0o777 == 0o600
        payload = json.loads(path.read_text())
        assert payload['purpose'] == purpose
        key = base64.b64decode(payload['keys']['1'])
        assert len(key) == 32
        keys.append(key)
        assert payload['keys']['1'] not in result.stdout + result.stderr
    assert keys[0] != keys[1]
    assert subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True).returncode == 0
    assert snapshots == {name: (tmp_path / name).read_bytes() for name in snapshots}


def test_preparation_refuses_symlink_and_wrong_purpose(tmp_path):
    assert SCRIPT.is_file(), 'partner key preparation helper missing'
    target = tmp_path / 'target'; target.write_text('private'); target.chmod(0o600)
    path = tmp_path / 'partner-encryption-keyring'; path.symlink_to(target)
    assert subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True).returncode != 0
    assert target.read_text() == 'private'
    path.unlink(); path.write_text('{"purpose":"provider-encryption"}'); path.chmod(0o600)
    assert subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True).returncode != 0
