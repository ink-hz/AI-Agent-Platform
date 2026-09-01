\set ON_ERROR_STOP on

begin;

grant usage on schema platform_fae_reports to flywheel_analyst;
grant select on platform_fae_reports.reports,
  platform_fae_reports.report_evidence,
  platform_fae_reports.finding_issue_links to flywheel_analyst;

commit;
