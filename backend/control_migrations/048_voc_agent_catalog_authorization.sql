create or replace function platform_control.has_agent_use_scope_v29(
  selected_user_id uuid,
  selected_agent_id text
) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  with active_member as (
    select state.active_generation_id, member.member_key
    from platform_control.internal_users users
    join platform_control.directory_state state on state.singleton
    join platform_control.directory_generations generation
      on generation.generation_id = state.active_generation_id
     and generation.status = 'complete'
    join platform_control.directory_members member
      on member.generation_id = state.active_generation_id
     and member.internal_user_id = users.internal_user_id
     and member.status = 'active'
    where users.internal_user_id = selected_user_id
      and users.status = 'active'
      and users.locally_invalidated_at is null
      and selected_agent_id in (
        'hr-bot',
        'voc',
        'marketing-prospecting-bot',
        'marketing-inbound-bot',
        'marketing-voice-bot',
        'marketing-intelligence-bot',
        'marketing-gtm-bot',
        'ai-admin-agent',
        'ai-fae-agent'
      )
  )
  select exists (
    select 1
    from active_member member
    join platform_control.agent_use_grants grant_row
      on grant_row.agent_id = selected_agent_id
     and grant_row.revoked_at is null
    where (
      grant_row.target_kind = 'all_members'
      or (
        grant_row.target_kind = 'user'
        and grant_row.target_internal_user_id = selected_user_id
      )
      or (
        grant_row.target_kind = 'department'
        and grant_row.include_descendants
        and exists (
          select 1
          from platform_control.member_departments membership
          join platform_control.department_closure closure
            on closure.generation_id = membership.generation_id
           and closure.descendant_department_key = membership.department_key
           and closure.ancestor_department_key = grant_row.target_department_key
          where membership.generation_id = member.active_generation_id
            and membership.member_key = member.member_key
        )
      )
    )
  )
$function$;

comment on function platform_control.has_agent_use_scope_v29(uuid, text) is
  'Canonical nine-Agent authorization allowlist, revised by migration 048.';
