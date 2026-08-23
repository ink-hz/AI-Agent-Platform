create table platform_control.conversations (
  conversation_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  started_by_client_request_id uuid not null,
  mode text not null check (mode in ('brain', 'direct_agent')),
  direct_agent_id text
    check (
      direct_agent_id is null
      or direct_agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
    ),
  title text not null check (char_length(title) between 1 and 160),
  status text not null default 'active'
    check (status in ('active', 'archived')),
  summary_ciphertext bytea,
  summary_key_version integer,
  summary_through_seq integer not null default 0
    check (summary_through_seq >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  check (
    (mode = 'brain' and direct_agent_id is null)
    or (mode = 'direct_agent' and direct_agent_id is not null)
  ),
  check (
    (summary_ciphertext is null and summary_key_version is null)
    or (
      octet_length(summary_ciphertext) between 29 and 1048576
      and summary_key_version > 0
    )
  ),
  check (
    (status = 'active' and archived_at is null)
    or (status = 'archived' and archived_at is not null)
  ),
  unique (owner_internal_user_id, started_by_client_request_id)
);

create table platform_control.conversation_messages (
  message_id uuid primary key,
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  seq integer not null check (seq > 0),
  role text not null check (role in ('user', 'assistant', 'system')),
  content_ciphertext bytea not null
    check (octet_length(content_ciphertext) between 29 and 1048576),
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  turn_id uuid,
  mission_id uuid
    references platform_control.missions(mission_id)
    deferrable initially deferred,
  delivery_status text not null
    check (delivery_status in ('accepted', 'streaming', 'completed', 'failed')),
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (conversation_id, seq),
  unique (conversation_id, message_id),
  check (
    (delivery_status in ('completed', 'failed')) = (completed_at is not null)
  )
);

create table platform_control.conversation_turns (
  turn_id uuid primary key,
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  user_message_id uuid not null,
  assistant_message_id uuid,
  client_request_id uuid not null,
  mission_id uuid
    references platform_control.missions(mission_id)
    deferrable initially deferred,
  status text not null check (status in (
    'accepted', 'running', 'completed', 'failed', 'cancelled', 'interrupted'
  )),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (conversation_id, client_request_id),
  unique (conversation_id, turn_id),
  foreign key (conversation_id, user_message_id)
    references platform_control.conversation_messages(conversation_id, message_id)
    deferrable initially deferred,
  foreign key (conversation_id, assistant_message_id)
    references platform_control.conversation_messages(conversation_id, message_id)
    deferrable initially deferred
);

alter table platform_control.conversation_messages
  add constraint conversation_message_turn_v36
  foreign key (conversation_id, turn_id)
  references platform_control.conversation_turns(conversation_id, turn_id)
  deferrable initially deferred;

create unique index one_active_conversation_turn
  on platform_control.conversation_turns(conversation_id)
  where status in ('accepted', 'running');

create table platform_control.conversation_events (
  event_id uuid primary key,
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  seq integer not null check (seq > 0),
  turn_id uuid,
  mission_id uuid
    references platform_control.missions(mission_id)
    deferrable initially deferred,
  event_type text not null check (event_type in (
    'conversation.started', 'conversation.archived',
    'message.accepted', 'message.completed', 'message.failed',
    'turn.accepted', 'turn.running', 'turn.completed', 'turn.failed',
    'turn.cancelled', 'turn.interrupted',
    'brain.responding', 'plan.created', 'task.dispatched',
    'agent.accepted', 'agent.progress', 'agent.result',
    'task.reviewed', 'synthesis.started'
  )),
  payload_ciphertext bytea not null
    check (octet_length(payload_ciphertext) between 29 and 1048576),
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  created_at timestamptz not null default now(),
  unique (conversation_id, seq),
  foreign key (conversation_id, turn_id)
    references platform_control.conversation_turns(conversation_id, turn_id)
    deferrable initially deferred
);

alter table platform_control.missions
  add column conversation_id uuid,
  add column turn_id uuid,
  add column triggering_message_id uuid,
  add constraint mission_conversation_v36
    foreign key (conversation_id)
    references platform_control.conversations(conversation_id)
    deferrable initially deferred,
  add constraint mission_turn_v36
    foreign key (conversation_id, turn_id)
    references platform_control.conversation_turns(conversation_id, turn_id)
    deferrable initially deferred,
  add constraint mission_triggering_message_v36
    foreign key (conversation_id, triggering_message_id)
    references platform_control.conversation_messages(conversation_id, message_id)
    deferrable initially deferred,
  add constraint mission_conversation_links_v36 check (
    (conversation_id is null and turn_id is null and triggering_message_id is null)
    or (
      conversation_id is not null
      and turn_id is not null
      and triggering_message_id is not null
    )
  );

create index conversations_owner_updated_v36
  on platform_control.conversations(
    owner_internal_user_id, updated_at desc, conversation_id
  );
create index conversation_messages_after_v36
  on platform_control.conversation_messages(conversation_id, seq);
create index conversation_events_after_v36
  on platform_control.conversation_events(conversation_id, seq);
create index missions_conversation_turn_v36
  on platform_control.missions(conversation_id, turn_id)
  where conversation_id is not null;

comment on table platform_control.conversations is
  'Owner-scoped Agent Brain conversation metadata. Message and summary content '
  'remain purpose-bound ciphertext decrypted only by the trusted backend.';

revoke all on
  platform_control.conversations,
  platform_control.conversation_messages,
  platform_control.conversation_turns,
  platform_control.conversation_events
from public;

do $migration$
declare
  selected_app text;
  role_name text;
begin
  case current_user
    when 'platform_control_owner' then selected_app := 'platform_control_app';
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
      'revoke all on platform_control.conversations, '
      'platform_control.conversation_messages, '
      'platform_control.conversation_turns, '
      'platform_control.conversation_events from %I',
      role_name
    );
  end loop;

  execute format(
    'grant select,insert on platform_control.conversations, '
    'platform_control.conversation_messages, '
    'platform_control.conversation_turns, '
    'platform_control.conversation_events to %I',
    selected_app
  );
  execute format(
    'grant update (title,status,summary_ciphertext,summary_key_version,'
    'summary_through_seq,updated_at,archived_at) '
    'on platform_control.conversations to %I',
    selected_app
  );
  execute format(
    'grant update (delivery_status,completed_at) '
    'on platform_control.conversation_messages to %I',
    selected_app
  );
  execute format(
    'grant update (assistant_message_id,mission_id,status,updated_at) '
    'on platform_control.conversation_turns to %I',
    selected_app
  );
end
$migration$;
