-- UNNUMBERED DRAFT: explicitly loaded by disposable PostgreSQL tests only.
-- P03a immutable intake ownership; production numbering remains unassigned.
alter table platform_control.conversation_turns
  add column execution_owner text not null default 'legacy_api_v1'
    check (execution_owner in ('legacy_api_v1','worker_direct')),
  add column origin_route_epoch bigint not null default 0
    check (origin_route_epoch >= 0);

create function platform_control.pin_turn_execution_origin()
returns trigger language plpgsql
set search_path = pg_catalog, platform_control
as $function$
declare selected_mode text; selected_agent text;
begin
  if tg_op='UPDATE' then
    if (new.execution_owner,new.origin_route_epoch)
       is distinct from (old.execution_owner,old.origin_route_epoch) then
      raise check_violation using message='turn execution origin is immutable';
    end if;
    return new;
  end if;
  select execution_owner,route_epoch,mode,direct_agent_id
    into new.execution_owner,new.origin_route_epoch,selected_mode,selected_agent
    from platform_control.conversations
    where conversation_id=new.conversation_id for update;
  if new.execution_owner='worker_direct'
     and (selected_mode<>'direct_agent' or selected_agent is distinct from 'hr-bot') then
    raise check_violation using message='worker execution requires HR direct intake';
  end if;
  return new;
end
$function$;

create trigger pin_turn_execution_origin
before insert or update of execution_owner,origin_route_epoch
on platform_control.conversation_turns
for each row execute function platform_control.pin_turn_execution_origin();
