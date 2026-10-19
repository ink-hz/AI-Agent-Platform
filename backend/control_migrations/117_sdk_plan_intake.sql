-- Immutable SDK plan source rows and atomic linkage to existing write intents.
create table sdk.plan_imports (
  id bigint generated always as identity primary key,
  uploaded_by uuid not null references platform_control.internal_users(internal_user_id),
  filename text not null check (length(filename) between 1 and 255),
  sha256 char(64) not null unique check (sha256 ~ '^[0-9a-f]{64}$'),
  row_count integer not null check (row_count between 1 and 1000),
  task_count integer not null check (task_count between 1 and row_count),
  module_count integer not null check (module_count between 1 and row_count),
  created_at timestamptz not null default clock_timestamp()
);

create table sdk.plan_rows (
  id bigint generated always as identity primary key,
  import_id bigint not null references sdk.plan_imports(id),
  record_number integer not null check (record_number >= 2),
  physical_line integer not null check (physical_line >= 2),
  kind text not null check (kind in ('task','heading')),
  module text not null check (length(module) >= 1),
  cells jsonb not null check (jsonb_typeof(cells) = 'array' and jsonb_array_length(cells) = 10),
  unique (import_id, record_number)
);
create index sdk_plan_rows_import_position on sdk.plan_rows(import_id, record_number);

alter table sdk.write_intents add column plan_row_id bigint references sdk.plan_rows(id);
alter table sdk.write_intents add constraint sdk_plan_row_requirement_only
  check (plan_row_id is null or kind = 'create_requirement');
create unique index sdk_write_intents_one_per_plan_row on sdk.write_intents(plan_row_id)
  where plan_row_id is not null and state <> 'rejected';
create index sdk_write_intents_plan_history on sdk.write_intents(plan_row_id, updated_at desc)
  where plan_row_id is not null;

do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK plan migration owner/environment mismatch';
  end if;
  execute format('grant select, insert on sdk.plan_imports, sdk.plan_rows to %I', selected_app);
  execute format('grant usage, select on all sequences in schema sdk to %I', selected_app);
end
$migration$;
