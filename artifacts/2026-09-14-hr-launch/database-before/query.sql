BEGIN READ ONLY; SET LOCAL lock_timeout='2s'; SET LOCAL statement_timeout='3s';
SELECT json_build_object(
 'ledger', (select json_agg(row_to_json(m) order by version) from (select version,sha256 from platform_control.schema_migrations) m),
 'draft_states', (select json_agg(row_to_json(d)) from (select state,count(*) as count from platform_hr.candidate_drafts group by state order by state) d),
 'hr_job_states', (select json_agg(row_to_json(j)) from (select job_kind,status,count(*) as count from platform_control.execution_jobs where agent_id='hr-bot' group by job_kind,status order by job_kind,status) j),
 'unacknowledged_hr_stops',(select count(*) from platform_control.execution_jobs where agent_id='hr-bot' and stop_requested_status is not null and stop_acknowledged_at is null),
 'hr_active_turns',(select count(*) from platform_control.conversation_turns t join platform_control.conversations c using(conversation_id) where ((c.mode='direct_agent' and c.direct_agent_id='hr-bot') or exists(select 1 from platform_hr.position_conversations pc where pc.conversation_id=c.conversation_id)) and t.status not in ('completed','failed','cancelled','interrupted')),
 'owner_membership', (select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid where r.rolname in ('platform_control_owner','platform_control_owner_preview')),
 'migrator_sessions',(select count(*) from pg_stat_activity where usename in ('platform_control_migrator','platform_control_migrator_preview'))
); COMMIT;
