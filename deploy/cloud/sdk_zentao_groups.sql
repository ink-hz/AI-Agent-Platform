-- Run only against the backed-up SDK ZenTao 22.6 database. The application
-- uses the resulting IDs through PLATFORM_SDK_PROVISION_GROUP_ID and a
-- dedicated sdk-provision service account. No existing group is altered.
insert into zt_group (name, role, `desc`, developer, vision)
select 'SDK 账号开通服务', 'others', '仅供平台私网自动开通与撤权', 0, 'rnd'
where not exists (select 1 from zt_group where name='SDK 账号开通服务');

insert into zt_group (name, role, `desc`, developer, vision)
select 'SDK 工作组', 'others', '需求、任务、测试与发布', 1, 'rnd'
where not exists (select 1 from zt_group where name='SDK 工作组');

set @sdk_provision_group := (select id from zt_group where name='SDK 账号开通服务' limit 1);
set @sdk_member_group := (select id from zt_group where name='SDK 工作组' limit 1);

insert ignore into zt_grouppriv (`group`, module, method) values
  (@sdk_provision_group, 'user', 'create'),
  (@sdk_provision_group, 'user', 'edit'),
  (@sdk_provision_group, 'user', 'profile'),
  (@sdk_provision_group, 'group', 'browse'),
  (@sdk_provision_group, 'product', 'view'),
  (@sdk_provision_group, 'product', 'edit');

-- This 56-permission subset was exercised against all four SDK destinations
-- in a private ZenTao 22.6 instance. It is substantially narrower than DEV.
insert ignore into zt_grouppriv (`group`, module, method)
select distinct @sdk_member_group, module, method
from zt_grouppriv
where `group` in (2, 3, 5)
  and (
    (module='my' and method in ('index','work','task','profile','changePassword','preference'))
    or (module='product' and method in ('index','browse','view'))
    or (module='story' and method in ('browse','create','edit','view','review','change','close','tasks','cases'))
    or (module='requirement' and method in ('create','edit','view','review'))
    or (module='task' and method in ('browse','create','edit','view','assignTo','start','finish','close','recordWorkhour'))
    or (module='qa' and method='index')
    or (module='testcase' and method in ('browse','create','edit','view','submit'))
    or (module='release' and method in ('browse','view','create','edit','publish'))
    or (module='execution' and method in ('browse','view','task','story','testcase'))
    or (module='project' and method in ('browse','view'))
    or (module='user' and method in ('profile','view'))
    or (module='index' and method='index')
    or (module='bug' and method in ('browse','create','edit','view'))
  );
