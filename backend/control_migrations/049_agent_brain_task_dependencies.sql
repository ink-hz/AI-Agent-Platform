alter table platform_brain.agent_tasks
  add column depends_on_task_ids uuid[] not null default '{}';

alter table platform_brain.agent_tasks
  add constraint agent_tasks_depends_on_bounded
    check (cardinality(depends_on_task_ids) <= 4),
  add constraint agent_tasks_depends_on_excludes_self
    check (not (task_id = any (depends_on_task_ids)));

create index agent_tasks_blocked_idx
  on platform_brain.agent_tasks (loop_id)
  where cardinality(depends_on_task_ids) > 0 and terminal_at is null;

comment on column platform_brain.agent_tasks.depends_on_task_ids is
  'Tasks of the same Brain Step whose results this task consumes. Plaintext because delivery leasing must gate on it in SQL; the field mapping stays inside the encrypted task context. Resolved from delegate_task.depends_on positions at commit time, so it can only reference an earlier call in the same batch and cannot form a cycle.';
