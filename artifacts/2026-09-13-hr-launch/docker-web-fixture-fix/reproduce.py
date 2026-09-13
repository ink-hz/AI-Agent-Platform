"""Reproduce the web-builder filesystem locally without Docker/network access."""
from pathlib import Path
import datetime
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[3]
out = Path(__file__).parent / sys.argv[1]
out.mkdir(exist_ok=False)
sha = lambda raw: hashlib.sha256(raw).hexdigest()
docker = (root / 'deploy/cloud/Dockerfile').read_bytes()
(out / 'Dockerfile').write_bytes(docker)
for name in ('package.json', 'package-lock.json', 'tsconfig.json'):
    (out / name).write_bytes((root / 'webui' / name).read_bytes())
meta = {'head': subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        'command':['npm','run','build'], 'dockerfile_sha256':sha(docker),
        'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'node':subprocess.check_output(['node','--version'],text=True).strip(),
        'npm':subprocess.check_output(['npm','--version'],text=True).strip(),
        'boundary':'Local copy of tracked webui and only Docker-declared backend fixture COPY; existing node_modules symlink; not Alpine Docker or fresh npm ci',
        'source_sha256':{},'backend_copies':[]}
allowed = {'companies.json','company-insta360.json','company-scantech-missing-metrics.json','company-insta360-jobs-page.json'}
with tempfile.TemporaryDirectory(prefix='hr-web-builder-',dir='/tmp') as temporary:
    stage = Path(temporary) / 'src'
    for name in subprocess.check_output(['git','ls-files','webui'],cwd=root,text=True).splitlines():
        source = root / name
        if not source.is_file() or source.is_symlink():
            raise ValueError('unexpected tracked web source')
        destination = stage / name
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(source.read_bytes())
        meta['source_sha256'][name] = sha(source.read_bytes())
    # Mirror additional files actually declared in the Docker web-builder.
    web_stage = docker.decode().split('FROM python:',1)[0].replace('\\\n',' ')
    for line in web_stage.splitlines():
        tokens = shlex.split(line)
        if not tokens or tokens[0] != 'COPY': continue
        if not any(value.startswith('backend/') for value in tokens[1:-1]): continue
        if tokens[-1] != '/src/backend/tests/fixtures/hr_intelligence_company/':
            raise ValueError('unexpected backend fixture destination')
        for name in tokens[1:-1]:
            path = Path(name)
            if str(path.parent) != 'backend/tests/fixtures/hr_intelligence_company' or path.name not in allowed:
                raise ValueError('unexpected backend COPY scope')
            destination = stage / path
            destination.parent.mkdir(parents=True,exist_ok=True)
            data = (root / path).read_bytes();destination.write_bytes(data)
            meta['backend_copies'].append(name);meta['source_sha256'][name]=sha(data)
            saved = out / 'fixtures' / path.name;saved.parent.mkdir(exist_ok=True);saved.write_bytes(data)
    (stage / 'webui/node_modules').symlink_to((root / 'webui/node_modules').resolve(),target_is_directory=True)
    meta['cwd'] = str(stage / 'webui')
    with (out / 'output.log').open('x') as log:
        result=subprocess.run(meta['command'],cwd=stage/'webui',stdout=log,stderr=subprocess.STDOUT)
    meta['exit_code']=result.returncode
    meta['inputs_unchanged']=all(sha((root/name).read_bytes())==expected for name,expected in meta['source_sha256'].items())
    if (stage / 'webui/dist').is_dir():
        meta['built_dist_sha256']={str(p.relative_to(stage/'webui/dist')):sha(p.read_bytes()) for p in sorted((stage/'webui/dist').rglob('*')) if p.is_file()}
meta['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
(out/'command.json').write_text(json.dumps(meta,indent=2)+'\n')
print((out/'output.log').read_text())
sys.exit(result.returncode)
