"""Real local shell redirection permissions; timeout binary is a local boundary stub."""
import importlib.util
from pathlib import Path
import shlex
import stat
import subprocess
import sys

SCRIPT = Path(__file__).parents[2] / '2026-09-13-hr-launch/production/provision_hr_release.py'

def test_detached_launcher_creates_private_logs_before_job_starts(tmp_path):
    spec=importlib.util.spec_from_file_location('provision',SCRIPT)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    launch=module._remote_launch('a'*64)
    prefix="/bin/bash -c '\n"
    inner=launch.split(prefix,1)[1].split("\n' _ ",1)[0]
    # macOS lacks the Linux /usr/bin/timeout; replace only its process executor,
    # preserving the exact shell redirection and umask under audit.
    inner=inner.replace('/usr/bin/timeout --signal=TERM --kill-after=10s 300','/bin/bash')
    metadata=tmp_path/'metadata';metadata.mkdir(mode=0o700)
    job=tmp_path/'job.sh'
    py="import pathlib,stat; print(stat.S_IMODE(pathlib.Path("+repr(str(metadata/'stdout.log'))+").stat().st_mode))"
    job.write_text(shlex.quote(sys.executable)+' -c '+shlex.quote(py)+'\nprintf "synthetic stderr\\n" >&2\n')
    result=subprocess.run(['/bin/bash','-c','umask 022\n'+inner,'_',str(job),str(metadata)],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert (metadata/'stdout.log').read_text().strip()==str(0o600)
    assert stat.S_IMODE((metadata/'stdout.log').stat().st_mode)==0o600
    assert stat.S_IMODE((metadata/'stderr.log').stat().st_mode)==0o600
    assert (metadata/'exit_code').read_text().strip()=='0'
