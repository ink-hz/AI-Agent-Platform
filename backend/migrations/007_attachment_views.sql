\set ON_ERROR_STOP on

begin;

create or replace view platform_read.attachments
with (security_barrier = true) as
select
  id as attachment_id,
  turn_id::text as turn_key,
  direction,
  case when archive_status = 'expired' then null else name end as display_name,
  mime_type,
  size_bytes,
  received_or_generated_at,
  archive_status,
  delivery_status,
  expires_at
from flywheel_core.attachments attachment
where turn_id is not null
  and not (
    attachment.ingest_key like 'legacy:%'
    and exists (
      select 1
      from flywheel_core.attachments canonical
      where canonical.turn_id = attachment.turn_id
        and canonical.direction = attachment.direction
        and canonical.source_provider is not distinct from attachment.source_provider
        and canonical.platform_message_id is not distinct from attachment.platform_message_id
        and canonical.name is not distinct from attachment.name
        and canonical.ingest_key not like 'legacy:%'
    )
  );

alter view platform_read.attachments owner to flywheel_owner;
revoke all on platform_read.attachments from public, flywheel_ingest;
grant select on platform_read.attachments to flywheel_analyst;

comment on view platform_read.attachments is
  'Safe attachment metadata for Platform session detail responses.';

commit;
