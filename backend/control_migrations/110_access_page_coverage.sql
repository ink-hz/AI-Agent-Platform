-- Add fixed, user-visible page categories without changing historical events.
insert into platform_control.access_page_catalog(
  workspace_key,page_key,display_name,allows_agent_id,module_display_name
) values
  ('platform','platform.home','平台首页',false,'平台首页'),
  ('platform','platform.organization','组织空间',false,'组织空间'),
  ('platform','platform.ai_engineering','AI 工程',false,'AI 工程'),
  ('hr','hr.panorama','招聘全景',false,'招聘协作'),
  ('hr','hr.position_chat','岗位对话',false,'招聘协作'),
  ('hr','hr.position_context','岗位上下文',false,'招聘协作'),
  ('hr','hr.position_candidates','岗位候选人',false,'招聘协作'),
  ('hr','hr.position_artifacts','岗位成果',false,'招聘协作'),
  ('admin','admin.agent_designs','Agent 设计',false,'Agent 运营'),
  ('admin','admin.permissions.observers','观察者权限',false,'平台治理'),
  ('admin','admin.permissions.partners','合作方权限',false,'平台治理'),
  ('admin','admin.permissions.fae','FAE 权限',false,'平台治理'),
  ('admin','admin.permissions.voc','VOC 权限',false,'平台治理'),
  ('office','office.workspaces','奥比空间站',false,'工位管理'),
  ('office','office.workspace_admin','工位管理后台',false,'工位管理'),
  ('office','office.meetings','会务服务',false,'会务服务'),
  ('office','office.meeting_admin','会务管理',false,'会务服务'),
  ('office','office.floor_stations','楼层工位图',false,'工位管理'),
  ('office','office.invitation_admin','邀约管理',false,'行政管理'),
  ('office','office.service.workspaces','奥比空间站服务详情',false,'行政服务'),
  ('office','office.service.meetings','会务服务详情',false,'行政服务'),
  ('office','office.service.floor_stations','楼层工位服务详情',false,'行政服务')
on conflict (page_key) do nothing;
