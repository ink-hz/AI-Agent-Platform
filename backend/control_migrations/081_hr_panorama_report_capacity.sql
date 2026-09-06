alter table platform_hr.talent_insight_versions
  drop constraint talent_insight_versions_snapshot_ids_check;
alter table platform_hr.talent_insight_versions
  add constraint talent_insight_versions_snapshot_ids_check check (
    cardinality(snapshot_ids) between 1 and 10000
    and platform_hr.uuid_array_is_unique_v79(snapshot_ids)
  );

create function platform_hr.create_production_insight_v81(
  selected_insight_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_production_batch_id uuid,
  selected_source_ids uuid[],
  selected_snapshot_ids uuid[],
  selected_facts jsonb,
  selected_inferences jsonb,
  selected_unknowns jsonb,
  selected_direction_clusters jsonb,
  selected_summary text,
  selected_agent_id text,
  selected_model_version text
) returns platform_hr.talent_insight_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_batch platform_hr.panorama_production_batches%rowtype;
declare selected platform_hr.talent_insight_versions%rowtype;
declare next_version bigint;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':talent-insight-request:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.talent_insight_versions insight
  where insight.owner_internal_user_id=selected_owner_internal_user_id
    and insight.client_request_id=selected_client_request_id;
  if found then
    if selected.insight_version_id is distinct from
        selected_insight_version_id
      or selected.production_batch_id is distinct from
        selected_production_batch_id
      or selected.selected_source_ids is distinct from selected_source_ids
      or selected.snapshot_ids is distinct from selected_snapshot_ids
      or selected.facts is distinct from selected_facts
      or selected.inferences is distinct from selected_inferences
      or selected.unknowns is distinct from selected_unknowns
      or selected.direction_clusters is distinct from
        selected_direction_clusters
      or selected.summary is distinct from btrim(selected_summary)
      or selected.agent_id is distinct from btrim(selected_agent_id)
      or selected.model_version is distinct from
        btrim(selected_model_version) then
      raise unique_violation using
        message='production insight idempotency mismatch';
    end if;
    return selected;
  end if;
  select * into selected_batch
  from platform_hr.panorama_production_batches batch
  where batch.batch_id=selected_production_batch_id
    and batch.producer_owner_internal_user_id=
      selected_owner_internal_user_id
    and batch.state='analyzing'
  for update;
  if not found then raise no_data_found; end if;
  if cardinality(selected_source_ids) not between 1 and 100
    or not platform_hr.uuid_array_is_unique_v79(selected_source_ids)
    or not selected_source_ids<@selected_batch.selected_source_ids
    or exists (
      select 1 from unnest(selected_source_ids) requested(source_id)
      where not exists (
        select 1 from platform_hr.panorama_source_attempts attempt
        where attempt.batch_id=selected_production_batch_id
          and attempt.source_id=requested.source_id
          and attempt.state='succeeded'
      )
    ) then
    raise check_violation using
      message='production insight source selection invalid';
  end if;
  if cardinality(selected_snapshot_ids) not between 1 and 10000
    or not platform_hr.uuid_array_is_unique_v79(selected_snapshot_ids)
    or (
      select count(distinct observation.result_snapshot_id)
      from platform_hr.public_job_snapshot_requests observation
      join platform_hr.public_job_snapshots snapshot
        on snapshot.snapshot_id=observation.result_snapshot_id
        and snapshot.owner_internal_user_id=observation.owner_internal_user_id
      where observation.owner_internal_user_id=
          selected_owner_internal_user_id
        and observation.production_batch_id=selected_production_batch_id
        and observation.result_snapshot_id=any(selected_snapshot_ids)
        and observation.source_id=any(selected_source_ids)
    )<>cardinality(selected_snapshot_ids) then
    raise no_data_found;
  end if;
  if not platform_hr.insight_payload_is_valid_v79(
      selected_facts,selected_inferences,selected_unknowns
    ) or not platform_hr.facts_have_https_urls_v79(selected_facts) then
    raise check_violation using
      message='production insight fact source invalid';
  end if;
  if exists (
    select 1 from jsonb_array_elements(selected_facts) fact
    where not exists (
      select 1
      from platform_hr.public_job_snapshot_requests observation
      join platform_hr.public_job_snapshots snapshot
        on snapshot.owner_internal_user_id=
          observation.owner_internal_user_id
        and snapshot.snapshot_id=observation.result_snapshot_id
        and snapshot.source_id=observation.source_id
      join platform_hr.talent_sources source
        on source.owner_internal_user_id=observation.owner_internal_user_id
        and source.source_id=observation.source_id
      where observation.owner_internal_user_id=
          selected_owner_internal_user_id
        and observation.production_batch_id=selected_production_batch_id
        and observation.observation_id=(fact->>'observation_id')::uuid
        and observation.result_snapshot_id=(fact->>'snapshot_id')::uuid
        and observation.result_snapshot_id=any(selected_snapshot_ids)
        and observation.source_id=any(selected_source_ids)
        and observation.source_url=fact->>'source_url'
        and observation.observed_at=(fact->>'observed_at')::timestamptz
        and platform_hr.url_is_approved_v79(
          fact->>'source_url',source.approved_public_urls
        )
    )
  ) then
    raise check_violation using
      message='production insight fact observation binding invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':talent-insight-version',0
  ));
  select coalesce(max(insight.version_number),0)+1 into next_version
  from platform_hr.talent_insight_versions insight
  where insight.owner_internal_user_id=selected_owner_internal_user_id;
  insert into platform_hr.talent_insight_versions(
    insight_version_id,owner_internal_user_id,client_request_id,run_id,
    version_number,selected_source_ids,snapshot_ids,facts,inferences,
    unknowns,direction_clusters,summary,source_conversation_id,
    source_turn_id,agent_id,model_version,production_batch_id
  ) values (
    selected_insight_version_id,selected_owner_internal_user_id,
    selected_client_request_id,null,next_version,selected_source_ids,
    selected_snapshot_ids,selected_facts,selected_inferences,
    selected_unknowns,selected_direction_clusters,btrim(selected_summary),
    null,null,btrim(selected_agent_id),btrim(selected_model_version),
    selected_production_batch_id
  ) returning * into selected;
  return selected;
end
$function$;

revoke all on function platform_hr.create_production_insight_v81(
  uuid,uuid,uuid,uuid,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,text,text,text
) from public;
grant execute on function platform_hr.create_production_insight_v81(
  uuid,uuid,uuid,uuid,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,text,text,text
) to platform_control_app,platform_control_app_preview;
