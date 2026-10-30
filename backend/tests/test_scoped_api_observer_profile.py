"""Bounded observer profile tests; Docker/systemd boundaries are synthetic."""
import copy
import json
from pathlib import Path
import pytest
from test_scoped_api_release import load, service, policy_host, health_policy

PROFILE = 'dashboard-observer-bind-v1'
FAE = 'PLATFORM_FAE_HEALTH_URL'
URL = 'http://ai-fae-backend:8000/health'
BIND = {'type':'bind','source':'/var/lib/orbbec-platform-runtime','target':'/run/platform-runtime',
        'read_only':True,'bind':{'create_host_path':False,'propagation':'rprivate'}}
HOST_BIND = {'Type':'bind','Source':BIND['source'],'Target':BIND['target'],'ReadOnly':True,
             'BindOptions':{'Propagation':'rprivate'}}


def configs(image_env=(), compose_env=None):
    old=service('orbbec-agent-platform:old')
    if compose_env is not None: old['services']['platform-api']['environment'][FAE]=compose_env
    new=copy.deepcopy(old);api=new['services']['platform-api']
    api['image']='orbbec-agent-platform:new';api['environment']['PLATFORM_RELEASE_SHA']='d'*40
    api['volumes'].append(copy.deepcopy(BIND))
    effective=dict(v.split('=',1) for v in image_env)
    effective.update(old['services']['platform-api']['environment'])
    if FAE not in effective:api['environment'][FAE]=URL
    return old,new,{'Config':{'Env':list(image_env)}}


@pytest.mark.parametrize('image_env,compose_env', [((),None),((FAE+'='+URL,),None),((FAE+'=http://custom',),None),((),URL),((),'http://custom')])
def test_exact_delta_derives_absent_only_env_from_effective_image(image_env,compose_env):
    h=load();old,new,image=configs(image_env,compose_env)
    added=h.assert_observer_delta(old,new,'orbbec-agent-platform:new','d'*40,image)
    assert added == (not image_env and compose_env is None)
    with pytest.raises(h.ReleaseError,match='compose_parity'):
        h.assert_parity(old,new,'orbbec-agent-platform:new','d'*40)


@pytest.mark.parametrize('mutation', ['source','target','rw','propagation','create','extra','duplicate','delete_old',
    'old_mount','env','custom_env','user','security','network','resource','command','entrypoint','health','other_service','top_level'])
def test_full_parity_rejects_every_nonapproved_delta(mutation):
    h=load();old,new,image=configs();api=new['services']['platform-api'];bind=api['volumes'][-1]
    if mutation=='source':bind['source']='/var/lib'
    elif mutation=='target':bind['target']='/run/platform-runtime/child'
    elif mutation=='rw':bind['read_only']=False
    elif mutation=='propagation':bind['bind']['propagation']='rshared'
    elif mutation=='create':bind['bind']['create_host_path']=True
    elif mutation=='extra':bind['consistency']='cached'
    elif mutation=='duplicate':api['volumes'].append(copy.deepcopy(bind))
    elif mutation=='delete_old':api['volumes'].pop(0)
    elif mutation=='old_mount':api['volumes'][0]['source']='/etc'
    elif mutation=='env':api['environment']['TOKEN']='changed'
    elif mutation=='custom_env':image['Config']['Env']=[FAE+'=http://custom']
    elif mutation=='user':api['user']='root'
    elif mutation=='security':api['cap_drop']=[]
    elif mutation=='network':api['networks']={'public':{}}
    elif mutation=='resource':api['mem_limit']='1g'
    elif mutation=='command':api['command']=['sh']
    elif mutation=='entrypoint':api['entrypoint']=['sh']
    elif mutation=='health':api['healthcheck']['retries']=1
    elif mutation=='other_service':new['services']['platform-postgres']['image']='foreign'
    elif mutation=='top_level':new['networks']['internal']['name']='foreign'
    with pytest.raises(h.ReleaseError):h.assert_observer_delta(old,new,'orbbec-agent-platform:new','d'*40,image)


