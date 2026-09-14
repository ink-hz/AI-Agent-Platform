"""Read-only local exact config/knowledge load; bridge one container credential path.
No credential/content output, no model, no DB; does not prove host mount/UID.
"""
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from app.hr_agent.config import load_hr_agent_settings
from app.hr_agent.knowledge import KnowledgeReleases

private = Path('/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-launch-2026-09-13')
config = private/'release-config'
credential = config/'hr-provider-credential'
fixed = Path('/run/hr-agent-secrets/hr-provider-credential')
env = {'PLATFORM_HR_AGENT_ENABLED':'1','PLATFORM_HR_AGENT_KNOWLEDGE_DIR':str(private/'knowledge-release-fence-v2')}
for key,name in [('PROVIDER_PROFILE_FILE','provider-profile'),('BUDGET_PROFILE_FILE','budget-profile'),('DIAGNOSTIC_PROFILE_FILE','diagnostic-profile'),('RELEASE_POLICY_FILE','release-policy'),('CONTENT_KEYRING_FILE','content-keyring')]:
    env['PLATFORM_HR_AGENT_'+key]=str(config/f'hr-{name}.json')
originals={name:getattr(Path,name) for name in ('is_file','is_symlink','stat')}
def bridge(name):
    def call(path,*args,**kwargs):
        return originals[name](credential if path==fixed else path,*args,**kwargs)
    return call
with tempfile.TemporaryDirectory() as work:
    env['PLATFORM_HR_AGENT_WORK_DIR']=work
    with patch.object(Path,'is_file',bridge('is_file')),patch.object(Path,'is_symlink',bridge('is_symlink')),patch.object(Path,'stat',bridge('stat')):
        settings=load_hr_agent_settings(env)
        knowledge=KnowledgeReleases(settings.knowledge_dir).current()
        knowledge.check()
        assert settings.enabled and settings.release_policy['scope']=='public-only'
        assert knowledge.release_id=='hr-intelligence-ad5f3cac253a6a28d3c764db'
        print(json.dumps({'enabled':True,'personal_materials_authorized':False,'configuration_revision':settings.configuration_revision,'policy_configuration_sha256':settings.release_policy['configuration_sha256'],'knowledge_release':knowledge.release_id,'knowledge_manifest_sha256':knowledge.manifest_sha,'configuration_source_sha256':hashlib.sha256(Path('backend/app/hr_agent/config.py').read_bytes()).hexdigest(),'boundary':'local Path metadata bridge for container credential location; no DB/host/model validation'},sort_keys=True))
