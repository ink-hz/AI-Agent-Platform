"""Execute marked runbook blocks; external Docker/PM2/bootstrap are causal mocks."""
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
from pathlib import Path

import pytest

RUNBOOK = Path(__file__).parents[2] / "docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md"

MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root=pathlib.Path(os.environ['MOCK_ROOT']); path=root/'state.json'
state=json.loads(path.read_text()); args=sys.argv[1:]; kind=pathlib.Path(sys.argv[0]).name
state['calls'].append([kind,*args]); scenario=os.environ['MOCK_SCENARIO']
def finish(code=0, output=''):
    path.write_text(json.dumps(state)); print(output) if output else None; raise SystemExit(code)
if kind=='mock-pm2':
    command=args[0]
    if command=='state-one': finish(output=state['hr'])
    if command=='snapshot-except': finish(output='changed' if scenario=='peers_changed' and state['hr']=='absent' else '{}')
    if command=='delete-one':
        if scenario=='delete_failure': finish(2)
        if scenario!='delete_no_effect': state['hr']='absent'
        finish()
    if command=='save': state['saved']=True; finish()
    finish(99)
if kind=='mock-bootstrap':
    if state['old_running']: finish(91)
    if scenario=='migration_failure': finish(2)
    state['migrated']=True; finish()
if kind=='mock-docker':
    if args[0]=='image': finish(output=os.environ['MOCK_IMAGE'])
    if args[0]=='run': finish(output='bad-source' if scenario=='source_mismatch' else os.environ['MOCK_SOURCE_SHA'])
    if args[0]=='update': finish()
    if args[0]=='stop':
        if args[-1]=='old': state['old_running']=scenario=='stop_no_effect'
        else: state['new_running']=False
        finish(2 if scenario=='stop_failure' else 0)
    if args[0]=='inspect':
        identity=args[-1]; new=identity=='new'
        if '.Image' in args[2]: finish(output=('sha256:'+'f'*64) if new and scenario=='wrong_started_image' else os.environ['MOCK_IMAGE'] if new else os.environ['MOCK_OLD_IMAGE'])
        finish(output=str(state['new_running'] if new else state['old_running']).lower())
    if args[0]=='exec': finish(output='wrong-ledger' if scenario=='ledger_failure' else os.environ['MOCK_MIGRATION_SHA']+'|true')
    if args[0]=='compose':
        if 'config' in args: finish(output=json.dumps({'services':{'platform-attachments':{'image':('wrong-image' if scenario=='wrong_config_image' else os.environ['MOCK_IMAGE'])}}}))
        if 'ps' in args: finish(output='new' if state['new_created'] else 'old')
        if 'up' in args:
            if state['old_running'] or not state['migrated']: finish(92)
            if scenario=='start_failure': finish(2)
            state['new_created']=True; state['new_running']=True; finish()
    finish(98)