@pytest.mark.parametrize('target', ['/','/run','/run/','/run/./platform-runtime','/run/platform-runtime','/run/platform-runtime/child'])
@pytest.mark.parametrize('kind', ['volumes','tmpfs'])
def test_old_overlapping_mounts_are_rejected(target,kind):
    h=load();old,new,image=configs()
    for config in [old,new]:
        api=config['services']['platform-api']
        api[kind].append({'type':'bind','source':'/foreign','target':target} if kind=='volumes' else target+':rw')
    with pytest.raises(h.ReleaseError):h.assert_observer_delta(old,new,'orbbec-agent-platform:new','d'*40,image)


def test_absent_original_volume_list_preserved():
    h=load();old,new,image=configs();del old['services']['platform-api']['volumes']
    new['services']['platform-api']['volumes']=[copy.deepcopy(BIND)]
    assert h.assert_observer_delta(old,new,'orbbec-agent-platform:new','d'*40,image)


def test_expected_hostconfig_keeps_all_original_fields_and_unique_order():
    h=load();old=policy_host();old['Binds']=['/data/hr:/hr:ro'];old['Mounts']=[]
    new=h.expected_observer_hostconfig(old)
    assert new=={**old,'Mounts':[HOST_BIND]}
    assert old['Mounts']==[]
    for target in ['/run','/run/platform-runtime/x','/run/platform-runtime']:
        changed=copy.deepcopy(old);changed['Binds'].append('/foreign:'+target+':ro')
        with pytest.raises(h.ReleaseError):h.expected_observer_hostconfig(changed)
    changed=copy.deepcopy(old);changed['Mounts']=[{'Type':'bind','Source':'/other','Target':'/hr'}]
    with pytest.raises(h.ReleaseError):h.expected_observer_hostconfig(changed)


@pytest.mark.parametrize('action',['apply','rollback','inspect-plan'])
@pytest.mark.parametrize('state', [{}, {'profile':'unknown'}, {'profile':PROFILE,'hostconfig_hash_scheme':'unique-mount-lists-v1'},
                                  {'profile':PROFILE,'version':2,'hostconfig_hash_scheme':'unique-mount-lists-observer-bind-v2'}])
def test_bad_profile_state_rejected_before_runtime_or_mutation(tmp_path,monkeypatch,action,state):
    h=load();tmp_path.chmod(0o700);h.save(tmp_path/'state.json',state)
    def forbidden(*args,**kwargs):pytest.fail('invalid state reached runtime/mutation')
    for name in ['command','snapshots','resolve_reference']:monkeypatch.setattr(h,name,forbidden)
    with pytest.raises(h.ReleaseError):h.main([action,'--transaction',str(tmp_path)])


def test_approved_hashes_match_reviewed_repository_artifacts():
    h=load();root=Path(__file__).resolve().parents[2]
    for installed,item in h.OBSERVER_FILES.items():
        assert h.digest((root/item['repository']).read_bytes())==item['sha256']


def snapshot():
    return {'schema_version':1,'generated_at':'2026-10-30T00:00:00+00:00','observations':[
        {'application_id':name,'state':'unknown','observed_at':'2026-10-30T00:00:00+00:00',
         'error':'health_timeout','version':None} for name in ['hr-bot','ai-admin-agent']]}


@pytest.mark.parametrize('mutation',['extra','wrong_id','duplicate','version','error','timestamp','too_many','schema'])
def test_snapshot_metadata_schema_is_bounded_and_fixed(mutation):
    h=load();value=snapshot();h.validate_observer_snapshot(value)
    if mutation=='extra':value['secret']='never print'
    if mutation=='wrong_id':value['observations'][0]['application_id']='foreign'
    if mutation=='duplicate':value['observations'][1]=value['observations'][0]
    if mutation=='version':value['observations'][0]['version']='credential'
    if mutation=='error':value['observations'][0]['error']='credential'
    if mutation=='timestamp':value['generated_at']='not date'
    if mutation=='too_many':value['observations'].append(value['observations'][0])
    if mutation=='schema':value['schema_version']=True
    with pytest.raises(h.ReleaseError):h.validate_observer_snapshot(value)


