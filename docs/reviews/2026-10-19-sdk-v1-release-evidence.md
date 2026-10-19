# SDK V1 真实场景验收与发布记录

日期：2026-10-19。状态：**本地真实禅道与平台接口链路通过；SDK 核心库构建通过；单测、真机、企业登录和生产发布未通过或未执行。发布门槛关闭。**

## 版本与入口

| 对象 | 本次核对值 |
| --- | --- |
| 平台开发基线 | `25c63551fdd8de3eb375fe141843d3de6178482e`；本轮实现提交 `02e9e7fc2c32a22960e1b2bff7f790b9127ee4ad` 已归并主线 |
| 远端 `origin/master` | 本轮已推送，推送后核对为 `02e9e7fc2c32a22960e1b2bff7f790b9127ee4ad`；后续文档更新以新的主线提交为准 |
| 线上现行 release | `2c6111bdefba4760cf67045dcdd2dc41333dc043` |
| SDK 打包源码基线 | `c7669cc67ff303a9d6fc45585d640a546a682dcc`，工作树干净，八个子模块 SHA 从 Git tree 读取 |
| 计划员工入口 | `https://agent.orbbec.com.cn/sdk/`，同源 API `/api/v1/sdk/`；**线上尚未开放** |
| 隔离禅道产品/执行 | 本地产品 `SDK-V1-ACCEPT-OpenOrbbecSDK` ID `1`，项目 ID `1`，执行 ID `2`；不得用于正式配置 |
| 本地镜像 | 禅道 22.6 arm64 `sha256:d7266f4cd4a5da594c987bcdaaac6c74b4a6a2946384cc378dc949b69ea0b99e`；MariaDB 10.6 arm64 `sha256:88ac718dafee1e8fb415233c04f314f3c3866af99177ce25d436106c62090461` |

## 本地真实链路

隔离 Docker Compose 实例安装了 ZenTao 22.6。安装第三步曾把 `default->lang` 写为空，导致安装后页面 PHP 类型错误、API 返回空体；核实原因为本地自动填写安装表单遗漏语言字段，修复试验实例配置为 `zh-cn` 后，未修改禅道镜像代码，API 登录恢复正常。先前失败保留在本记录，不计为通过。正式部署须通过安装页面核对语言并做登录 JSON 健康检查。

- 专用 `sdk-api` 账号属于禅道研发和产品经理组，担任**隔离测试产品** PO、执行 PM；无管理员组。真实 `/api.php/v2` 登录、产品/执行读取、需求创建/读回/列表、任务创建/读回均通过。一批专用账号试验：需求 ID `5`、版本 `1`、状态 `active`，任务 ID `4`，HTTP 200。之前管理员诊断记录 ID `1`–`4` 只在同一隔离实例中保留。需求写入带 `needNotReview=1`；评审由平台按禅道需求版本保存。
- 平台 SDK 路由 → 一次性 PostgreSQL（迁移至 115）→ 真实禅道的本地 ASGI 试验通过：写入前核对隔离产品、项目、执行及已有任务关联；独立成员授权、需求创建及同请求 ID 重放、评审前创建任务被 409 拒绝、当前版本评审、关联任务、候选冻结、真实产物登记。最新批次需求 ID `18`、任务 ID `17`、候选 ID `1`、构建 ID `1`，请求均按预期返回。平台身份上下文由试验注入，因此**未覆盖企业会话与 CSRF**。
- 本机 USB 枚举未发现 Orbbec 设备，依据为 `/tmp/sdk-live-openorbbecsdk-20261019/usb-inventory.txt`，并有 `/tmp/sdk-live-openorbbecsdk-20261019/hardware-blocked.txt` 说明硬件未执行；平台对该构建实际登记硬件 `blocked`，没有登记单测通过。门槛返回 `ready=false`，理由精确为 `macos/arm64:unit_not_passed`、`macos/arm64:hardware_not_passed`；发布负责人审批返回 HTTP 409 `sdk_gate_not_ready`，发布请求返回 HTTP 409 `sdk_approval_required`。没有伪造单测或硬件通过记录，也没有创建业务发布。
- 启用本地真实禅道的 SDK 专项及控制库迁移回归 `91 passed`，包括真实禅道契约与平台完整链路各一例；默认关闭时这两例会跳过。前端 SDK/权限/路由相关测试 `88 passed`，`npm run build` 成功，均为本轮已有记录。全量后端测试在不相关的 `test_result_artifact_recovery.py` 收集阶段失败：该文件导入不存在的 `worker_turn`；未宣称全量通过。
- 云和禅道 Compose `config --quiet`、发布脚本 `bash -n`、`git diff --check` 通过；生产部署脚本可保留 SDK 启用设置，密钥卷仅挂 API，ZenTao 不接入平台数据库网络。启用工具与正式发布共用操作锁，回滚配置的单元试验通过。检查是本地配置与脚本验证，**尚未在生产执行**。

