create function platform_hr.reconcile_talent_source_v84(
  selected_source_id uuid,
  selected_owner_internal_user_id uuid,
  selected_company_key text,
  selected_canonical_name text,
  selected_aliases jsonb,
  selected_approved_public_urls jsonb,
  selected_active boolean
) returns platform_hr.talent_sources
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.talent_sources%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_source_id is null
     or selected_owner_internal_user_id is null
     or selected_company_key is null
     or selected_canonical_name is null
     or selected_aliases is null
     or selected_approved_public_urls is null
     or selected_active is null then
    raise check_violation using message='talent source reconciliation invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':talent-source:' ||
    btrim(selected_company_key),0
  ));
  select * into selected
  from platform_hr.talent_sources source
  where source.owner_internal_user_id=selected_owner_internal_user_id
    and source.company_key=btrim(selected_company_key)
  for update;
  if not found then raise no_data_found; end if;
  if selected.source_id<>selected_source_id then
    raise check_violation using message='talent source identity mismatch';
  end if;
  update platform_hr.talent_sources source set
    canonical_name=btrim(selected_canonical_name),
    aliases=selected_aliases,
    approved_public_urls=selected_approved_public_urls,
    active=selected_active,
    updated_at=now()
  where source.owner_internal_user_id=selected_owner_internal_user_id
    and source.company_key=btrim(selected_company_key)
  returning * into selected;
  return selected;
end
$function$;

revoke all on function platform_hr.reconcile_talent_source_v84(
  uuid,uuid,text,text,jsonb,jsonb,boolean
) from public;
grant execute on function platform_hr.reconcile_talent_source_v84(
  uuid,uuid,text,text,jsonb,jsonb,boolean
) to platform_control_app,platform_control_app_preview;
