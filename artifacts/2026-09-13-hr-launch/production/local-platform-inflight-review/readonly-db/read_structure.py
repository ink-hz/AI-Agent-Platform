import subprocess,json,pathlib,psycopg
out=pathlib.Path(__file__).parent
paths={'worker':'/Users/agentops/AgentRuntime/private/execution-worker-postgres-dsn','metabot_hr':'/Users/agentops/AgentRuntime/instances/hr-bot/hr-web-v5/private/postgres-url'}
result={}
sql="SELECT table_schema,table_name,column_name,data_type FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema,table_name,ordinal_position"
for label,path in paths.items():
 try:
  secret=subprocess.check_output(['sudo','-n','-H','-u','agentops','/bin/cat',path],cwd='/tmp',stderr=subprocess.DEVNULL).decode().strip()
  with psycopg.connect(secret,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000') as c:
   result[label]={'status':'observed','columns':c.execute(sql).fetchall()}
 except Exception:result[label]={'status':'unknown','reason':'readonly_connection_or_query_failed'}
(out/'structure.json').write_text(json.dumps({'sql':sql,'results':result},indent=2)+'\n');print(json.dumps(result,indent=2))
