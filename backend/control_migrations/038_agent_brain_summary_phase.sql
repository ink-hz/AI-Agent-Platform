alter table platform_control.mission_runs
  drop constraint mission_runs_phase_check,
  drop constraint mission_runs_check;

alter table platform_control.mission_runs
  add constraint mission_runs_phase_check check (
    phase in ('summary', 'planning', 'professional', 'synthesis', 'direct')
  ),
  add constraint mission_runs_check check (
    (phase in ('professional', 'direct') and task_id is not null)
    or (
      phase in ('summary', 'planning', 'synthesis')
      and task_id is null
      and agent_id = 'agent-brain-bot'
    )
  );

comment on constraint mission_runs_phase_check
  on platform_control.mission_runs is
  'Summary is an internal Agent Brain phase and never creates a professional task.';
