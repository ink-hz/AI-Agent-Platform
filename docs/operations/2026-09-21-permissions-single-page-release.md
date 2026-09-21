# 账号与权限单页发布

2026-09-21 已上线。同一页面内通过按钮切换平台管理员、FAE、VOC、观察者及合作方分区，不刷新文档、不修改 URL/history。各分区首次访问才挂载，切换保留输入、busy 与幂等状态；账号或角色变化清除已访问分区。旧链接仍定位初始分区。去掉重复的平台管理员标题；添加管理员按钮仅在管理员分区显示。

- 应用：`1764b60ab5b96d54d31c958e7bcd24e59ef6ba02`；前一应用：`c9e337e955059d24469a309282c50913e4998c62`。
- 镜像：`sha256:21ac69f8f5f30a43e84dc6226be4f0b2a8386ef60ae71d38e3434db4fe8a44ed`；API 容器：`c5dafa780e85713c4f505f69174197f32bd6744dfb45d491cbd3f0de265d2afd`。
- 源码树：`b3d138332ef958f08575d52d037c4eb8f4bb8a82`；归档 SHA-256：`d17f0864ca1547b97b71ae1d243e03fb0ebcea8fcda64f7b8f11acbb8baf8f11`；清单 SHA-256：`c520ede8095b61e21fc6a2c07d78e1b7ae3d1b2583dd67a93d5520546eb46352`。
- 后续维护：`/opt/orbbec-agent-platform/private/panorama-4fdf742e6c214b19aee3e8e1a96815d8/future-maintenance.json`；沿用 25 份 Compose 输入，仅追加 API 镜像及版本覆盖，共 26 份。

验证：144 项相关组件/路由测试通过，0 失败；涵盖地址与 history 不变、标题节点保留、首次加载、输入保留、进行中授权只发送一次、降权/换账号清理、旧链接授权边界。生产构建通过。独立代码审查无阻断项。发布事务回归 15 项通过。

线上 26 项检查通过：静态资源与镜像哈希一致、分区按钮和面板标识存在、原全局同步横条不存在；权限页和管理查询的匿名访问保持 401/no-store，保护接口缓存边界正常。管理员投影与候选搜索只读仓库探查通过，未输出人员信息。容器 healthy、重启 0、锁已释放。24 个其他容器、Nginx、systemd 及挂载不变。

未执行数据库迁移、实际账号授权或完整管理员登录后的浏览器操作；页面视觉交互由用户验收。生产资源检查不等同于真实浏览器点击验收。发布证据目录：`/opt/orbbec-agent-platform/private/panorama-4fdf742e6c214b19aee3e8e1a96815d8`；本地回执：`/tmp/permissions-single-page-release`。

本记录提交不改变上述应用版本。