def unit_output(h,unit):
    props=h.observer_unit_contract(unit)
    omitted={'ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','EnvironmentFiles','TimersCalendar'}
    rows=[k+'='+v for k,v in props.items() if k not in omitted]
    if unit.endswith('.service'):
        rows.append('ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 /usr/local/libexec/orbbec-platform/collect-runtime-observations.py --output /var/lib/orbbec-platform-runtime/snapshot.json ; ignore_errors=no ; start_time=[Fri 2026-10-30 CST] ; stop_time=[Fri 2026-10-30 CST] ; pid=123 ; code=exited ; status=0 }')
    else:
        rows.extend(['TimersMonotonic={ OnBootUSec=10s ; next_elapse=10s }',
                     'TimersMonotonic={ OnUnitActiveUSec=30s ; next_elapse=3d 10s }'])
    return ('\n'.join(rows)+'\n').encode()


@pytest.mark.parametrize('mutation',['dropin','reload','exec','hardening','timer','duplicate','missing'])
def test_loaded_unit_drift_is_rejected(monkeypatch,mutation):
    h=load();unit='orbbec-platform-runtime-observer.'+('timer' if mutation=='timer' else 'service')
    raw=unit_output(h,unit);monkeypatch.setattr(h,'command',lambda args:raw)
    original=h.observer_effective_unit(unit)
    if mutation=='dropin':raw=raw.replace(b'DropInPaths=\n',b'DropInPaths=/evil\n')
    if mutation=='reload':raw=raw.replace(b'NeedDaemonReload=no',b'NeedDaemonReload=yes')
    if mutation=='exec':raw=raw.replace(b'--output /var/lib/',b'--output /tmp/')
    if mutation=='hardening':raw=raw.replace(b'ProtectSystem=strict',b'ProtectSystem=no')
    if mutation=='timer':raw=raw.replace(b'OnUnitActiveUSec=30s',b'OnUnitActiveUSec=60s')
    if mutation=='duplicate':raw+=b'DynamicUser=yes\n'
    if mutation=='missing':raw=raw.replace(b'DynamicUser=yes\n',b'')
    with pytest.raises(h.ReleaseError):h.observer_effective_unit(unit)
    assert original


def test_effective_unit_excludes_only_volatile_execution_fields(monkeypatch):
    h=load();unit='orbbec-platform-runtime-observer.service';raw=unit_output(h,unit)
    monkeypatch.setattr(h,'command',lambda args:raw)
    before=h.observer_effective_unit(unit)
    raw=raw.replace(b'pid=123',b'pid=456').replace(b'status=0',b'status=1').replace(b'start_time=[Fri 2026-10-30 CST]',b'start_time=[n/a]')
    assert h.observer_effective_unit(unit)==before


