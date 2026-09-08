"""Actual signed HTTP/MCP/worker execution; the native/model provider is substituted."""
import os
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from test_hr_turn_scope_v6_database import scoped_database,control_database,_seed_candidate_scope,repository
from test_execution_worker_store import worker_database
from test_hr_tools_v6_http import ToolLoop
from tests.helpers.hr_web_loop import wait_until


def worker_factory(attempts,adapter,repository,database_url):
    from app.hr.role_package import HrRolePackages
    from app.agent_brain.direct_worker import DirectWorker
    adapter.role_packages=HrRolePackages(Path(os.environ['HR_WEB_FIXTURE_ROOT'])/'roles','a'*40)
    return DirectWorker(attempts,adapter,lease_seconds=10)

class V6Loop(ToolLoop):
    worker_factory=staticmethod(worker_factory)

@pytest.mark.skipif(not os.environ.get('HR_WEB_METABOT_ORIGIN'),reason='owned MetaBot fixture required')
def test_actual_v6_tools_and_scope_switch(scoped_database,worker_database,tmp_path):
    env=scoped_database
    ids=_seed_candidate_scope(env)
    conversation=repository(env).ensure_direct_conversation_shell(ids['owner'],uuid4(),direct_agent_id='hr-bot',title='Owned v6 execution')
    second=uuid4()
    with psycopg.connect(env['admin']) as connection:
        connection.execute("update platform_control.conversations set execution_owner='worker_direct',route_epoch=7 where conversation_id=%s",(conversation.conversation_id,))
        connection.execute("insert into platform_hr.positions(position_id,owner_internal_user_id,client_request_id,source_kind,title) values(%s,%s,%s,'manual','Owned second position')",(second,ids['owner'],uuid4()))
    loop=V6Loop(env,ids['owner'],conversation.conversation_id)
    controls=Path(os.environ['HR_WEB_FIXTURE_ROOT'])
    try:
        loop.start_workers(worker_database,os.environ['HR_WEB_METABOT_ORIGIN'],int(os.environ['HR_WEB_CALLBACK_PORT']),tmp_path/'machine')
        loop.wait_ready()
        for index,position in enumerate([ids['position'],second],1):
            body={'text':f'岗位 {index} 独有讨论','scope':{'positionId':str(position),'positionCandidateIds':[],'attachmentIds':[]}}
            request_id=str(uuid4())
            submitted=loop.client.post(f'/api/v1/conversations/{loop.conversation_id}/messages',json=body,headers={'Idempotency-Key':request_id})
            assert submitted.status_code==201,submitted.text
            turn=submitted.json()['turn']['turn_id']
            duplicate=loop.client.post(f'/api/v1/conversations/{loop.conversation_id}/messages',json=body,headers={'Idempotency-Key':request_id})
            assert duplicate.json()['turn']['turn_id']==turn
            def tools_ready():
                error=controls/'provider-fixture-error'
                assert not error.exists(),error.read_text() if error.exists() else ''
                snapshot=loop.snapshot(turn)
                assert snapshot['attempt']['status'] not in ('failed','cancelled','interrupted'),snapshot
                return (controls/f'tools-{index}.json').exists()
            wait_until(tools_ready,description='signed MCP result',timeout=40)
            if index==1:
                binding=wait_until(lambda:value if (value:=loop.binding(turn)) and value['accepted_at'] else None)
                loop.restart_direct()
                wait_until(lambda:(value:=loop.binding(turn)) and value['lease_epoch']>binding['lease_epoch'],description='actual coordinator recovery',timeout=25)
                def renewed():
                    import json
                    path=controls/'tools'/str(binding['command_id'])/'capability.json'
                    current=json.loads(path.read_text())
                    return current['leaseEpoch']>binding['lease_epoch']
                try:
                    wait_until(renewed,description='native capability renewal',timeout=10)
                except AssertionError:
                    from app.execution_relay.recovery_v5 import recovery_work
                    from app.execution_relay.repository import ExecutionRelayRepository
                    from app.agent_brain.direct_command_binding import DirectCommandBindingRepository
                    from test_agent_brain_conversation_repository import _codec
                    with psycopg.connect(env['admin']) as connection:
                        worker=connection.execute('select transport_worker_id from platform_control.direct_command_bindings where command_id=%s',(binding['command_id'],)).fetchone()[0]
                    # Diagnostic on the same owned database, no execution or fabricated event.
                    work=recovery_work(DirectCommandBindingRepository(ExecutionRelayRepository(env['urls']['platform_control_app'],content_codec=_codec())),worker)
                    raise AssertionError('Recovery did not renew: work='+('available' if work else 'absent'))

            (controls/f'release-{index}').touch()
            try:
                snapshot=loop.drive_until_available(turn)
            except AssertionError as error:
                fixture_error=controls/'provider-fixture-error'
                with psycopg.connect(env['admin']) as connection:
                    sources=connection.execute('select event_type,seq from platform_control.v5_source_events where run_id=%s order by seq',(loop.binding(turn)['run_id'],)).fetchall()
                raise AssertionError(str(error)+'; sources='+str(sources)+'; '+(fixture_error.read_text() if fixture_error.exists() else 'no provider fixture error')) from error
            assert snapshot['answer']['content']=='岗位分析已保存。'
            loop.wait_terminal(turn)
            assert loop.assistant_count(turn)==1
            import json
            ref=json.loads((controls/f'tools-{index}.json').read_text())['resultRef']
            result=loop.client.get('/api/v1/hr/results/'+ref['resultId'])
            assert result.status_code==200,result.text
            assert result.json()['turnId']==turn
            evidence=loop.client.get(f'/api/v1/hr/conversations/{loop.conversation_id}/results')
            assert evidence.status_code==200,evidence.text
            reads=[item for item in evidence.json()['knowledgeReads'] if item['turnId']==turn]
            assert len(reads)==1 and reads[0]['methodId']=='job-and-context' and reads[0]['teamCommit']=='a'*40
            if index==1:
                loop.restart_api_process()
                assert loop.client.get('/api/v1/hr/results/'+ref['resultId']).json()==result.json()
        assert '岗位 1 独有讨论' not in (controls/'prompt-2.json').read_text()
    finally:
        loop.close()