## 实际 SDK 构建与缺失证据

从打包仓库基线的 `OpenOrbbecSDK` 子模块 `ad7518704a7ab630719a691748c4f36473a416bb` 复制源码，以 Ninja/Release/macOS arm64、示例/测试/工具/文档关闭构建 659 个目标。核心产物 `libOrbbecSDK.2.6.2.dylib`，大小 `11,036,680` 字节，SHA-256 `c92ce74dd8e08eb0cee8cf13f32f92e65190cf67ad33c433729435ab1ed7ba73`；本地日志 `/tmp/sdk-live-openorbbecsdk-20261019/build-arm64.log`，SHA-256 `c93b7bb3af2de728b5104a5ca41b6d5976f4126709ac542297ea1cdaa56518e8`。这是核心动态库构建，**不是打包仓库的全套发行包**。未检测到 Orbbec USB 设备；单元测试及真机报告缺失，真实 SDK 业务验收仍为 `blocked`。

## 线上只读预检与未执行项

- 清理前根盘可用 `23,403,442,176` 字节、使用率 `77%`；发布脚本要求可用至少 `26,843,545,600` 字节、使用率至多 `75%`。当时会在切换前拒绝，且尚未部署。下文记录了后续经授权的清理与复查结果。
- 正式控制库最高迁移 `113`，预览库最高 `112`；SDK 迁移 `114`、`115` 均未上线。没有生产禅道、生产服务账号、正式产品/执行 ID 或 SDK 密钥卷。arm64 本地镜像 digest 不可直接当作云主机 x86_64 digest。
- 云端现行 release 仍为 `2c6111bdefba4760cf67045dcdd2dc41333dc043`，与已推送主线不同。公网未登录请求 `/sdk/` 与 `/api/v1/sdk/me` 均返回 HTTP 401，只说明企业身份入口生效，不能作为 SDK 页面可用证明。待容量达标、正式服务配置完成后，再按主线准确提交走现有发布脚本，先部署且保持 SDK 开关关闭，核对迁移与旧功能后启用；企业会话、CSRF、线上 SDK API 与用户浏览器页面验收均未执行。GitLab 开发事件按首版范围暂不接入。

该记录不得作为“线上可用”或“业务发布通过”的证明。不得把密码、Token、Cookie 或客户敏感内容写入仓库、日志或报告。

## 线上容量清理与 ClickHouse 日志收敛（2026-10-19 补记）

用户授权清除 ClickHouse 历史诊断日志、清理 Docker 未使用内容，并降低非排障期日志等级。操作前根盘可用 `23,087,972,352` 字节、使用率 `78%`；`system` 诊断表的活跃数据为 `32,237,031,030` 字节，`default` 业务表为 `6,199,828` 字节。

- 通过 ClickHouse `TRUNCATE TABLE` 清空 `system.trace_log`、`text_log`、`metric_log`、`asynchronous_metric_log`、`part_log`。没有删除数据目录或 `default` 业务表。
- 清理 Docker 构建缓存 `2.129 GB`，删除 60 个没有容器引用且不对应现存平台 release 目录的旧 `orbbec-agent-platform` 镜像标签，再清理无容器引用的悬空镜像（Docker 报告回收 `820.6 MB`）；未使用强制删除，也未清理卷。
- Langfuse ClickHouse 服务及结构化文本日志从 `trace` 降为 `warning`，关闭 trace、周期指标和 part 诊断日志，保留查询和 warning 文本日志并设置 7 天 TTL。配置挂载在 `/etc/ai-fae-agent/clickhouse-production-logs.xml`，部署源码记录在 AI-FAE-Agent `a834ebd`；线上当前 FAE release 的 Compose、启动脚本及配置已同步。
- 复查根盘可用 `63,850,885,120` 字节、使用率 `37%`；ClickHouse `system` 活跃数据约 `48 MB`。ClickHouse healthy、Langfuse 首页 HTTP 200；`default` 业务表重建前后均为 19 个活跃 part、29,734 行、`6,191,930` 字节。平台 API 与 loopback healthy；平台 brain 容器在清理前已是 unhealthy，清理后状态未变。

容量门槛现在满足。**SDK 仍未上线**：正式禅道与服务凭据、单元及真机证据、企业会话验收等原发布门槛仍未完成。
