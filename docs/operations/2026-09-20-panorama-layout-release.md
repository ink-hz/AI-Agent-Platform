# 四层可编辑全景首页生产发布

2026-09-20｜用户指令“上线我看看”｜应用提交 `03074620241588021f358d307af576e1bdd48158`

入口：https://agent.orbbec.com.cn/

首页采用产业位置、产品与技术、Marketing ↔ Technology、支撑体系四层。默认图移除经营数字，点击节点查看关联与功能；支持修改布局、保存共享草稿、发布和恢复上一版。主站管理员权限及独立子路径沿用原规则。

## 发布身份与维护

- 镜像：`sha256:b3ae2f5d75aa808be94391c0a1da62b9deb123ef322137f6cd1332bff2090430`。
- API 容器：`f63b6fd09640a9c96e3dac7398987b4b06e4e59d9dcb2ceca2cb55ebcaeddf14`。
- 上一发布：`39f5a039ba5447151d56ea3bdfaaf81b8fc3ffc4`，上一镜像：`sha256:74cb361439f305f21215631f8e547c261d575dc962af53e167f1023174466136`。
- 仅重建 platform-api，保留十三份原 Compose 输入，追加一份镜像/版本/全景持久化覆盖；不执行既有业务数据库迁移。
- 全景 SQLite 专用挂载：`/data/orbbec-agent-platform/panorama` → `/data/agent-platform/panorama`，宿主 uid/gid 10001、0700。回退保留布局数据。
- 后续维护权威输入：`/opt/orbbec-agent-platform/private/panorama-7293b11afffe48d8a60e37f91b5ad746/future-maintenance.json`，共十四份 Compose；不要直接使用通用 deploy.sh 重建全平台。
- 源树 `a2dc3174567ec632f25ccf65ad8c9a39b74bebca`；archive SHA-256 `a828ea37308f203cff5f02c51799062e604d0909dfbacb8695293b40603ddff3`；manifest SHA-256 `cd7cd1fd7ab3ef549050d28fcf4ad4b6c312b7e61d00ee0533f7893086259bf5`。

## 实际验证

- 后端相关回归 720 项，前端全景/页面/宿主/导航/云端模式回归 68 项，构建通过。
- 发布事务 15 项测试通过，涵盖失败回退、输入变更拒绝、权限与缓存边界、挂载检查。服务器 Compose 将显式 false 规范化为空 bind 对象，预检查曾因此停止；仅接受这两种等价表示，true 仍拒绝。修正后复审通过，停止期间未切换 API。
- 镜像内无网络验证四层 JSON、SVG、默认 1920×1080 PNG、中文字体，以及临时 SQLite 重开后的保存/发布/恢复。
- 应用独立审查发现的保存期间新输入丢失、共享草稿核对不一致、详情文案解析契约不一致已修复并复审。
- 当前 API healthy、重启计数 0，current 指向本次应用提交；发布锁释放，未触发回退。
- 24 个其他容器、独立应用 systemd 与 nginx 状态及配置摘要保持一致。/office/、/hr/、/voc/、/fae/ 响应状态/摘要/跳转与发布前一致。
- 九个受保护全景接口匿名均 401 且 private/no-store。在线 JS/CSS 摘要与运行容器一致，公开 JS 不含检查的受保护事实标记。
- 钉钉登录发起返回 200；未执行 OAuth 回调、借用管理员会话或更改角色。
- 专用状态目录已验证运行 uid 可写；生产业务内容没有通过测试账号写入。

## 验收边界

浏览器、手机、投屏布局和真实管理员编辑使用由用户验收。上述自动化、隔离镜像及生产接口检查不等于完整业务验收。导出为发布版概要；密集分组在导出中折叠，不作为全部节点明细清单。

私有发布回执：`/opt/orbbec-agent-platform/private/panorama-7293b11afffe48d8a60e37f91b5ad746/`，含构建、基线、事务、上线检查、审查及后续维护输入。后续文档提交不是新的应用发布。
