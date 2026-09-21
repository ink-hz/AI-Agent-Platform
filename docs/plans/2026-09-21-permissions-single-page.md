# 账号与权限合并为单页

用户已明确要求一个页面内切换，不再刷新整页或访问新路由。沿用现有账号与权限名称及授权规则。

根因与方案：上次只统一导航外观，PermissionNavigation 仍输出普通链接，两个页面分别拥有标题与容器。只给链接加 SPA 跳转仍会更换路由和卸载表单，未满足需求。改由 IdentityManagementPage 统一拥有标题、选择状态与权限分区，导航按钮只修改本地状态。旧 PermissionAccessPage 仅作为深链接适配，不再维护另一套页面结构。

状态与边界：各分区首次选择才挂载，访问后保持挂载、非当前分区使用 hidden 隐藏，因此草稿、busy 锁和幂等恢复状态在切换时保留。账号或角色变化时统一重建页面；非 owner 不挂载 FAE/VOC/合作方。旧链接首次打开对应分区且不加载其他权限。底层写入与服务端审计不变。平台管理员标签下不再重复同名标题，添加按钮只出现在该分区。

- [x] 在 IdentityManagementPage.test.tsx 增加点击切换测试：地址与 history 不变、页面标题节点不重建、只加载首次访问分区、返回保留输入；模拟未完成 FAE 授权切换返回，busy 和请求数不变。
- [x] PermissionNavigation.tsx 改为 button + onSelect；IdentityManagementPage.tsx 集中容器，权限组件按需保留；PermissionAccessPage.tsx 适配 initialSection。CSS 改为按钮导航并保留键盘焦点样式。
- [x] 检查降权/换账号清除已访问面板，旧深链接的拒绝和单面板加载测试继续成立。
- [x] 运行相关权限组件、路由/外壳测试及构建；独立审查，归并主线。
- [x] 沿用已授权发布流程，以当前实际应用为基线仅更新 API 镜像；验证静态资源和保护路由，维护设计和发布记录。页面视觉交用户验收，不执行生产角色写入。

验证命令：`npm --prefix webui test -- --run src/pages/IdentityManagementPage.test.tsx src/pages/PermissionAccessPage.test.tsx src/pages/PartnerAccessPanel.test.tsx src/components/FaeAccessPanel.test.tsx src/components/VocAccessPanel.test.tsx src/AppShell.sidebar.test.tsx src/router.test.ts`；`npm --prefix webui run build`。

本地验证：144 项相关组件/路由测试及构建通过；独立代码审查无阻断项。发布事务脚本更新准确基线后 15 项通过。

已发布应用 `1764b60a`，见[发布记录](../operations/2026-09-21-permissions-single-page-release.md)。