@pytest.fixture
def transaction(tmp_path,monkeypatch):
    h=load();private=tmp_path/'private';private.mkdir(mode=0o700);monkeypatch.setattr(h,'PRIVATE',private)
    root=tmp_path/'transaction';root.mkdir(mode=0o700)
    old,new,img=configs();oldref=old['services']['platform-api']['image'];mh='f'*64
    newref=h.application_reference('d'*40,mh);new['services']['platform-api']['image']=newref
    oldimage='sha256:'+'a'*64;newimage='sha256:'+'c'*64
    host=policy_host();host['Binds']=['/data/hr:/hr:ro'];host['Mounts']=[]
    def live(config,image,cid,candidate):
        api=config['services']['platform-api']
        return {'Name':'/api','Id':cid,'Image':image,'RestartCount':0,
            'Config':{'Image':api['image'],'Labels':{'com.docker.compose.project':h.PROJECT,'com.docker.compose.service':h.SERVICE},
                'Env':['PATH=/bin']+[k+'='+v for k,v in api['environment'].items()],
                'Cmd':api['command'],'Entrypoint':None,'User':'10001','WorkingDir':'/app/backend','Healthcheck':health_policy()},
            'HostConfig':h.expected_observer_hostconfig(host) if candidate else copy.deepcopy(host),
            'Mounts':[{'Type':'bind','Source':v['source'],'Destination':v['target'],'RW':False,'Propagation':'rprivate'} for v in api['volumes']],
            'NetworkSettings':{'Networks':{'orbbec-agent-platform-internal':{'Aliases':['api','platform-api']}}},
            'State':{'Running':True,'Health':{'Status':'healthy'}}}
    oldlive=live(old,oldimage,'old',False);newlive=live(new,newimage,'new',True)
    baseline={'/api':{'id':'old'},'/other':{'id':'unchanged'}}
    state={'profile':PROFILE,'version':2,'hostconfig_hash_scheme':h.OBSERVER_SCHEME,
        'files':['/one','/two'],'working_dir':'/release','project':h.PROJECT,'env_file':'/private/platform.env',
        'inputs':{},'guardrails':{},'old_config':h.digest(h.canonical(oldlive['Config'])),'old_hostconfig_raw':host,'old_hostconfig':h.hostconfig_hash(host),
        'candidate_hostconfig':h.hostconfig_hash(newlive['HostConfig']),'source_receipt':{},'fae_added':True,
        'baseline_containers':baseline,'api_name':'/api','old_id':'old','old_image':oldimage,'new_image':newimage,
        'old_image_reference':oldref,'new_image_reference':newref,'manifest_sha256':mh,'old_release':'b'*40,'release':'d'*40,
        'before_config':h.digest(h.canonical(old)),'after_config':h.digest(h.canonical(new)),
        'owner':'unique-owner','deployment_id':'e'*32}
    h.save(root/'state.json',state)
    # Source attestation is separately tested; transaction cases isolate Docker/OS boundaries.
    monkeypatch.setattr(h,'validate_source_receipt',lambda receipt:None)
    source_checks=[];monkeypatch.setattr(h,'assert_observer_source',lambda receipt:source_checks.append(True))
    monkeypatch.setattr(h,'input_hashes',lambda state:{})
    monkeypatch.setattr(h,'guardrails',lambda receipt:None)
    monkeypatch.setattr(h,'render',lambda state,path:old if 'rollback' in str(path) else new)
    monkeypatch.setattr(h,'inspect_image',lambda image:{'Config':{'Env':['PATH=/bin'],'User':'10001','WorkingDir':'/app/backend'}})
    calls=[]
    monkeypatch.setattr(h,'resolve_reference',lambda ref,image:None)
    monkeypatch.setattr(h,'command',lambda args:calls.append(args))
    return h,root,state,oldlive,newlive,baseline,calls,source_checks


@pytest.mark.parametrize('mode',['apply','running_candidate','stopped_candidate','stopped_old','absent'])
def test_transaction_full_hostconfig_recovery_matrix(transaction,monkeypatch,mode):
    h,root,state,old,new,baseline,calls,source_checks=transaction
    rollback=mode!='apply';pre=old if mode in ('apply','stopped_old') else new
    if mode.startswith('stopped'):
        pre['State']['Running']=False;pre['NetworkSettings']['Networks']={}
    if mode=='absent':pre=None
    if mode not in ('apply','running_candidate'):
        h.save(root/'apply.started.json',{'action':'apply','image_id':state['new_image'],'image_reference':state['new_image_reference'],
            'release_sha':state['release'],'owner':state['owner'],'deployment_id':state['deployment_id']})
        h.locks(state,True)
    after=copy.deepcopy(old if rollback else new)
    after['State']['Running']=True
    after['NetworkSettings']['Networks']={'orbbec-agent-platform-internal':{'Aliases':['api','platform-api']}}
    before=baseline if mode=='apply' else {'/other':{'id':'unchanged'},**({'/api':{'id':pre['Id']}} if pre else {})}
    sequence=iter([(before,[pre] if pre else []),(before,[pre] if pre else []),(baseline,[after])])
    monkeypatch.setattr(h,'snapshots',lambda:next(sequence))
    h.transact(root,rollback)
    assert len(calls)==1 and calls[0][-1]=='platform-api'
    assert (root/('rollback' if rollback else 'apply')).with_suffix('.complete.json').exists()
    assert not list(h.PRIVATE.iterdir())
    assert len(source_checks)==(0 if rollback else 1)


