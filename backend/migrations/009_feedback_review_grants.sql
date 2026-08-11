\set ON_ERROR_STOP on

begin;

revoke all on schema platform_source_fae from platform_review_writer;
revoke all on schema platform_source_admin from platform_review_writer;
revoke all on schema platform_sync from platform_review_writer;

revoke all on schema platform_read from platform_review_writer;
revoke all on all tables in schema platform_read from platform_review_writer;
grant usage on schema platform_read to platform_review_writer;
grant select on platform_read.feedback, platform_read.turns
  to platform_review_writer;

commit;
