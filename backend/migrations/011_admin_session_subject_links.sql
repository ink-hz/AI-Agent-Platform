\set ON_ERROR_STOP on

begin;

create table if not exists platform_identity.session_subject_links (
  source_kind text not null check (source_kind in ('admin')),
  native_session_id text not null check (btrim(native_session_id) <> ''),
  internal_user_id uuid not null,
  verification_method text not null check (verification_method = 'platform_session'),
  verified_at timestamptz not null,
  source_synced_at timestamptz not null,
  primary key (source_kind, native_session_id)
);

alter table platform_identity.session_subject_links owner to flywheel_owner;
revoke all on platform_identity.session_subject_links from public;
grant select, insert, update, delete
  on platform_identity.session_subject_links to platform_sync_writer;

comment on table platform_identity.session_subject_links is
  'Explicit Platform-verified session ownership; never inferred from names or sender text.';

commit;