@pytest.mark.parametrize('mutation',['host','mount','tag','id','env','receipt','foreign_lock','others','source'])
def test_recovery_and_apply_rejections_never_recreate(transaction,monkeypatch,mutation):
    h,root,state,old,new,baseline,calls,source_checks=transaction
    rollback=mutation!='source';pre=old if mutation=='id' or not rollback else new
    if rollback:
        pre['State']['Running']=False;pre['NetworkSettings']['Networks']={}
        receipt={'action':'apply','image_id':state['new_image'],'image_reference':state['new_image_reference'],
                 'release_sha':state['release'],'owner':state['owner'],'deployment_id':state['deployment_id']}
        if mutation=='receipt':receipt['deployment_id']='foreign'
        h.save(root/'apply.started.json',receipt);h.locks(state,True)
    if mutation=='host':pre['HostConfig']['ArbitraryUnmappedField']='changed'
    if mutation=='mount':pre['Mounts'][-1]['Propagation']='rshared'
    if mutation=='tag':pre['Config']['Image']='orbbec-agent-platform:foreign'
    if mutation=='id':pre['Id']='foreign'
    if mutation=='env':pre['Config']['Env'].append('FOREIGN=secret')
    if mutation=='foreign_lock':(h.PRIVATE/'agent-brain-action.lock'/'owner').write_text('foreign\n')
    before=copy.deepcopy(baseline)
    if mutation=='others':before['/other']['id']='changed'
    if mutation=='source':
        def changed(receipt):raise h.ReleaseError('observer_source_changed')
        monkeypatch.setattr(h,'assert_observer_source',changed)
    monkeypatch.setattr(h,'snapshots',lambda:(before,[pre]))
    with pytest.raises(h.ReleaseError):h.transact(root,rollback)
    assert calls==[]
    assert not (root/'apply.complete.json').exists()


def test_unknown_plan_profile_rejected_before_artifact_or_output_operations(tmp_path,monkeypatch):
    h=load()
    def forbidden(*args):pytest.fail('unknown profile reached artifact/output operations')
    monkeypatch.setattr(h,'validate_stage',forbidden)
    with pytest.raises(h.ReleaseError,match='release_profile'):
        h.plan('unused','unused','unused',tmp_path/'output',profile='arbitrary')
    assert not (tmp_path/'output').exists()


@pytest.fixture
def source_fixture(tmp_path,monkeypatch):
    """Real temporary file IO; synthetic root UID and systemctl boundary."""
    import os
    import stat
    h=load();base=tmp_path/'host';base.mkdir(mode=0o755)
    source=base/'public';terminal=base/'private'/'runtime';terminal.parent.mkdir(mode=0o700)
    terminal.mkdir(mode=0o755);terminal.chmod(0o755);source.symlink_to(terminal)
    monkeypatch.setattr(h,'OBSERVER_SOURCE',str(source));monkeypatch.setattr(h,'OBSERVER_RESOLVED',str(terminal))
    repo=Path(__file__).resolve().parents[2];files={}
    for index,item in enumerate(h.OBSERVER_FILES.values()):
        target=base/('artifact'+str(index));target.write_bytes((repo/item['repository']).read_bytes());target.chmod(item['mode'])
        files[str(target)]=item
    monkeypatch.setattr(h,'OBSERVER_FILES',files)
    snap=terminal/'snapshot.json';snap.write_text(json.dumps(snapshot()));snap.chmod(0o644)
    original_lstat=Path.lstat
    def root_metadata(path):
        metadata=original_lstat(path);parts=list(metadata);parts[4]=0
        if path in base.parents:parts[0]=stat.S_IFDIR|0o755
        return os.stat_result(parts)
    monkeypatch.setattr(Path,'lstat',root_metadata)
    monkeypatch.setattr(h.os,'geteuid',lambda:0)
    monkeypatch.setattr(h,'command',lambda args:unit_output(h,args[-1]))
    return h,source,terminal,snap,files,base


def test_source_receipt_allows_snapshot_replacement_and_dynamic_ownership(source_fixture,monkeypatch):
    h,source,terminal,snap,files,base=source_fixture
    receipt=h.observer_source_receipt();h.validate_source_receipt(receipt)
    replacement=terminal/'replacement';replacement.write_text(json.dumps(snapshot()));replacement.chmod(0o644)
    replacement.replace(snap)
    receipt['diagnostic_ownership']={'uid':65535,'gid':65535}
    h.assert_observer_source(receipt)
    assert receipt['resolved']==str(terminal)
    # Fixed direct directory is also allowed with its own fresh receipt.
    source.unlink();source.mkdir(mode=0o755);source.chmod(0o755)
    direct=source/'snapshot.json';direct.write_text(json.dumps(snapshot()));direct.chmod(0o644)
    assert h.observer_source_receipt()['link'] is None


