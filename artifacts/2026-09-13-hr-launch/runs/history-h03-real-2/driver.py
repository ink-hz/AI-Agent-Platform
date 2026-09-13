"""One fresh H03 real-provider replay after correcting the harness contract."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'backend'))
from tests.helpers.hr_history_replay import BoundedPort,real_configuration,replay_case
from app.hr_agent.model import ConfiguredHttpModelPort

output=ROOT/'artifacts/2026-09-13-hr-launch/history-h03-real-run-2'
profile,output=real_configuration({'HR_HISTORY_REAL_MODEL':'1','HR_HISTORY_REAL_PROFILE_FILE':'/Users/neo/Developer/work/AI-Agent-Platform/.hr-agent/provider.json','HR_HISTORY_EVIDENCE_DIR':str(output)})
port=BoundedPort(ConfiguredHttpModelPort.from_mapping(profile))
with tempfile.TemporaryDirectory(prefix='hr-history-h03-real-') as temporary:
 report=replay_case(ROOT/'artifacts/2026-09-13-hr-launch/history/replay-v2','H03',Path(temporary)/'runtime',output/'H03',profile=profile,port=port)
summary={'case_id':'H03','status':report['status'],'executed_turns':len(report['turns']),'generation_calls':port.calls,'observations':port.observations,'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'professional_acceptance':'pending independent output review','production_test':False}
(output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='observations'},ensure_ascii=False))
raise SystemExit(0 if report['status']=='completed' else 1)
