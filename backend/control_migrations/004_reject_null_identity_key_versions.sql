lock table platform_control.provider_identity_key_policies
in share row exclusive mode;

do $migration$
begin
  if exists (
    select 1
    from platform_control.provider_identity_key_policies policy
    cross join lateral unnest(
      policy.lookup_transition_versions
    ) as selected_version
    where selected_version is null or selected_version <= 0
  ) then
    raise check_violation using
      message = 'provider identity key policy data invalid';
  end if;
end
$migration$;

alter table platform_control.provider_identity_key_policies
  add constraint provider_identity_key_policies_versions_nonnull_positive
  check (
    array_position(lookup_transition_versions, null) is null
    and coalesce(0 < all (lookup_transition_versions), false)
  ) not valid;

alter table platform_control.provider_identity_key_policies
  validate constraint
  provider_identity_key_policies_versions_nonnull_positive;
