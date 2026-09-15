# HR 同机独立服务生产切换（2026-09-15）

## 发布结果

HR 已从平台内进程切换为同机独立 API、Worker 和前端，Nginx 直接分流正式 HR 路径。共享数据库、角色、密文、附件对象、平台会话和 lane 协议保持原样。

| 项目 | 准确生产身份 |
| --- | --- |
| HR 源码 | `f99707bea834ed00e64f9c8b468c2ed879e7493f` |
| 平台源码 | `041495e04b35749272ecbcec53ab27a490023f16` |
| 平台 API 镜像 | `sha256:f8dca12079413baf4525fc2435b8db5e942a96688b1649d30d5b0f0708c1a38d` |
| 平台 API 容器 | `0aff6a5d5cccc1f419db8907150a5f01da6851a3515be88b0a920a86f291032b`，healthy、Docker `RestartCount=0` |
| HR 进程 | `ai-hr-agent`、`ai-hr-worker` 均 enabled/active、`NRestarts=0` |
| 切换时点 | `2026-09-15T04:10:42.194835Z`，phase=`cloud` |
| 公共迁移 108 | SHA-256 `89f448a20b7958357459af6d1fc6e6e519815229df98a063d599482fd5483832` |

公共迁移 108 只替换既有 `create_rate_limited_web_login_attempt_v2` 的 return-path 校验，允许当前 HR production/preview 基路径上的 `position/work` UUID 查询。原限流、身份、`SECURITY DEFINER` 与固定 `search_path` 保持；旧实验 HR 108 角色/所有权迁移从未恢复。应用回执为 `completed`、`cleanup_verified=true`，结束时 owner membership 和 migrator session 均为 0。

## 切换与验证

切换时 work queued/running、material parse queued/processing 与活租约均为 0，candidate states 为 null。旧 Docker HR Worker 已停止并设为 `restart=no`；独立 Worker ready 后接管，HR new admission 为 true，未来平台 Compose 将旧 Worker 固定 `scale=0`。业务数据、密文、附件对象和数据库角色没有迁移或重写。

Nginx 已将 `/hr/`、`/hr/assets/`、`/api/hr/` 指向 `127.0.0.1:8012`，登录、账号和共享附件接口继续由平台提供。最终外网验证包括：

- `/hr/`、`/hr/positions`、`/hr/panorama` 与岗位 context 深链返回 200，独立 index SHA-256 为 `621bb9d931ff492f40472a0ff866422bba40984f92bab602fcff40b108a81685`；入口 JS SHA-256 为 `636682bfa1375e546b5a8b951dbd0f7457d30342b16cbffbd82ea07c9d65ddd8`。
- 缺失 HR asset 返回 404，匿名 `/api/hr/positions` 返回 401，未知 HR API 返回 404。
- `/hr/positions`、仅 `work` UUID、`position` 与 `work` UUID 的三条登录 start 均返回 200，授权主机仍为 `login.dingtalk.com`。
- 平台 `/login` 返回 200，index SHA-256 为 `e86b2c42bc02cd792afedcee50b34093ab8400af0e8fdf640404fa6aa4a9f567`；平台 API healthy，HR readiness 另行核实为 ready、new admission enabled。
- 最终 24 个 peer 身份检查通过，action/deploy 锁与 failclosed 标记均释放。切换期间 FAE 的独立维护更换过容器，故本记录只声明最终检查结果。

没有执行真实模型、真实候选人、真实 S3、用户扫码/OAuth callback 或受认证浏览器页面验收。用户负责页面验收；上述 API、HTTP 和进程证据不构成专业质量或真实个人材料处理验收。

## 后续维护与版本边界

后续维护唯一入口为：

`/opt/orbbec-agent-platform/private/hr-same-host-platform-final-2fcebd1606892d01c359d48f126db9bc/execution/future-maintenance.json`

该文件列出 11 个有序 Compose 配置文件（包含 base）、准确平台镜像和旧 HR Worker `scale=0`。不得只用基础 Compose。旧 Nginx、旧 Worker 和原镜像材料仍保留供受控回退；回退不回滚数据或重加密。

平台后续 helper 提交 `a0f383663c4a5a9ad5002645451354f9ca29e53a` 补齐 root supervisor 对 HR 96/97/98/99/101 账本的校验。它和本发布记录均未重建运行镜像；生产运行身份仍是上表中的 `041495e0` 与 `f8dca120…`。

详细阶段证据与本地/生产验收边界见[同机迁移记录](../reviews/2026-09-15-hr-same-host-migration.md)。
