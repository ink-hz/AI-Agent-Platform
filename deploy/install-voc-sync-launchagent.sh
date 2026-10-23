#!/bin/bash
set -euo pipefail
umask 077
repo_root=$(cd "$(dirname "$0")/.." && pwd -P)
label=com.orbbec.ai-agent-platform-voc-sync
target="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$HOME/Library/LaunchAgents"
/usr/bin/python3 - "$repo_root" "$target" <<'PY'
import os,plistlib,sys,tempfile
from pathlib import Path
repo,target=map(Path,sys.argv[1:])
template=repo/'deploy/com.orbbec.ai-agent-platform-voc-sync.plist'
logs=Path.home()/'Library/Logs/OrbbecAI-Agent-Platform/voc-sync'
logs.mkdir(mode=0o700,parents=True,exist_ok=True)
payload=plistlib.loads(template.read_bytes())
def render(value):
    if isinstance(value,str):return value.replace('__REPO_ROOT__',str(repo)).replace('__LOG_DIR__',str(logs))
    if isinstance(value,list):return [render(v) for v in value]
    if isinstance(value,dict):return {k:render(v) for k,v in value.items()}
    return value
assert not target.is_symlink(),'launchagent target must not be linked'
if target.exists():
    assert target.stat().st_uid==os.getuid()
    assert plistlib.loads(target.read_bytes()).get('Label')==payload['Label']
fd,name=tempfile.mkstemp(prefix='.voc-sync-',dir=target.parent)
try:
    with os.fdopen(fd,'wb') as stream:
        stream.write(plistlib.dumps(render(payload)));stream.flush();os.fsync(stream.fileno())
    os.chmod(name,0o600);os.replace(name,target)
finally:
    if os.path.exists(name):os.unlink(name)
PY
/usr/bin/plutil -lint "$target"
/bin/launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$target"
/bin/launchctl enable "gui/$(id -u)/$label"
echo 'VOC source synchronization installed: every 300s; existing cloud push retains its 300s cadence.'
