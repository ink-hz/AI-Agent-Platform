create unique index one_terminal_audit_event_per_request
  on platform_control.audit_events (request_id)
  where result in ('completed', 'failed');

create function platform_control.refuse_mutation_after_failed_terminal()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if exists (
    select 1 from platform_control.audit_events event
    where event.request_id = new.operation_id and event.result = 'failed'
  ) then
    raise check_violation using message = 'operation already terminal failed';
  end if;
  return new;
end
$function$;

create trigger refuse_mutation_after_failed_terminal
before insert on platform_control.management_mutations
for each row execute function platform_control.refuse_mutation_after_failed_terminal();

revoke all on function platform_control.refuse_mutation_after_failed_terminal()
from public;
