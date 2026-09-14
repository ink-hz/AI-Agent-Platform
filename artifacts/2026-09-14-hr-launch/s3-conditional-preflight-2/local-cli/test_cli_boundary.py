import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT/'s3-conditional-production-probe/probe.py'
spec = importlib.util.spec_from_file_location('probe_cli_fixture', ROOT/'s3-conditional-production-probe/test_probe.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
m = fixture.m


def test_full_cli_reaches_probe_with_private_files_and_simulated_runtime_ownership(tmp_path, monkeypatch, capsys):
    evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
    secrets=tmp_path/'secrets';secrets.mkdir()
    for name in ('attachment-s3-access-key','attachment-s3-secret-key'):
        p=secrets/name;p.write_text('synthetic');p.chmod(0o600)
    identity='c'*64
    binding={'run_id':'a'*32,'minio':{'id':'d'*64,'image':'sha256:'+'e'*64,'pid':1,'started_at':'synthetic'},'client':{'id':identity,'image':'sha256:'+'f'*64},'script_sha256':m.sha(PROBE.read_bytes())}
    path=evidence/'binding.json';path.write_text(json.dumps(binding));path.chmod(0o600)
    real_lstat=Path.lstat
    def lstat(p,*a,**kw):
        values=real_lstat(p,*a,**kw)
        if p.parent==secrets:
            changed=list(values);changed[4]=10001;return os.stat_result(changed)
        return values
    monkeypatch.setattr(Path,'lstat',lstat)
    monkeypatch.setattr(m,'Path',lambda value: secrets if str(value)=='/run/secrets' else Path(value))
    monkeypatch.setattr(m.socket,'gethostname',lambda:identity[:12])
    client=fixture.S3();client.close=lambda:None
    monkeypatch.setattr(m.boto3,'client',lambda *a,**kw:client)
    monkeypatch.setattr(sys,'argv',['probe','--binding',str(path),'--evidence',str(evidence),'--execute'])
    assert m.main()==0
    assert json.loads(capsys.readouterr().out)['status']=='passed'
    assert not client.objects


def test_new_wrapper_sets_only_own_files_to_runtime_uid_and_caps_stay_dropped():
    source=Path(__file__).with_name('host.py').read_text()
    tree=ast.parse(source)
    program=ast.literal_eval(tree.body[0].value)
    assert program.encode()==PROBE.read_bytes()
    assert "'--user','10001:10001'" in source
    assert "'--cap-drop','ALL'" in source
    assert 'os.fchown(f.fileno(),10001,10001)' in source
    assert 'os.chown(ROOT,10001,10001)' in source
    assert "os.chown(ROOT/'evidence',10001,10001)" in source
    assert '/var/lib/docker/volumes' not in source
    assert "'--mount','type=volume,src=orbbec-agent-platform-api-secrets,dst=/run/secrets,readonly'" in source
