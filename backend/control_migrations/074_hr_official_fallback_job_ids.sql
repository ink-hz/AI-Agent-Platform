alter table platform_hr.positions
  drop constraint positions_official_job_id_check;

alter table platform_hr.positions
  add constraint positions_official_job_id_check
  check (
    official_job_id is null
    or official_job_id ~ '^(J[0-9]{4,12}|JOBAD:[0-9]{1,20})$'
  ) not valid;

alter table platform_hr.positions
  validate constraint positions_official_job_id_check;

alter table platform_hr.official_position_versions
  drop constraint official_position_versions_official_job_id_check;

alter table platform_hr.official_position_versions
  add constraint official_position_versions_official_job_id_check
  check (official_job_id ~ '^(J[0-9]{4,12}|JOBAD:[0-9]{1,20})$') not valid;

alter table platform_hr.official_position_versions
  validate constraint official_position_versions_official_job_id_check;
