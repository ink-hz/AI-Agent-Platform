\set ON_ERROR_STOP on

begin;

create view platform_read.attachments
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
from flywheel_core.attachments
where turn_id is not null;

alter view platform_read.attachments owner to flywheel_owner;
revoke all on platform_read.attachments from public, flywheel_ingest;
grant select on platform_read.attachments to flywheel_analyst;

comment on view platform_read.attachments is
  'Safe attachment metadata for Platform session detail responses.';

commit;
