\set ON_ERROR_STOP on

begin;

create schema if not exists platform_fae_reports authorization flywheel_owner;

create table if not exists platform_fae_reports.reports (
  report_pk uuid primary key default gen_random_uuid(),
  report_id text not null check (report_id ~ '^fae-(weekly|topic)-[a-z0-9][a-z0-9-]{2,63}$'),
  report_version integer not null check (report_version > 0),
  report_type text not null check (report_type in ('weekly', 'topic')),
  status text not null check (status in ('ready', 'failed')),
  title text not null check (nullif(btrim(title), '') is not null),
  period_start timestamptz not null,
  period_end timestamptz not null,
  data_cutoff_at timestamptz not null,
  generated_at timestamptz not null,
  imported_at timestamptz not null default now(),
  imported_by text not null check (nullif(btrim(imported_by), '') is not null),
  analysis_version text not null check (nullif(btrim(analysis_version), '') is not null),
  source_snapshot_at timestamptz not null,
  payload_digest char(64) not null check (payload_digest ~ '^[0-9a-f]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  unique(report_id, report_version),
  check (period_start < period_end),
  check (period_end <= data_cutoff_at and data_cutoff_at <= generated_at)
);

create table if not exists platform_fae_reports.report_evidence (
  report_pk uuid not null references platform_fae_reports.reports(report_pk) on delete restrict,
  finding_id text not null check (nullif(btrim(finding_id), '') is not null),
  evidence_ordinal integer not null check (evidence_ordinal >= 0),
  evidence_kind text not null check (evidence_kind in ('session', 'turn', 'feedback', 'issue')),
  canonical_key text not null check (nullif(btrim(canonical_key), '') is not null),
  label text not null check (nullif(btrim(label), '') is not null),
  import_availability text not null check (import_availability in ('available', 'unavailable')),
  import_unavailable_reason text,
  primary key(report_pk, finding_id, evidence_ordinal)
);

create table if not exists platform_fae_reports.finding_issue_links (
  link_id uuid primary key default gen_random_uuid(),
  report_pk uuid not null references platform_fae_reports.reports(report_pk) on delete restrict,
  finding_id text not null check (nullif(btrim(finding_id), '') is not null),
  issue_id uuid not null references platform_review.feedback_issues(id) on delete restrict,
  linked_at timestamptz not null default now(),
  linked_by text not null check (nullif(btrim(linked_by), '') is not null),
  unlinked_at timestamptz,
  unlinked_by text
);

create unique index if not exists fae_report_active_finding_issue_uq
  on platform_fae_reports.finding_issue_links(report_pk, finding_id, issue_id)
  where unlinked_at is null;

create table if not exists platform_fae_reports.report_audit_events (
  event_id uuid primary key default gen_random_uuid(),
  report_pk uuid not null references platform_fae_reports.reports(report_pk) on delete restrict,
  event_type text not null check (nullif(btrim(event_type), '') is not null),
  actor text not null check (nullif(btrim(actor), '') is not null),
  occurred_at timestamptz not null default now(),
  details jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object')
);

create or replace function platform_fae_reports.prevent_immutable_report_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'FAE report versions and imported evidence are immutable';
end
$$;

drop trigger if exists fae_reports_immutable on platform_fae_reports.reports;
create trigger fae_reports_immutable before update or delete
  on platform_fae_reports.reports for each row execute function
  platform_fae_reports.prevent_immutable_report_mutation();

drop trigger if exists fae_report_evidence_immutable on platform_fae_reports.report_evidence;
create trigger fae_report_evidence_immutable before update or delete
  on platform_fae_reports.report_evidence for each row execute function
  platform_fae_reports.prevent_immutable_report_mutation();

drop trigger if exists fae_report_audit_append_only on platform_fae_reports.report_audit_events;
create trigger fae_report_audit_append_only before update or delete
  on platform_fae_reports.report_audit_events for each row execute function
  platform_fae_reports.prevent_immutable_report_mutation();

alter table platform_fae_reports.reports owner to flywheel_owner;
alter table platform_fae_reports.report_evidence owner to flywheel_owner;
alter table platform_fae_reports.finding_issue_links owner to flywheel_owner;
alter table platform_fae_reports.report_audit_events owner to flywheel_owner;
alter function platform_fae_reports.prevent_immutable_report_mutation() owner to flywheel_owner;

revoke all on schema platform_fae_reports from public;
grant usage on schema platform_fae_reports to platform_review_writer, flywheel_analyst;
revoke all on all tables in schema platform_fae_reports from public;
grant select, insert on platform_fae_reports.reports,
  platform_fae_reports.report_evidence,
  platform_fae_reports.report_audit_events to platform_review_writer;
grant select, insert, update on platform_fae_reports.finding_issue_links
  to platform_review_writer;
revoke update, delete on platform_fae_reports.reports,
  platform_fae_reports.report_evidence,
  platform_fae_reports.report_audit_events from platform_review_writer;
revoke delete on platform_fae_reports.finding_issue_links from platform_review_writer;
grant select on all tables in schema platform_fae_reports to flywheel_analyst;

commit;
