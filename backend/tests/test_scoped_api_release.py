"""Offline safety tests: no production daemon, SSH, Nginx or model calls."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

MODULE = Path(__file__).resolve().parents[2] / 'deploy/cloud/scoped-api-release.py'
spec = importlib.util.spec_from_file_location('scoped_api_release', MODULE)
m = importlib.util.module_from_spec(spec)


def load():
    assert MODULE.exists(), 'scoped API release helper has not been implemented'
    spec.loader.exec_module(m)
    m.daemon_logging = lambda: {'Type':'json-file','Config':{'max-size':'50m','max-file':'3'}}
    return m


def service(image='sha256:' + 'a' * 64, release='b' * 40):
    return {'name': 'orbbec-agent-platform', 'services': {
        'platform-api': {'image': image, 'container_name':'api', 'read_only':True,'cap_drop':['ALL'],
                         'security_opt':['no-new-privileges:true'],'restart':'unless-stopped',
                         'tmpfs':['/tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001,mode=0700'],
                         'healthcheck':{'test':['CMD','health'],'interval':'10s','timeout':'3s','start_period':'15s','retries':12}, 'environment': {'TOKEN': 'do-not-print', 'PLATFORM_RELEASE_SHA': release},
                         'command': ['uvicorn', 'app.main:create_app'], 'networks': {'internal': {}},
                         'volumes': [{'type': 'bind', 'source': '/data/hr', 'target': '/hr', 'read_only': True}]},
        'platform-postgres': {'image': 'postgres:17', 'environment': {'PASSWORD': 'secret'}}},
        'networks': {'internal': {'name': 'orbbec-agent-platform-internal'}}}


def test_parity_allows_only_api_image_and_release_sha():
    h = load(); before = service(); after = copy.deepcopy(before)
    after['services']['platform-api']['image'] = 'sha256:' + 'c' * 64
    after['services']['platform-api']['environment']['PLATFORM_RELEASE_SHA'] = 'd' * 40
    h.assert_parity(before, after, 'sha256:' + 'c' * 64, 'd' * 40)


@pytest.mark.parametrize('mutation', ['secret', 'mount', 'network', 'command', 'other_service', 'top_level'])
def test_parity_rejects_unrelated_changes_without_secret_in_error(mutation):
    h = load(); before = service(); after = copy.deepcopy(before)
    api = after['services']['platform-api']
    if mutation == 'secret': api['environment']['TOKEN'] = 'new-secret-never-print'
    if mutation == 'mount': api['volumes'][0]['read_only'] = False
    if mutation == 'network': api['networks'] = {'public': {}}
    if mutation == 'command': api['command'] = ['sh']
    if mutation == 'other_service': after['services']['platform-postgres']['image'] = 'other'
    if mutation == 'top_level': after['networks']['internal']['name'] = 'wrong'
    with pytest.raises(h.ReleaseError, match='compose_parity') as err:
        h.assert_parity(before, after, api['image'], api['environment']['PLATFORM_RELEASE_SHA'])
    assert 'secret' not in str(err.value)


def test_live_contract_checks_image_defaults_and_named_mounts():
    h = load(); config = service(); api = config['services']['platform-api']
    inspect = {'Name':'/api','HostConfig':policy_host(), 'Config': {'Image':api['image'],'Healthcheck':health_policy(),'Env': ['TOKEN=do-not-print', 'PLATFORM_RELEASE_SHA=' + 'b' * 40, 'PATH=/bin'],
                          'Cmd': api['command'], 'Entrypoint': None, 'User': '10001', 'WorkingDir': '/app/backend'},
               'Mounts': [{'Type': 'bind', 'Source': '/data/hr', 'Destination': '/hr', 'RW': False}],
               'NetworkSettings': {'Networks': {'orbbec-agent-platform-internal': {'Aliases':['api','platform-api']}}}}
    image = {'Config': {'Env': ['PATH=/bin'], 'User': '10001', 'WorkingDir': '/app/backend'}}
    h.assert_live_contract(config, inspect, image)
    inspect['Config']['Env'].append('SILENT_DRIFT=secret-value')
    with pytest.raises(h.ReleaseError, match='live_environment'):
        h.assert_live_contract(config, inspect, image)


@pytest.mark.parametrize('compose_key,image_key', [('command','Cmd'),('entrypoint','Entrypoint')])
def test_live_contract_unescapes_only_rendered_compose_commands(compose_key, image_key):
    h = load(); config = service(); api = config['services']['platform-api']
    rendered = ['/bin/sh', '-ec', 'KEY="$$(cat /run/secrets/fixture-key)"; echo "$$KEY"']
    actual = ['/bin/sh', '-ec', 'KEY="$(cat /run/secrets/fixture-key)"; echo "$KEY"']
    api[compose_key] = rendered
    inspect = {'Name':'/api','HostConfig':policy_host(), 'Config': {
        'Image':api['image'],'Healthcheck':health_policy(),
        'Env':['TOKEN=do-not-print','PLATFORM_RELEASE_SHA='+'b'*40,'PATH=/bin'],
        'Cmd':api['command'], 'Entrypoint':None, 'User':'10001','WorkingDir':'/app/backend'},
        'Mounts':[{'Type':'bind','Source':'/data/hr','Destination':'/hr','RW':False}],
        'NetworkSettings':{'Networks':{'orbbec-agent-platform-internal':{'Aliases':['api','platform-api']}}}}
    inspect['Config'][image_key] = actual
    image = {'Config':{'Env':['PATH=/bin'],'User':'10001','WorkingDir':'/app/backend'}}
    h.assert_live_contract(config, inspect, image)
    inspect['Config'][image_key] = actual[:-1] + [actual[-1]+'; unexpected command']
    with pytest.raises(h.ReleaseError, match='live_'+compose_key):
        h.assert_live_contract(config, inspect, image)
    # Image defaults are Docker argv already; literal double dollars stay literal.
    api.pop(compose_key)
    image['Config'][image_key] = rendered
    inspect['Config'][image_key] = rendered
    h.assert_live_contract(config, inspect, image)


def test_command_uses_every_ordered_input_and_only_recreates_api():
    h = load(); state = {'working_dir': '/release/deploy/cloud', 'files': ['/a', '/b', '/c'],
                         'env_file': '/private/platform.env', 'project': 'orbbec-agent-platform'}
    cmd = h.compose_command(state, '/private/new.json')
    assert cmd.count('-f') == 4
    assert [cmd[i+1] for i, v in enumerate(cmd) if v == '-f'] == ['/a', '/b', '/c', '/private/new.json']
    assert h.recreate_command(state, '/private/new.json')[-8:] == [
        'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'platform-api'][-8:]
    assert all('secret' not in arg for arg in cmd)


def test_hostconfig_hash_preserves_every_value_but_ignores_unique_mount_order():
    h = load()
    original = {'Binds':['/a:/one:ro','volume:/two:rw'], 'Mounts':[
        {'Type':'bind','Source':'/c','Target':'/three','ReadOnly':True,'BindOptions':{}},
        {'Type':'bind','Source':'/d','Target':'/four','BindOptions':{}}],
        'ReadonlyRootfs':True,'CapDrop':['ALL']}
    reordered = copy.deepcopy(original)
    reordered['Binds'].reverse();reordered['Mounts'].reverse()
    assert h.hostconfig_hash(original) == h.hostconfig_hash(reordered)
    assert original['Binds'][0] == '/a:/one:ro'
    for field in ('Source','ReadOnly','BindOptions'):
        changed = copy.deepcopy(reordered)
        changed['Mounts'][0][field] = {'Propagation':'rshared'} if field == 'BindOptions' else ('/other' if field == 'Source' else True)
        assert h.hostconfig_hash(original) != h.hostconfig_hash(changed)
    changed = copy.deepcopy(reordered);changed['CapDrop']=[]
    assert h.hostconfig_hash(original) != h.hostconfig_hash(changed)
    changed = copy.deepcopy(reordered);changed['Binds'].append('/other:/one:ro')
    with pytest.raises(h.ReleaseError, match='duplicate_mount_target'):
        h.hostconfig_hash(changed)


@pytest.mark.parametrize('rollback', [False, True])
def test_legacy_hostconfig_state_is_blocked_before_any_recreate(tmp_path, monkeypatch, rollback):
    h = load();root=tmp_path/'transaction';root.mkdir(mode=0o700)
    h.save(root/'state.json',{'old_hostconfig':'legacy-raw-digest'})
    def unexpected(*args, **kwargs):
        pytest.fail('legacy state must not reach a runtime or mutation operation')
    monkeypatch.setattr(h,'command',unexpected)
    monkeypatch.setattr(h,'snapshots',unexpected)
    with pytest.raises(h.ReleaseError, match='state_hostconfig_scheme'):
        h.transact(root, rollback)


def test_image_derivation_rejects_config_or_base_layer_change():
    h = load(); base = {'RootFS': {'Layers': ['sha256:base']}, 'Config': {'Env': ['PATH=/bin', 'PLATFORM_RELEASE_SHA=old'], 'Cmd': ['api'], 'User': '10001'}}
    derived = copy.deepcopy(base); derived['RootFS']['Layers'].append('sha256:code')
    derived['Config']['Env'] = ['PATH=/bin', 'PLATFORM_RELEASE_SHA=' + 'f' * 40]
    h.assert_derived_image(base, derived, 'f' * 40)
    derived['Config']['User'] = 'root'
    with pytest.raises(h.ReleaseError, match='image_config'): h.assert_derived_image(base, derived, 'f' * 40)
    derived = copy.deepcopy(base); derived['RootFS']['Layers'] = ['sha256:other']
    with pytest.raises(h.ReleaseError, match='image_layers'): h.assert_derived_image(base, derived, 'f' * 40)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()


@pytest.mark.parametrize('migration_path', [
    'backend/migrations/cloud_agent_sync/001_agent_sources.sql',
    'backend/control_migrations/127_agent_session_identity.sql',
])
def test_stage_copies_only_delta_migration_and_dist_with_private_manifest(tmp_path, migration_path):
    h = load(); repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-q'); git(repo, 'config', 'user.email', 'test@example.invalid'); git(repo, 'config', 'user.name', 'Test')
    code = repo / 'backend/app/a.py'; code.parent.mkdir(parents=True); code.write_text('old = 1\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'base'); baseline = git(repo, 'rev-parse', 'HEAD')
    oldhash = h.digest(code.read_bytes()); code.write_text('new = 2\n')
    migration = repo / migration_path; migration.parent.mkdir(parents=True); migration.write_text('SELECT 1;\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'release'); release = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/master', release)
    dist = tmp_path / 'dist'; dist.mkdir(); (dist / 'index.html').write_text('<html>built</html>')
    proof = [{'path': 'backend/app/a.py', 'same_baseline': True, 'actual': oldhash, 'baseline': oldhash}]
    receipt = {'containers': [{'compose_labels': {'com.docker.compose.service': 'platform-api'}, 'image_id': 'sha256:' + 'a' * 64}]}
    output = tmp_path / 'stage'
    manifest = h.stage(repo, baseline, release, receipt, proof, dist, output, static_proof(receipt, baseline))
    dockerfile = (output / 'Dockerfile').read_text()
    assert dockerfile.startswith('FROM sha256:' + 'a' * 64 + '\n')
    assert 'RUN ' not in dockerfile
    assert 'pip' not in dockerfile and 'apt' not in dockerfile
    assert {f['destination'] for f in manifest['files']} == {'/app/backend/app/a.py', '/app/backend/' + migration_path.removeprefix('backend/'), '/app/backend/app/static/index.html'}
    assert (output / 'manifest.json').stat().st_mode & 0o777 == 0o600
    assert output.stat().st_mode & 0o777 == 0o700
    assert not (output / '.git').exists()
    (output / manifest['files'][0]['source']).write_text('tampered')
    with pytest.raises(h.ReleaseError, match='artifact_hash'): h.validate_stage(output)


def test_stage_rejects_symlink_dist_and_missing_live_code_proof(tmp_path):
    h = load(); repo = tmp_path/'repo'; repo.mkdir(); git(repo, 'init', '-q'); git(repo, 'config', 'user.email', 'a@b'); git(repo, 'config', 'user.name', 'test')
    code = repo/'backend/app/a.py'; code.parent.mkdir(parents=True); code.write_text('old')
    git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'base'); base = git(repo, 'rev-parse', 'HEAD'); code.write_text('new')
    mig = repo/h.MIGRATION; mig.parent.mkdir(parents=True); mig.write_text('sql')
    git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'new'); release = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/master', release)
    dist=tmp_path/'dist';dist.mkdir();(dist/'index.html').write_text('hi')
    receipt={'containers':[{'compose_labels':{'com.docker.compose.service':'platform-api'},'image_id':'sha256:'+'a'*64}]}
    with pytest.raises(h.ReleaseError, match='code_baseline'): h.stage(repo,base,release,receipt,[],dist,tmp_path/'out',static_proof(receipt,base))
    (dist/'link').symlink_to('/etc/passwd')
    with pytest.raises(h.ReleaseError): h.stage(repo,base,release,receipt,[],dist,tmp_path/'out2',static_proof(receipt,base))


def test_stage_tracks_distinct_verified_frontend_and_backend_baselines(tmp_path):
    h=load();repo=tmp_path/'repo';repo.mkdir()
    git(repo,'init','-q');git(repo,'config','user.email','test@example.invalid');git(repo,'config','user.name','Test')
    code=repo/'backend/app/a.py';code.parent.mkdir(parents=True);code.write_text('old')
    git(repo,'add','.');git(repo,'commit','-qm','backend baseline');base=git(repo,'rev-parse','HEAD')
    (repo/'frontend-marker').write_text('already deployed independently')
    git(repo,'add','.');git(repo,'commit','-qm','frontend baseline');front=git(repo,'rev-parse','HEAD')
    oldhash=h.digest(code.read_bytes());code.write_text('new')
    migration=repo/'backend/control_migrations/127_agent_session_identity.sql';migration.parent.mkdir(parents=True);migration.write_text('SELECT 1;')
    git(repo,'add','.');git(repo,'commit','-qm','release');release=git(repo,'rev-parse','HEAD')
    git(repo,'update-ref','refs/remotes/origin/master',release)
    dist=tmp_path/'dist';dist.mkdir();(dist/'index.html').write_text('built')
    receipt={'containers':[{'compose_labels':{'com.docker.compose.service':'platform-api'},'image_id':'sha256:'+'a'*64}]}
    proof=[{'path':'backend/app/a.py','same_baseline':True,'actual':oldhash,'baseline':oldhash}]
    manifest=h.stage(repo,base,release,receipt,proof,dist,tmp_path/'out',static_proof(receipt,front))
    assert manifest['baseline_commit']==base
    assert manifest['static_baseline_commit']==front
    h.validate_stage(tmp_path/'out')


def test_cli_failure_does_not_echo_secret_or_traceback(tmp_path):
    load(); p=tmp_path/'bad';p.write_text('{"secret":"never-echo"}');p.chmod(0o600)
    result=subprocess.run(['python3',str(MODULE),'inspect-plan','--transaction',str(tmp_path)],text=True,capture_output=True)
    assert result.returncode != 0
    assert 'never-echo' not in result.stdout+result.stderr
    assert 'Traceback' not in result.stderr


def static_proof(receipt, baseline):
    return {'image_id': receipt['containers'][0]['image_id'], 'baseline_commit': baseline,
            'same_baseline': True, 'algorithm': 'active-static-graph-v1', 'live_active_sha256': 'a'*64, 'baseline_active_sha256': 'a'*64}


def test_static_baseline_must_match_exact_base_image_and_frontend():
    h=load();proof=static_proof({'containers':[{'image_id':'sha256:'+'a'*64}]},'b'*40)
    h.assert_static_baseline(proof,'sha256:'+'a'*64,'b'*40)
    for key, value in [('image_id','sha256:'+'c'*64),('baseline_commit','c'*40),('same_baseline',False),('live_active_sha256','d'*64)]:
        changed={**proof,key:value}
        with pytest.raises(h.ReleaseError,match='static_baseline'):h.assert_static_baseline(changed,'sha256:'+'a'*64,'b'*40)


def test_unrelated_container_change_is_detected():
    h=load();before={'/api':{'id':'old'},'/fae':{'id':'same','pid':123}}
    h.unchanged_others(before,{**before,'/api':{'id':'new'}},'/api')
    with pytest.raises(h.ReleaseError,match='other_container_changed'):
        h.unchanged_others(before,{'/api':{'id':'new'},'/fae':{'id':'same','pid':124}},'/api')


def test_existing_foreign_locks_are_not_removed(tmp_path,monkeypatch):
    h=load();monkeypatch.setattr(h,'PRIVATE',tmp_path);lock=tmp_path/'agent-brain-action.lock';lock.mkdir(mode=0o700)
    owner=lock/'owner';owner.write_text('foreign-owner\n');owner.chmod(0o600)
    state={'owner':'ours','deployment_id':'a'*32,'release':'b'*40}
    with pytest.raises(h.ReleaseError,match='deployment_locked'):h.locks(state,True)
    assert owner.read_text()=='foreign-owner\n'
    assert not (tmp_path/'deploy-input.lock').exists()


def test_owned_locks_are_reusable_for_explicit_rollback(tmp_path,monkeypatch):
    h=load();monkeypatch.setattr(h,'PRIVATE',tmp_path)
    state={'owner':'ours','deployment_id':'a'*32,'release':'b'*40}
    h.locks(state,True);h.locks(state,True);h.release_locks(state)
    assert not list(tmp_path.iterdir())


def test_runtime_admin_and_nginx_baseline_failure_is_not_hidden(tmp_path,monkeypatch):
    h=load();p=tmp_path/'nginx.conf';p.write_text('user www-data;')
    receipt={'nginx_config_files':[{'path':str(p),'sha256':h.digest(p.read_bytes())}], 'systemd_units':[]}
    h.guardrails(receipt)
    p.write_text('changed')
    with pytest.raises(h.ReleaseError,match='shared_config_changed'):h.guardrails(receipt)


def test_release_must_be_clean_exact_origin_master(tmp_path):
    h=load();repo=tmp_path/'repo';repo.mkdir();git(repo,'init','-q');git(repo,'config','user.email','a@b');git(repo,'config','user.name','test')
    (repo/'a').write_text('a');git(repo,'add','.');git(repo,'commit','-qm','base');release=git(repo,'rev-parse','HEAD')
    with pytest.raises(h.ReleaseError):h.assert_release(repo,release)
    git(repo,'update-ref','refs/remotes/origin/master',release);h.assert_release(repo,release)
    (repo/'a').write_text('changed')
    with pytest.raises(h.ReleaseError,match='release_dirty'):h.assert_release(repo,release)


def test_stage_rejects_added_context_file(tmp_path):
    h=load();tmp_path.chmod(0o700)
    # Reject before any Docker build if a staged context gained an extra file.
    manifest={'version':1,'release_sha':'a'*40,'base_image_id':'sha256:'+'b'*64,
              'baseline_commit':'c'*40,'files':[],'dockerfile_sha256':h.digest(b'FROM base\n')}
    for name,value in [('manifest.json',manifest),('readiness.json',{}),('static-baseline.json',static_proof({'containers':[{'image_id':'sha256:'+'b'*64}]},'c'*40))]:h.save(tmp_path/name,value)
    h.save(tmp_path/'Dockerfile',b'FROM base\n');h.save(tmp_path/'accidental-secret',b'never-build-me')
    with pytest.raises(h.ReleaseError,match='context_extra_files'):h.validate_stage(tmp_path)


@pytest.mark.parametrize('rollback,fail,missing,drift', [(False,False,False,None),(True,False,False,None),(False,True,False,None),(True,False,True,None),(False,False,False,'before'),(False,False,False,'after'),(False,False,False,'tag_before'),(False,False,False,'tag_after')])
def test_transaction_only_recreates_api_and_retains_locks_on_uncertainty(tmp_path,monkeypatch,rollback,fail,missing,drift):
    h=load();private=tmp_path/'private';private.mkdir(mode=0o700);monkeypatch.setattr(h,'PRIVATE',private)
    root=tmp_path/'transaction';root.mkdir(mode=0o700)
    oldimage='sha256:'+'a'*64;newimage='sha256:'+'c'*64
    oldref='orbbec-agent-platform:manufacturing-v2-original';mh='f'*64
    newref=h.application_reference('d'*40,mh)
    old=service(oldref);new=service(newref,'d'*40)
    def live(config, image, cid):
        return {'Name':'/api','Id':cid,'Image':image,'RestartCount':0,
                'Config':{'Image':config['services']['platform-api']['image'],'Labels':{'com.docker.compose.project':h.PROJECT,'com.docker.compose.service':h.SERVICE},
                          'Env':['PATH=/bin']+[k+'='+v for k,v in config['services']['platform-api']['environment'].items()],
                          'Cmd':config['services']['platform-api']['command'],'Entrypoint':None,'User':'10001','WorkingDir':'/app/backend','Healthcheck':health_policy()},
                'HostConfig':policy_host(),
                'State':{'Running':True,'Health':{'Status':'healthy'}},
                'Mounts':[{'Type':'bind','Source':'/data/hr','Destination':'/hr','RW':False}],
                'NetworkSettings':{'Networks':{'orbbec-agent-platform-internal':{'Aliases':['api','platform-api']}}}}
    initial=live(new,newimage,'new') if rollback else live(old,oldimage,'old')
    final=live(old,oldimage,'rollback') if rollback else live(new,newimage,'new')
    if drift=='before':initial['HostConfig']['ReadonlyRootfs']=False
    if drift=='after':final['HostConfig']['PortBindings']={'8080/tcp':[{'HostIp':'0.0.0.0','HostPort':'8080'}]}
    baseline={'/api':{'id':'old'},'/fae':{'id':'untouched','pid':20}}
    start={**baseline,'/api':{'id':initial['Id']}};after={**baseline,'/api':{'id':final['Id']}}
    if missing:start={k:v for k,v in start.items() if k!='/api'}
    sequence=iter([(start,[] if missing else [initial]),(start,[] if missing else [initial]),(after,[final])])
    monkeypatch.setattr(h,'snapshots',lambda:next(sequence))
    monkeypatch.setattr(h,'input_hashes',lambda state:{})
    monkeypatch.setattr(h,'guardrails',lambda receipt:None)
    monkeypatch.setattr(h,'render',lambda state,path:old if 'rollback' in str(path) else new)
    monkeypatch.setattr(h,'inspect_image',lambda image:{'Config':{'Env':['PATH=/bin'],'User':'10001','WorkingDir':'/app/backend'}})
    calls=[]
    def resolve(reference,image):
        if drift=='tag_before' or (drift=='tag_after' and calls):raise h.ReleaseError('image_reference')
        assert {oldref:oldimage,newref:newimage}[reference]==image
    monkeypatch.setattr(h,'resolve_reference',resolve)
    def execute(args,cwd=None):
        calls.append(args)
        if fail:raise h.ReleaseError('command_failed')
        return b''
    monkeypatch.setattr(h,'command',execute)
    state={'files':['/one','/two'],'working_dir':'/release','project':h.PROJECT,'env_file':'/private/platform.env',
           'inputs':{},'guardrails':{},'old_hostconfig':h.hostconfig_hash(policy_host()),'hostconfig_hash_scheme':'unique-mount-lists-v1','baseline_containers':baseline,'api_name':'/api','old_id':'old',
           'old_image':oldimage,'new_image':newimage,'old_image_reference':oldref,'new_image_reference':newref,'manifest_sha256':mh,'old_release':'b'*40,'release':'d'*40,
           'before_config':h.digest(h.canonical(old)),'after_config':h.digest(h.canonical(new)),
           'owner':'unique-owner','deployment_id':'e'*32}
    h.save(root/'state.json',state)
    if missing:
        h.save(root/'apply.started.json',{'action':'apply','image_id':newimage,'release_sha':'d'*40})
        h.locks(state,True)
    if drift:
        with pytest.raises(h.ReleaseError,match=('image_reference' if drift.startswith('tag_') else 'live_security' if drift=='before' else 'live_ports')):h.transact(root,rollback)
        assert not (root/'apply.complete.json').exists()
        assert (private/'agent-brain-action.lock').exists() == (drift in ('after','tag_after'))
    elif fail:
        with pytest.raises(h.ReleaseError,match='command_failed'):h.transact(root,rollback)
        assert (private/'agent-brain-action.lock').is_dir()
        assert (private/'deploy-input.lock').is_dir()
        assert not (root/'apply.complete.json').exists()
    else:
        h.transact(root,rollback)
        assert not list(private.iterdir())
        receipt=json.loads((root/('rollback.complete.json' if rollback else 'apply.complete.json')).read_text())
        assert receipt['image_id']==(oldimage if rollback else newimage)
        assert receipt['release_sha']==('b'*40 if rollback else 'd'*40)
        assert receipt['image_reference']==(oldref if rollback else newref)
    assert len(calls)==(0 if drift in ('before','tag_before') else 1)
    if drift in ('before','tag_before'):return
    assert calls[0][-8:]==['up','-d','--no-deps','--no-build','--pull','never','--force-recreate','platform-api']
    assert 'do-not-print' not in repr(calls)


def test_guardrails_accept_hash_checked_nginx_site_symlinks(tmp_path):
    h=load();target=tmp_path/'site.conf';target.write_text('server {}')
    link=tmp_path/'enabled.conf';link.symlink_to(target)
    receipt={'nginx_config_files':[{'path':str(link),'sha256':h.digest(target.read_bytes()),'symlink':True}],'systemd_units':[]}
    h.guardrails(receipt)
    target.write_text('changed')
    with pytest.raises(h.ReleaseError,match='shared_config_changed'):h.guardrails(receipt)


def test_admin_unit_receipt_requires_its_configuration_supplement(monkeypatch):
    h=load();raw=b'MainPID=123\n';monkeypatch.setattr(h,'command',lambda args:raw)
    receipt={'nginx_config_files':[],'systemd_units':[{'unit':'ai-admin-agent.service','properties_sha256':h.digest(raw),'unit_files':[]}]}
    with pytest.raises(h.ReleaseError,match='admin_config_baseline_missing'):h.guardrails(receipt)


def policy_host():
    return {'ReadonlyRootfs':True,'Privileged':False,'CapAdd':None,'CapDrop':['ALL'],
            'SecurityOpt':['no-new-privileges:true'],'PortBindings':{},'PublishAllPorts':False,
            'Memory':0,'MemorySwap':0,'MemoryReservation':0,'NanoCpus':0,'CpuShares':0,'CpuQuota':0,'CpuPeriod':0,'CpusetCpus':'','PidsLimit':None,
            'RestartPolicy':{'Name':'unless-stopped','MaximumRetryCount':0},
            'LogConfig':{'Type':'json-file','Config':{'max-size':'50m','max-file':'3'}},
            'Tmpfs':{'/tmp':'rw,noexec,nosuid,size=32m,uid=10001,gid=10001,mode=0700'},
            'NetworkMode':'orbbec-agent-platform-internal','IpcMode':'private','PidMode':'','UTSMode':'','CgroupnsMode':'private',
            'ShmSize':67108864,'Ulimits':None,'OomKillDisable':None,'OomScoreAdj':0,'AutoRemove':False,
            'Devices':None,'DeviceRequests':None,'Dns':None,'ExtraHosts':[]}


def health_policy():
    return {'Test':['CMD','health'],'Interval':10000000000,'Timeout':3000000000,'StartPeriod':15000000000,'Retries':12}


def policy_fixture():
    config=service();api=config['services']['platform-api']
    api.update({'read_only':True,'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],'restart':'unless-stopped',
                'tmpfs':['/tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001,mode=0700'],
                'healthcheck':{'test':['CMD','health'],'interval':'10s','timeout':'3s','start_period':'15s','retries':12}})
    live={'Name':'/api','HostConfig':policy_host(),'Config':{'Healthcheck':health_policy()},
          'NetworkSettings':{'Networks':{'orbbec-agent-platform-internal':{'Aliases':['api','platform-api'],'IPAMConfig':None,'Links':None,'DriverOpts':None,'GwPriority':0}},'Ports':{'8080/tcp':None}}}
    image={'Config':{'Healthcheck':{'Test':['CMD','health'],'Interval':15000000000,'Timeout':3000000000,'StartPeriod':20000000000,'Retries':4}}}
    return config,live,image


@pytest.mark.parametrize('field,value', [('read_only',False),('cap_drop',[]),('security_opt',[]),('ports',[{'target':8080,'published':'8080','host_ip':'0.0.0.0','protocol':'tcp'}])])
def test_actual_runtime_policy_rejects_demonstrated_preflight_drift(field,value):
    h=load();config,live,image=policy_fixture();config['services']['platform-api'][field]=value
    with pytest.raises(h.ReleaseError,match='live_'):
        h.assert_runtime_policy(config,live,image,live['HostConfig']['LogConfig'])


@pytest.mark.parametrize('section,field,value', [('HostConfig','Memory',1024),('HostConfig','PidsLimit',64),('HostConfig','ReadonlyRootfs',False),('HostConfig','LogConfig',{'Type':'json-file','Config':{}}),('HostConfig','RestartPolicy',{'Name':'no','MaximumRetryCount':0}),('HostConfig','Tmpfs',{}),('HostConfig','NetworkMode','host'),('HostConfig','Privileged',True),('HostConfig','MaskedPaths',[]),('HostConfig','ReadonlyPaths',[])])
def test_actual_runtime_policy_rejects_postcreate_drift(section,field,value):
    h=load();config,live,image=policy_fixture();live[section][field]=value
    with pytest.raises(h.ReleaseError,match='live_'):
        h.assert_runtime_policy(config,live,image,{'Type':'json-file','Config':{'max-size':'50m','max-file':'3'}})


def test_actual_runtime_policy_matches_current_defaults_and_health_overrides():
    h=load();config,live,image=policy_fixture();h.assert_runtime_policy(config,live,image,live['HostConfig']['LogConfig'])
    live['Config']['Healthcheck']['Retries']=4
    with pytest.raises(h.ReleaseError,match='live_healthcheck'):h.assert_runtime_policy(config,live,image,live['HostConfig']['LogConfig'])


def test_actual_runtime_endpoint_contract_rejects_static_ip_alias_and_gateway_drift():
    h=load();config,live,image=policy_fixture();ep=config['services']['platform-api']['networks']['internal'];ep.update({'ipv4_address':'172.30.0.4','gw_priority':1})
    actual=live['NetworkSettings']['Networks']['orbbec-agent-platform-internal'];actual.update({'IPAMConfig':{'IPv4Address':'172.30.0.4','IPv6Address':''},'GwPriority':1})
    h.assert_runtime_policy(config,live,image,live['HostConfig']['LogConfig'])
    for key,value in [('IPAMConfig',{'IPv4Address':'172.30.0.99','IPv6Address':''}),('Aliases',['api','wrong']),('GwPriority',0)]:
        changed=copy.deepcopy(live);changed['NetworkSettings']['Networks']['orbbec-agent-platform-internal'][key]=value
        with pytest.raises(h.ReleaseError,match='live_network_endpoint'):h.assert_runtime_policy(config,changed,image,live['HostConfig']['LogConfig'])


def test_actual_runtime_policy_fails_closed_for_unhandled_rendered_policy():
    h=load();config,live,image=policy_fixture();config['services']['platform-api']['device_cgroup_rules']=['c 1:3 rwm']
    with pytest.raises(h.ReleaseError,match='unsupported_runtime_setting'):h.assert_runtime_policy(config,live,image,live['HostConfig']['LogConfig'])


def test_staged_payload_uses_one_copy_layer_and_runtime_readable_modes(tmp_path):
    h=load();repo=tmp_path/'repo';repo.mkdir();git(repo,'init','-q');git(repo,'config','user.email','a@b');git(repo,'config','user.name','test')
    p=repo/'backend/app/a.py';p.parent.mkdir(parents=True);p.write_text('old')
    git(repo,'add','.');git(repo,'commit','-qm','base');base=git(repo,'rev-parse','HEAD');oldhash=h.digest(p.read_bytes());p.write_text('new')
    mig=repo/h.MIGRATION;mig.parent.mkdir(parents=True);mig.write_text('migration')
    git(repo,'add','.');git(repo,'commit','-qm','release');release=git(repo,'rev-parse','HEAD');git(repo,'update-ref','refs/remotes/origin/master',release)
    dist=tmp_path/'dist';dist.mkdir();(dist/'index.html').write_text('built');(dist/'assets').mkdir()
    for i in range(90):(dist/'assets'/('chunk-'+str(i)+'.js')).write_text('asset')
    receipt={'containers':[{'compose_labels':{'com.docker.compose.service':'platform-api'},'image_id':'sha256:'+'a'*64}]}
    proof=[{'path':'backend/app/a.py','same_baseline':True,'actual':oldhash,'baseline':oldhash}]
    output=tmp_path/'stage';manifest=h.stage(repo,base,release,receipt,proof,dist,output,static_proof(receipt,base))
    assert (output/'Dockerfile').read_text().count('COPY ')==1
    assert len(manifest['files'])==93
    for f in manifest['files']:
        assert f['source']=='payload/'+f['destination'].removeprefix('/app/backend/')
        assert (output/f['source']).stat().st_mode & 0o777==0o644
    assert all(p.stat().st_mode&0o777==0o755 for p in (output/'payload').rglob('*') if p.is_dir())
    assert output.stat().st_mode&0o777==0o700
    h.validate_stage(output)


def test_derived_image_layer_budget_rejects_overlay2_overflow():
    h=load();base={'RootFS':{'Layers':['base']*128},'Config':{'Env':[]}}
    derived=copy.deepcopy(base);derived['RootFS']['Layers'].append('new');derived['Config']['Env']=['PLATFORM_RELEASE_SHA='+'a'*40]
    with pytest.raises(h.ReleaseError,match='image_layers'):h.assert_derived_image(base,derived,'a'*40)


def test_application_reference_is_manifest_bound_and_backup_compatible():
    h=load();reference=h.application_reference('a'*40,'b'*64)
    assert reference=='orbbec-agent-platform:scoped-api-'+'a'*40+'-'+'b'*64
    assert len(reference.split(':')[1])<=128
    assert reference.startswith('orbbec-agent-platform:')
    assert reference!=h.application_reference('a'*40,'c'*64)


def test_reference_resolution_rejects_retagging_and_missing_repo_tag(monkeypatch):
    h=load();reference='orbbec-agent-platform:scoped-api-test';image='sha256:'+'a'*64
    monkeypatch.setattr(h,'docker',lambda *args:json.dumps([{'Id':image,'RepoTags':[reference]}]).encode())
    h.resolve_reference(reference,image)
    for actual in [{'Id':'sha256:'+'b'*64,'RepoTags':[reference]},{'Id':image,'RepoTags':[]}]:
        monkeypatch.setattr(h,'docker',lambda *args:json.dumps([actual]).encode())
        with pytest.raises(h.ReleaseError,match='image_reference'):h.resolve_reference(reference,image)


def test_override_uses_exact_application_reference_for_backup_and_rollback(tmp_path):
    h=load();reference='orbbec-agent-platform:manufacturing-v2-28272ec4-f324cca2'
    h.override(tmp_path/'rollback.json',reference,'b'*40)
    assert json.loads((tmp_path/'rollback.json').read_bytes())['services']['platform-api']['image']==reference
    with pytest.raises(h.ReleaseError,match='override'):h.override(tmp_path/'bad.json','sha256:'+'a'*64,'b'*40)


@pytest.mark.parametrize('existing',[False,True])
def test_build_refuses_existing_manifest_tag_and_receipt_binds_reference_to_id(tmp_path,monkeypatch,existing):
    h=load();stage=tmp_path/'stage';stage.mkdir(mode=0o700)
    manifest={'release_sha':'a'*40,'base_image_id':'sha256:'+'b'*64}
    h.save(stage/'manifest.json',manifest);mh=h.digest((stage/'manifest.json').read_bytes())
    reference='orbbec-agent-platform:scoped-api-'+'a'*40+'-'+mh
    base={'Id':manifest['base_image_id'],'Config':{'Env':[]},'RootFS':{'Layers':['base']}}
    derived={'Id':'sha256:'+'c'*64,'Config':{'Env':['PLATFORM_RELEASE_SHA='+'a'*40]},'RootFS':{'Layers':['base','copy']},'RepoTags':[reference]}
    monkeypatch.setattr(h,'validate_stage',lambda root:manifest);monkeypatch.setattr(h,'inspect_image',lambda image:base)
    calls=[]
    def docker(*args):
        calls.append(args)
        if args[:2]==('image','ls'):return b'existing' if existing else b''
        if args[0]=='build':return b'built'
        if args[:2]==('image','inspect'):return json.dumps([derived]).encode()
        raise AssertionError(args)
    monkeypatch.setattr(h,'docker',docker)
    if existing:
        with pytest.raises(h.ReleaseError,match='release_tag_exists'):h.build(stage,tmp_path/'out')
        assert not any(c[0]=='build' for c in calls)
    else:
        h.build(stage,tmp_path/'out');receipt=json.loads((tmp_path/'out/build.json').read_bytes())
        assert receipt['image_reference']==reference and receipt['image_id']==derived['Id']
        assert receipt['manifest_sha256']==mh
        build=next(c for c in calls if c[0]=='build');assert build[build.index('--tag')+1]==reference
