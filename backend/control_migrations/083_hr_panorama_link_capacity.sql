alter table platform_hr.talent_insight_snapshots
  drop constraint talent_insight_snapshots_snapshot_ordinal_check;

alter table platform_hr.talent_insight_snapshots
  add constraint talent_insight_snapshots_snapshot_ordinal_check
  check (snapshot_ordinal between 1 and 10000);