@pytest.mark.parametrize('mutation',['link','source_mode','source_inode','parent_mode','script_bytes','script_mode','script_inode',
                                     'unit_bytes','snapshot_mode','snapshot_symlink','snapshot_fifo','snapshot_large','snapshot_extra'])
def test_source_receipt_rejects_filesystem_drift(source_fixture,mutation):
    import os
    h,source,terminal,snap,files,base=source_fixture;receipt=h.observer_source_receipt()
    script=Path(next(iter(files)))
    if mutation=='link':source.unlink();source.symlink_to(base)
    elif mutation=='source_mode':terminal.chmod(0o777)
    elif mutation=='source_inode':
        terminal.rename(base/'saved');terminal.mkdir(mode=0o755);terminal.chmod(0o755)
        snap.write_text(json.dumps(snapshot()));snap.chmod(0o644)
    elif mutation=='parent_mode':terminal.parent.chmod(0o777)
    elif mutation=='script_bytes':script.write_text('foreign')
    elif mutation=='script_mode':script.chmod(0o777)
    elif mutation=='script_inode':
        copy=base/'newscript';copy.write_bytes(script.read_bytes());copy.chmod(0o755);copy.replace(script)
    elif mutation=='unit_bytes':Path(list(files)[1]).write_text('foreign')
    elif mutation=='snapshot_mode':snap.chmod(0o666)
    elif mutation=='snapshot_symlink':snap.unlink();snap.symlink_to(script)
    elif mutation=='snapshot_fifo':snap.unlink();os.mkfifo(snap,0o644)
    elif mutation=='snapshot_large':snap.write_bytes(b' '*16385)
    elif mutation=='snapshot_extra':snap.write_text(json.dumps({**snapshot(),'secret':'never output'}))
    with pytest.raises((h.ReleaseError,OSError)):h.assert_observer_source(receipt)


def test_plan_produces_profile_state_from_effective_environment_and_inspect_hostconfig(tmp_path,monkeypatch):
    h=load();root=tmp_path/'output';stage=tmp_path/'stage';stage.mkdir(mode=0o700)
    old,new,image=configs((FAE+'=http://custom',));old_api=old['services']['platform-api']
    old_id='sha256:'+'a'*64;new_id='sha256:'+'c'*64;manifest={'base_image_id':old_id,'release_sha':'d'*40}
    h.save(stage/'manifest.json',manifest);mh=h.digest((stage/'manifest.json').read_bytes())
    newref=h.application_reference('d'*40,mh);new['services']['platform-api']['image']=newref
    recipe={'project':h.PROJECT,'working_dir':'/release','env_file':str(h.PRIVATE/'platform.env'),
            'files':['/one','/two'],'image_id':old_id,'runtime_release_sha':'b'*40}
    labels={'com.docker.compose.project':h.PROJECT,'com.docker.compose.service':h.SERVICE,
            'com.docker.compose.project.config_files':'/one,/two','com.docker.compose.project.working_dir':'/release'}
    live={'Id':'old','Image':old_id,'Name':'/api','Config':{'Image':old_api['image'],'Labels':labels,
          'Env':[FAE+'=http://custom','PLATFORM_RELEASE_SHA='+'b'*40]},'HostConfig':{'Binds':['/a:/a:ro'],'Mounts':[]},'State':{'Running':True}}
    api={'id':'old','image_id':old_id,'config_sha256':h.digest(h.canonical(live['Config'])),
         'hostconfig_sha256':h.digest(h.canonical(live['HostConfig'])),'compose_input_files':[],
         'compose_labels':{'com.docker.compose.service':h.SERVICE}}
    h.save(stage/'readiness.json',{'containers':[api]})
    h.save(tmp_path/'recipe',recipe);h.save(tmp_path/'source',{})
    h.save(tmp_path/'build',{'base_image_id':old_id,'release_sha':'d'*40,'manifest_sha256':mh,'image_reference':newref,'image_id':new_id})
    monkeypatch.setattr(h,'validate_stage',lambda root:manifest)
    monkeypatch.setattr(h,'snapshots',lambda:({'/api':{'id':'old'}},[live]))
    monkeypatch.setattr(h,'resolve_reference',lambda *args:None)
    monkeypatch.setattr(h,'guardrails',lambda *args:None)
    monkeypatch.setattr(h,'assert_derived_image',lambda *args:None)
    monkeypatch.setattr(h,'assert_live_contract',lambda *args:None)
    monkeypatch.setattr(h,'inspect_image',lambda image_id:image)
    monkeypatch.setattr(h,'input_hashes',lambda state:{})
    checks=[];monkeypatch.setattr(h,'assert_observer_source',lambda receipt:checks.append(True))
    monkeypatch.setattr(h,'validate_source_receipt',lambda receipt:None)
    monkeypatch.setattr(h,'render',lambda state,path:old if 'rollback' in str(path) else new)
    h.plan(stage,tmp_path/'build',tmp_path/'recipe',root,PROFILE,tmp_path/'source')
    state=h.read_json(root/'state.json');candidate=h.read_json(root/'compose.candidate.json')['services'][h.SERVICE]
    assert state['profile']==PROFILE and state['fae_added'] is False and checks==[True]
    assert candidate['volumes']==[BIND] and FAE not in candidate['environment']
    assert state['candidate_hostconfig']==h.hostconfig_hash(h.expected_observer_hostconfig(live['HostConfig']))
    assert state['old_hostconfig_raw']==live['HostConfig']
    h.main(['inspect-plan','--transaction',str(root)])
    assert checks==[True,True]


