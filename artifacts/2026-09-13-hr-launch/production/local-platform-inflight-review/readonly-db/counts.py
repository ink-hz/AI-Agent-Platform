import subprocess,json,pathlib,psycopg,hashlib,datetime
out=pathlib.Path(__file__).parent
paths={'worker':'/Users/agentops/AgentRuntime/private/execution-worker-postgres-dsn','metabot_hr':'/Users/agentops/AgentRuntime/instances/hr-bot/hr-web-v5/private/postgres-url'}
queries={'worker':{'legacy_hr_states':"SELECT state,count(*) FROM execution_worker.local_runs WHERE agent_id='hr-bot' GROUP BY state ORDER BY state",'legacy_hr_pending_outbox':"SELECT count(*) FROM execution_worker.event_outbox e JOIN execution_worker.local_runs r USING(run_id) WHERE r.agent_id='hr-bot' AND e.delivered_at IS NULL",'v5_binding_metadata':"SELECT command_id,run_id,attempt_id,terminal_seq IS NOT NULL,uploaded_through>=terminal_seq FROM execution_worker.v5_callback_runs"},'metabot_hr':{'hr_commands':"SELECT c.command_id,c.run_id,c.attempt_id,c.terminal_kind,c.terminal_event_seq IS NOT NULL,i.state,o.exited_at IS NOT NULL,r.stopped_at IS NOT NULL,r.reconciliation_required FROM hr_runtime.commands c JOIN hr_runtime.sessions s USING(logical_session_id) LEFT JOIN hr_runtime.execution_intents i USING(command_id) LEFT JOIN hr_runtime.executor_ownership o USING(command_id) LEFT JOIN hr_runtime.recovery_evidence r USING(command_id) WHERE s.target_bot='hr-bot'",'hr_active_sessions':"SELECT count(*) FROM hr_runtime.sessions WHERE target_bot='hr-bot' AND active_command_id IS NOT NULL"}}
result={}; bindings={}
for label,path in paths.items():
 try:
  secret=subprocess.check_output(['sudo','-n','-H','-u','agentops','/bin/cat',path],cwd='/tmp',stderr=subprocess.DEVNULL).decode().strip()
  with psycopg.connect(secret,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000') as c:
   data={k:c.execute(sql).fetchall() for k,sql in queries[label].items()}
   bindings[label]=data.pop('v5_binding_metadata' if label=='worker' else 'hr_commands');result[label]=data
 except Exception:result[label]={'status':'unknown','reason':'readonly_query_failed'}
if len(bindings)==2:
 w={tuple(str(x) for x in row[:3]):row[3:] for row in bindings['worker']};rows=[]
 for row in bindings['metabot_hr']:
  key=tuple(str(x) for x in row[:3]);rows.append({'binding_sha256':hashlib.sha256('|'.join(key).encode()).hexdigest(),'terminal_kind':row[3],'has_terminal_event':row[4],'intent_state':row[5],'executor_exit_recorded':row[6],'recovery_stop_recorded':row[7],'reconciliation_required':row[8],'worker_exact_command_run_attempt_match':key in w,'worker_terminal_received':w.get(key,[None,None])[0],'worker_terminal_uploaded':w.get(key,[None,None])[1]})
 result['hr_exact_bindings']=rows
result.update(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),queries=queries,boundary='readonly DB; only platform exact identities hashed, state and aggregate; payloads/tokens not selected; no execution/stop')
(out/'counts.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