finish(97)
'''


def run_block(tmp_path, marker, scenario):
    blocks = re.findall(r"```bash\n(.*?)```", RUNBOOK.read_text(), re.DOTALL)
    selected = [block for block in blocks if f"# {marker}\n" in block]
    assert len(selected) == 1
    snippet = selected[0]
    for name in ("mock-docker", "mock-pm2", "mock-bootstrap"):
        path = tmp_path / name
        path.write_text(MOCK)
        path.chmod(0o700)
    release = tmp_path / "release"
    (release / "backend/control_migrations").mkdir(parents=True)
    (release / "deploy/cloud").mkdir(parents=True)
    (release / "docs/runbooks").mkdir(parents=True)
    runbook_copy = release / "docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md"
    runbook_copy.write_bytes(RUNBOOK.read_bytes())
    source = tmp_path / "erasure.py"
    source.write_text("reviewed synthetic erasure fix")
    migration = release / "backend/control_migrations/100_attachment_erasure_worker_access.sql"
    migration.write_bytes((RUNBOOK.parents[2] / "backend/control_migrations/100_attachment_erasure_worker_access.sql").read_bytes())
    wrapper, ecosystem = tmp_path / "wrapper.sh", tmp_path / "ecosystem.cjs"
    wrapper.write_text("reviewed synthetic wrapper")
    ecosystem.write_text("reviewed synthetic ecosystem")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    approved = {
        "hostname": socket.gethostname(), "wrapperSha256": digest(wrapper),
        "ecosystemSha256": digest(ecosystem), "expectedState": "online",
        "runbookSha256": digest(runbook_copy),
    }
    if scenario == "wrong_wrapper":
        approved["wrapperSha256"] = "0" * 64
    if scenario == "wrong_host":
        approved["hostname"] = "another-host"
    if scenario != "missing_approval":
        (tmp_path / "approved-hr-stop.json").write_text(json.dumps(approved))
    (tmp_path / "state.json").write_text(json.dumps({
        "hr": "online", "saved": False, "old_running": True,
        "new_running": False, "new_created": False, "migrated": False, "calls": [],
    }))
    substitutions = {
        "/usr/bin/docker": str(tmp_path / "mock-docker"),
        'sudo -n -H -u agentops /bin/bash "$OPS_PM2"': shlex.quote(str(tmp_path / "mock-pm2")),
        '/bin/bash "$attachment_release/deploy/cloud/bootstrap-control-db.sh"': shlex.quote(str(tmp_path / "mock-bootstrap")),
        "/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh": str(wrapper),
        "/Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs": str(ecosystem),
        "/Users/agentops/AgentRuntime/release-evidence/APPROVED_CHANGE": str(tmp_path),
        "/opt/orbbec-agent-platform/releases/APPROVED_RELEASE": str(release),
        "/opt/orbbec-agent-platform/release-evidence/APPROVED_ATTACHMENT_CHANGE": str(tmp_path),
        "APPROVED_NEW_IMAGE_ID": "sha256:" + "a" * 64,
        "APPROVED_OLD_IMAGE_ID": "sha256:" + "b" * 64,
        "APPROVED_OLD_CONTAINER_ID": "old",
        "APPROVED_SOURCE_SHA256": digest(source),
        "APPROVED_MIGRATION_100_SHA256": digest(migration),
        "APPROVED_RUNBOOK_SHA256": digest(runbook_copy),
    }
    for old, new in substitutions.items():
        snippet = snippet.replace(old, new)
    # The PM2 host receives the exact approved runbook copy before exit.
    (tmp_path / "runbook.md").write_bytes(RUNBOOK.read_bytes())
    result = subprocess.run(["bash", "-c", snippet], env={
        **os.environ, "MOCK_ROOT": str(tmp_path), "MOCK_SCENARIO": scenario,
        "MOCK_IMAGE": "sha256:" + "a" * 64, "MOCK_OLD_IMAGE": "sha256:" + "b" * 64,
        "MOCK_SOURCE_SHA": digest(source), "MOCK_MIGRATION_SHA": digest(migration),
    }, capture_output=True, text=True, timeout=10, check=False)
    return result, json.loads((tmp_path / "state.json").read_text())


@pytest.mark.parametrize("scenario", ["success", "wrong_wrapper", "wrong_host", "missing_approval", "delete_failure", "delete_no_effect", "peers_changed"])
def test_pm2_exit_is_causal_and_saves_only_verified_absence(tmp_path, scenario):
    result, state = run_block(tmp_path, "HR_LEGACY_STOP", scenario)
    assert (result.returncode == 0) == (scenario == "success"), result.stderr
    assert state["saved"] == (scenario == "success")
    calls = [call[1] for call in state["calls"]]
    if scenario in {"wrong_wrapper", "wrong_host", "missing_approval"}:
        assert "delete-one" not in calls
    if scenario == "delete_no_effect":
        assert state["hr"] == "online"
    if scenario == "success":
        assert calls.index("delete-one") < calls.index("save")
        receipt = json.loads((tmp_path / "pm2-stop-receipt.json").read_text())
        assert receipt["runbookSha256"] == hashlib.sha256(RUNBOOK.read_bytes()).hexdigest()
        assert receipt["beforeState"] == "online" and receipt["afterState"] == "absent"
        assert (tmp_path / "hr-before-identity.json").is_file()


@pytest.mark.parametrize("scenario", ["success", "wrong_config_image", "source_mismatch", "stop_failure", "stop_no_effect", "migration_failure", "ledger_failure", "start_failure", "wrong_started_image"])
def test_attachment_sequence_fails_closed_and_never_restarts_old_image(tmp_path, scenario):
    result, state = run_block(tmp_path, "HR_ATTACHMENT_HOTFIX", scenario)
    assert (result.returncode == 0) == (scenario == "success"), result.stderr
    calls = state["calls"]
    boot = [i for i, call in enumerate(calls) if call[0] == "mock-bootstrap"]
    up = [i for i, call in enumerate(calls) if call[0] == "mock-docker" and "up" in call]
    if up:
        assert boot and boot[0] < up[0]
    if scenario in {"wrong_config_image", "source_mismatch", "stop_failure", "stop_no_effect", "migration_failure", "ledger_failure"}:
        assert not up
    if scenario in {"migration_failure", "ledger_failure", "start_failure", "wrong_started_image"}:
        assert not state["old_running"] and not state["new_running"]
    if scenario == "success":
        assert not state["old_running"] and state["new_running"]
        receipt = json.loads((tmp_path / "attachment-hotfix-receipt.json").read_text())
        assert receipt["runbookSha256"] == hashlib.sha256(RUNBOOK.read_bytes()).hexdigest()
        assert receipt["imageId"] == "sha256:" + "a" * 64