@pytest.mark.parametrize('mutation',['candidate_hash','raw_host','fae_flag','source_schema'])
def test_state_tampering_fails_before_recreate(transaction,monkeypatch,mutation):
    h,root,state,old,new,baseline,calls,checks=transaction
    if mutation=='candidate_hash':state['candidate_hostconfig']='0'*64
    elif mutation=='raw_host':state['old_hostconfig_raw']['Unknown']='changed'
    elif mutation=='fae_flag':state['fae_added']=False
    elif mutation=='source_schema':state['source_receipt']={'unknown':'data'}
    (root/'state.json').unlink();h.save(root/'state.json',state)
    if mutation=='source_schema':
        def validate(receipt):raise h.ReleaseError('observer_receipt')
        monkeypatch.setattr(h,'validate_source_receipt',validate)
    monkeypatch.setattr(h,'snapshots',lambda:(baseline,[old]))
    with pytest.raises(h.ReleaseError):h.transact(root)
    assert not calls


@pytest.mark.parametrize('mutation',['missing_receipt','missing_locks','foreign_action','foreign_owner','foreign_tag','foreign_release','foreign_image'])
def test_absent_recovery_requires_own_apply_receipt_and_locks(transaction,monkeypatch,mutation):
    h,root,state,old,new,baseline,calls,checks=transaction
    receipt={'action':'apply','image_id':state['new_image'],'image_reference':state['new_image_reference'],
             'release_sha':state['release'],'owner':state['owner'],'deployment_id':state['deployment_id']}
    key={'foreign_action':'action','foreign_owner':'owner','foreign_tag':'image_reference','foreign_release':'release_sha','foreign_image':'image_id'}.get(mutation)
    if key:receipt[key]='foreign'
    if mutation!='missing_receipt':h.save(root/'apply.started.json',receipt)
    if mutation!='missing_locks':h.locks(state,True)
    monkeypatch.setattr(h,'snapshots',lambda:({'/other':{'id':'unchanged'}},[]))
    with pytest.raises((h.ReleaseError,OSError)):h.transact(root,True)
    assert not calls


