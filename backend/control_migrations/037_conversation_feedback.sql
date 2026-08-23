create table platform_control.conversation_feedback (
  feedback_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  message_id uuid not null,
  turn_id uuid not null,
  mission_id uuid,
  rating text not null check (rating in ('helpful', 'unhelpful')),
  created_at timestamptz not null default now(),
  unique (owner_internal_user_id, message_id),
  foreign key (conversation_id, message_id)
    references platform_control.conversation_messages(conversation_id, message_id),
  foreign key (conversation_id, turn_id)
    references platform_control.conversation_turns(conversation_id, turn_id),
  foreign key (mission_id)
    references platform_control.missions(mission_id)
);

create index conversation_feedback_created_v37
  on platform_control.conversation_feedback(created_at desc, feedback_id);
create index conversation_feedback_turn_v37
  on platform_control.conversation_feedback(conversation_id, turn_id);

create function platform_control.enforce_conversation_feedback_target_v37()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  target record;
begin
  select conversation.owner_internal_user_id, message.role,
         turn.assistant_message_id, turn.mission_id
    into target
  from platform_control.conversations conversation
  join platform_control.conversation_messages message
    on message.conversation_id=conversation.conversation_id
   and message.message_id=new.message_id
  join platform_control.conversation_turns turn
    on turn.conversation_id=conversation.conversation_id
   and turn.turn_id=new.turn_id
  where conversation.conversation_id=new.conversation_id;

  if not found
     or target.owner_internal_user_id is distinct from new.owner_internal_user_id
     or target.role <> 'assistant'
     or target.assistant_message_id is distinct from new.message_id
     or target.mission_id is distinct from new.mission_id
  then
    raise check_violation using message = 'Conversation feedback target invalid';
  end if;
  return new;
end
$function$;

create trigger enforce_conversation_feedback_target_v37
before insert or update
on platform_control.conversation_feedback
for each row execute function
  platform_control.enforce_conversation_feedback_target_v37();

comment on table platform_control.conversation_feedback is
  'Append-only member rating for an owner-scoped assistant message. The row '
  'contains stable links and rating only; message content is never duplicated.';

revoke all on platform_control.conversation_feedback from public;

do $migration$
declare
  selected_app text;
  role_name text;
begin
  case current_user
    when 'platform_control_owner' then
      selected_app := 'platform_control_app';
    when 'platform_control_owner_preview' then
      selected_app := 'platform_control_app_preview';
    else
      raise insufficient_privilege using
        message = 'control migration must run as an approved owner role';
  end case;
  foreach role_name in array array[
    'platform_control_migrator',
    'platform_control_app',
    'platform_directory_worker',
    'platform_stream_ingest',
    'platform_audit_append',
    'platform_control_maintenance',
    'platform_control_migrator_preview',
    'platform_control_app_preview',
    'platform_directory_worker_preview',
    'platform_stream_ingest_preview',
    'platform_audit_append_preview',
    'platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on platform_control.conversation_feedback from %I',
      role_name
    );
  end loop;

  execute format(
    'grant select,insert on platform_control.conversation_feedback to %I',
    selected_app
  );
end
$migration$;
