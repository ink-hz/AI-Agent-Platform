# 平台左侧导航与 Agent 命名发布

2026-09-20，https://agent.orbbec.com.cn/ 已发布。顶部保留品牌与账号，左侧可收起导航，首页主体仍为彩色全景。业务入口使用 AI FAE Agent、AI HR Agent、AI 行政 Agent、AI VOC Agent；其余入口按真实用途命名。

## 版本

- 应用提交：`5e9420d9c43a7e5f92dfc00ea50ac2af03d670e5`，先合并 master 并推送 origin/master，再从该准确提交 git archive 构建。
- 上一应用：`42f6fb6e0f5fa448de2f999fa991bde237ba6a57`。
- 镜像：`sha256:40ae1193b54370f44db5b5bd8cb489117775579ba2cbcb12947d32fff5ea6f78`。
- 容器：`4085e4a42372495969c7c93df5670dda980b8ebc480161749087d5a79f70365a`。
- 源树：`50b6b95a88c598ada2d8b36842743548ff60d5bc`。
- 发布归档 SHA-256：`466fd6fbac10fcb97f4d14a78024c691e8e69d0f2262b87760a3266097fc6bc6`。
- Manifest SHA-256：`125c535222124c60f89aae04a6bcc46e6c3f3b5fd535e2327a408b598de7c8fb`。
- 本发布记录属于后续文档提交，不等于正在运行的应用版本。

## 验证

- 相关前端 37 文件 / 364 项通过；只读导航修正后外壳 5 文件 / 51 项通过；主线合并后侧栏与全景离开保护 14 项通过。范围有重叠，不相加。
- npm build 通过，保留原有 chunk 体积提示。未运行无关全量套件。
- 发布事务 13 项通过；代码与事务均完成独立静态审查。
- 镜像内隔离验证通过：中文 SVG/PNG、四层布局、临时 SQLite 草稿/发布/恢复。
- 发布后容器 healthy、restarts=0；线上 JS/CSS 与容器文件 SHA-256 一致，包含侧栏、业务 Agent 名称和角色颜色，旧功能按钮墙不存在。
- 九个受保护全景接口匿名访问 401、private/no-store；钉钉登录 start 200，未执行 OAuth callback 或冒用账号。
- 24 个其他容器、独立应用 systemd 服务、nginx 配置及进程不变；`/office/`、`/hr/`、`/voc/`、`/fae/` 的状态、响应摘要、跳转位置不变。
- 全景数据库挂载、路径、运行用户写权限保留；未修改已保存布局或迁移业务数据库。
- 未触发回滚，发布锁已释放。

## 维护

准确后续维护输入位于服务器私有目录：
`/opt/orbbec-agent-platform/private/panorama-928ad799fd8f420cb20881bc3979cada/future-maintenance.json`。
沿用原 16 份有序 Compose 输入，追加本次镜像与 release 覆盖，共 17 份。以后维护必须沿用此准确链和摘要校验；不得从泛用 deploy 脚本重建。

全景持久化继续使用 `/data/orbbec-agent-platform/panorama` → `/data/agent-platform/panorama`，环境变量 `PLATFORM_PANORAMA_STATE_PATH=/data/agent-platform/panorama/panorama.sqlite3`。

用户视觉、投屏、窄屏及真实管理员业务操作验收尚未完成；不把组件验证、静态资产核验或健康检查描述为这类验收。设计仍维护在 Orbbec-AI-Engineering/docs/design/2026-09-20-ai-engineering-homepage-final-design.md。