@pytest.mark.parametrize('failure',['command','posthost','postmount','postother','posttag'])
def test_profile_failure_retains_locks_and_bound_started_receipt(transaction,monkeypatch,failure):
    h,root,state,old,new,baseline,calls,checks=transaction
    def execute(args):
        calls.append(args)
        if failure=='command':raise h.ReleaseError('command_failed')
    monkeypatch.setattr(h,'command',execute)
    after=copy.deepcopy(baseline)
    if failure=='posthost':new['HostConfig']['Unknown']='drift'
    if failure=='postmount':new['Mounts'][-1]['Source']='/var/lib/private/orbbec-platform-runtime'
    if failure=='postother':after['/other']['id']='foreign'
    if failure=='posttag':new['Config']['Image']='orbbec-agent-platform:foreign'
    sequence=iter([(baseline,[old]),(baseline,[old]),(after,[new])])
    monkeypatch.setattr(h,'snapshots',lambda:next(sequence))
    with pytest.raises(h.ReleaseError):h.transact(root)
    assert len(calls)==1 and (h.PRIVATE/'agent-brain-action.lock').exists()
    h.locks(state,False)
    receipt=h.read_json(root/'apply.started.json')
    assert receipt['owner']==state['owner'] and receipt['deployment_id']==state['deployment_id']
    assert not (root/'apply.complete.json').exists()


def test_stopped_old_requires_full_original_config(transaction,monkeypatch):
    h,root,state,old,new,baseline,calls,checks=transaction
    state['old_config']=h.digest(h.canonical(old['Config']))
    (root/'state.json').unlink();h.save(root/'state.json',state)
    old['State']['Running']=False;old['Config']['Labels']['foreign']='foreign'
    h.save(root/'apply.started.json',{'action':'apply','image_id':state['new_image'],'image_reference':state['new_image_reference'],
        'release_sha':state['release'],'owner':state['owner'],'deployment_id':state['deployment_id']})
    h.locks(state,True);monkeypatch.setattr(h,'snapshots',lambda:(baseline,[old]))
    with pytest.raises(h.ReleaseError,match='runtime_changed'):h.transact(root,True)
    assert not calls


@pytest.mark.parametrize('mutation',['unit_exec','unit_timer','file_extra','file_identity','directory_type','diagnostic_type'])
def test_source_receipt_schema_does_not_authorize_its_own_values(source_fixture,mutation):
    h,source,terminal,snap,files,base=source_fixture;receipt=h.observer_source_receipt()
    if mutation=='unit_exec':receipt['units']['orbbec-platform-runtime-observer.service']['ExecStart']='foreign'
    elif mutation=='unit_timer':receipt['units']['orbbec-platform-runtime-observer.timer']['TimersMonotonic']=['foreign']
    elif mutation=='file_extra':receipt['files'][next(iter(files))]['extra']='foreign'
    elif mutation=='file_identity':receipt['files'][next(iter(files))]['inode']='not integer'
    elif mutation=='directory_type':receipt['directory']['inode']=True
    elif mutation=='diagnostic_type':receipt['diagnostic_ownership']='foreign'
    with pytest.raises(h.ReleaseError):h.validate_source_receipt(receipt)


def test_systemd_empty_array_omissions_do_not_allow_nonempty_commands(monkeypatch):
    h=load();unit='orbbec-platform-runtime-observer.service';raw=unit_output(h,unit)
    commands=[]
    def execute(args):commands.append(args);return raw
    monkeypatch.setattr(h,'command',execute)
    h.observer_effective_unit(unit)
    assert '--all' in commands[0]
    raw+=b'ExecStartPre={ path=/evil ; argv[]=/evil ; ignore_errors=no }\n'
    with pytest.raises(h.ReleaseError):h.observer_effective_unit(unit)


def test_dynamic_terminal_uid_change_is_diagnostic_but_script_owner_is_fixed(source_fixture,monkeypatch):
    import os
    h,source,terminal,snap,files,base=source_fixture;receipt=h.observer_source_receipt()
    previous=Path.lstat;changed_path=terminal
    def changed(path):
        metadata=previous(path)
        if path==changed_path:
            parts=list(metadata);parts[4]=60000;return os.stat_result(parts)
        return metadata
    monkeypatch.setattr(Path,'lstat',changed)
    h.assert_observer_source(receipt)
    changed_path=Path(next(iter(files)))
    with pytest.raises(h.ReleaseError,match='observer_code_permission'):h.assert_observer_source(receipt)
