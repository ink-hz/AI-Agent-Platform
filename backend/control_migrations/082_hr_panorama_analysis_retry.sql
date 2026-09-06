create function platform_hr.retry_panorama_analysis_v82(
  selected_owner_internal_user_id uuid,
  selected_batch_id uuid,
  selected_expected_row_version bigint
) returns platform_hr.panorama_production_batches
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare current_batch platform_hr.panorama_production_batches%rowtype;
declare selected platform_hr.panorama_production_batches%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  select * into current_batch
  from platform_hr.panorama_production_batches batch
  where batch.batch_id=selected_batch_id
    and batch.producer_owner_internal_user_id=selected_owner_internal_user_id
  for update;
  if not found then raise no_data_found; end if;
  if current_batch.row_version<>selected_expected_row_version then
    raise serialization_failure using message=format(
      'panorama batch version conflict: current=%s expected=%s',
      current_batch.row_version,selected_expected_row_version
    );
  end if;
  if current_batch.state<>'failed'
     or current_batch.error_code<>'analysis_failed' then
    raise check_violation using message='panorama analysis retry invalid';
  end if;
  if not exists (
      select 1 from platform_hr.public_job_snapshots snapshot
      where snapshot.production_batch_id=selected_batch_id
        and snapshot.owner_internal_user_id=selected_owner_internal_user_id
    ) or exists (
      select 1 from platform_hr.talent_insight_versions insight
      where insight.production_batch_id=selected_batch_id
        and insight.owner_internal_user_id=selected_owner_internal_user_id
    ) or exists (
      select 1 from platform_hr.panorama_publications publication
      where publication.batch_id=selected_batch_id
        and publication.producer_owner_internal_user_id=
          selected_owner_internal_user_id
    ) then
    raise check_violation using message='panorama analysis retry unsafe';
  end if;
  update platform_hr.panorama_production_batches batch set
    state='analyzing',
    error_code=null,
    row_version=batch.row_version+1,
    finished_at=null,
    updated_at=now()
  where batch.batch_id=selected_batch_id
    and batch.producer_owner_internal_user_id=selected_owner_internal_user_id
  returning * into selected;
  return selected;
end
$function$;

revoke all on function platform_hr.retry_panorama_analysis_v82(
  uuid,uuid,bigint
) from public;
grant execute on function platform_hr.retry_panorama_analysis_v82(
  uuid,uuid,bigint
) to platform_control_app,platform_control_app_preview;
