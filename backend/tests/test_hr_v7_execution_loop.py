"""Owned full v7 execution through signed relay and MCP; provider substituted."""
import os,json
from pathlib import Path
from uuid import uuid4
import psycopg,pytest
from test_hr_turn_scope_v6_database import scoped_database,control_database,_seed_candidate_scope,repository
from test_execution_worker_store import worker_database
from test_hr_v6_execution_loop import V6Loop
from tests.helpers.hr_web_loop import wait_until

@pytest.mark.skipif(not os.environ.get('HR_WEB_METABOT_ORIGIN'),reason='owned MetaBot fixture required')
def test_v7_full_execution_with_explicit_result_input(scoped_database,worker_database,tmp_path):
    env=scoped_database;ids=_seed_candidate_scope(env)
    chat=repository(env).ensure_direct_conversation_shell(ids['owner'],uuid4(),direct_agent_id='hr-bot',title='Owned v7 result continuation')
    with psycopg.connect(env['admin']) as connection:
        connection.execute("update platform_control.conversations set execution_owner='worker_direct',route_epoch=7 where conversation_id=%s",(chat.conversation_id,))
    loop=V6Loop(env,ids['owner'],chat.conversation_id);controls=Path(os.environ['HR_WEB_FIXTURE_ROOT']);refs=[]
    try:
        loop.start_workers(worker_database,os.environ['HR_WEB_METABOT_ORIGIN'],int(os.environ['HR_WEB_CALLBACK_PORT']),tmp_path/'machine')
        loop.wait_ready()
        for index in (1,2):
            body={'text':f'第 {index} 轮基于本次参考继续分析','scope':{'positionId':str(ids['position']),'positionCandidateIds':[],'attachmentIds':[]},'inputResultRefs':refs}
            response=loop.client.post(f'/api/v1/conversations/{chat.conversation_id}/messages',json=body,headers={'Idempotency-Key':str(uuid4())})
            assert response.status_code==201,response.text
            turn=response.json()['turn']['turn_id']
            def ready():
                error=controls/'provider-fixture-error'
                assert not error.exists(),error.read_text() if error.exists() else ''
                assert loop.snapshot(turn)['attempt']['status'] not in ('failed','cancelled','interrupted'),loop.snapshot(turn)
                return (controls/f'tools-{index}.json').exists()
            wait_until(ready,timeout=40,description='v7 signed tool result')
            (controls/f'release-{index}').touch()
            snapshot=loop.drive_until_available(turn)
            loop.wait_terminal(turn)
            snapshot=loop.snapshot(turn)
            assert snapshot['attempt']['status']=='completed',snapshot
            ref=json.loads((controls/f'tools-{index}.json').read_text())['resultRef']
            result=loop.client.get('/api/v1/hr/results/'+ref['resultId']);assert result.status_code==200,result.text
            assert result.json()['turnId']==turn
            refs=[ref]
        assert (controls/'read-prior-2.json').exists()
    finally: loop.close()
