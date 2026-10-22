# 2026-10-22 DAQ 管理观测接入

## 原因与契约

DAQ 已有用户入口和 Agent Catalog，但原远端采集仅配置相机 FAE、ADMIN；云端副本没有 DAQ 会话，云模式也未运行 DAQ 健康探针。入口注册不代表管理数据接入。

接入沿用源库只读导出 → 本机管理镜像 → 归一化只读视图 → 脱敏/稳定匿名标识 → Ed25519 批次签名 → 加密云端副本 → 管理仓库。没有直接向云端伪造记录、冒用管理员身份或发送生产模型问题。

- 采集身份 `daq`，独立镜像 `platform_source_daq`，源库仅 DAQ 容器的 `DAQ_DATABASE_URL` / `daq_fae`。逐项校验安装身份，源事务为 repeatable read / read only。
- 管理 API 的 `source_kind=fae` 是运行时家族；`agent_id=ai-daq-fae-agent` 才是 DAQ 身份。本机 session/turn/trace 键使用 `daq:`，云端沿用已有带作用域的匿名标识，不保留原始键。
- 外键留在 DAQ 镜像内，不能把相同 UUID 绑定到相机记录；相机统计显式限定 `ai-fae-agent`。会话所有者仅使用已验证的持久化稳定身份，缺失时保持 unavailable。
- 保留真实 outcome、fallback、根 trace、版本与来源字段。普通 `elapsed_ms` 是阶段耗时，不能当作绝对时间偏移。没有真实阶段时间戳时不合成时间线。
- 云端继续采用既有脱敏 trace 摘要策略；完整步骤/原始证据不对云端开放，`steps=[]` / `detail_availability=unavailable` 必须如实呈现。
- 只启用明确配置的 DAQ HTTP 健康检查；不启用云端原有相机/ADMIN SSH 轮询，也不猜测模型、引擎或 uptime。健康与使用量新鲜度独立。

## 实际发布身份

| 项目 | 身份 |
| --- | --- |
| 生产源码 | `b0301d9865d60d265ce67d2909b88f17975c8e33` |
| 远端发布分支 | `fix/daq-admin-runtime-20261022` |
| 主线整合 | `3915dc7c`，已推送 |
| 旧 API 源码基线 | `21d82d60155076dcd0086fa76fa2a86cd6457ce9`，480 个实际文件全量相符 |
| 新 API 镜像 | `orbbec-agent-platform:daq-admin-b0301d9865d6` |
| 新镜像 ID | `sha256:1ff94eb92ab93b89d69ebd57ba7d589c24538949b4bf42b46d6a086dc0752994` |
| 旧镜像 ID | `sha256:bb9735c95d0f5bbec189bb2fbe51c1d67508b2f3b9f84e3631c2fb3f0bf26743` |
| 本机迁移 | `015_daq_observability.sql`，SHA-256 `17e3ed5239e8935766779eed80571035809b404f7ef600c17eef8940823a6c8d` |

生产候选由准确旧生产基线加本次 DAQ 提交构建，未夹带主线已有但尚未发布的 SDK 工作。旧 API 镜像覆盖层保留原前端；481 个新源码文件及全部静态资源逐文件校验。沿实际 17 份 Compose 配置增加单服务覆盖，只有 Platform API 重建，其他容器 ID/镜像不变。`current` 软链接不能代表 API 实际源码版本。

运行覆盖文件位于 VM `/opt/orbbec-agent-platform/staging/daq-admin-20261022/overlay.json`，包含明确的 DAQ 内部健康 URL 和发布 SHA。该目录是当前活动 Compose 依赖，不能当作临时下载目录删除。后续发布须保留 `PLATFORM_DAQ_HEALTH_URL=http://172.31.0.9:8081/health` 或重新审核实际内部地址。

## 持续同步

本机 owner 显式应用校验和迁移；应用启动不会自行执行 DDL。原 owner 凭据为 libpq 格式，本次仅转换为 mode600 临时 URL 供已有迁移 runner 使用，操作后删除；没有改写原凭据。

`deploy/install-daq-sync-launchagent.sh` 安装 `com.orbbec.ai-agent-platform-daq-sync`，每 300 秒执行 `deploy/sync-remote-agents --source daq`。`review_backfill=not_enabled/daq_observability_only` 是有意的功能边界，不执行相机/全局回填。原每日相机/ADMIN 任务保持原状，其既存 review/outbox 阻断没有被改成成功。

既有 `com.orbbec.ai-agent-platform-cloud-sync` 仍每 300 秒沿原签名加密链路推送。发布期间临时暂停该任务，避免旧 importer 收到新增受控枚举；新 API 上线后顺序处理积压，既有 watermark、队列和签名序列不跳跃、不重置。首次积压处理后的任务恢复结果见下方生产验收记录。正常情况下采集加推送约两个周期；积压、源失败和网络故障会延迟，不能写成严格实时 SLA。

云端 forced importer 从当前 API 的准确镜像启动，所以新 API 同时提供新导入契约，无需旁路导入服务。

## 验证与评审

- 源数据契约：292 项相关定向测试通过；最后 45 项 DAQ/Postgres/importer 门为其中受影响部分，不累加虚报。包含隔离真实 PostgreSQL、受限 writer、analyst、第二次导入、UUID/FK 冲突、权限、来源拒绝、迁移幂等/依赖/校验和失败退出及真实签名加密链。
- 健康链路：165 项定向测试通过。2 项旧 HR `test_main.py` 失败在准确生产基线复现，按原样记录并从接受门排除，未称为全量通过。
- 部署助手初审发现容器缺失时不能回滚、覆盖配置/标签未重新固定等问题，已先修复；独立 Codex 复审绑定 SHA-256 `59668beb50df7327a815b78e7944e0f067c50b170225549e0c09bf0324c29597`，12 项本地故障模拟全部通过。包含缺失 API 恢复、真实 Docker 错误拒绝、覆盖文件/标签/渲染配置漂移、其他容器及 API CAS。
- 部署前重新 seal 包/源码/镜像/静态资源/原配置/覆盖配置，切换前再次校验全部容器；备份后只重建 API，健康通过。没有执行生产回滚演练。
- 不重复已通过的模型场景。本任务真实模型调用数为 **0**；浏览器登录态页面验收未执行。未登录 `/admin`、`/admin/agents`、`/admin/sessions`、`/api/agents`、`/api/sessions` 返回 401，权限未绕过；`/api/health` 和 `/daq/` 返回 200。

## 实际生产验收

首次和第二次 DAQ 源同步均成功：12 个会话、20 轮问答、20 条根 trace、2 条反馈；第二次导入没有新增重复记录，孤立 session/turn、缺根 trace 和坏 trace 行均为 0。本机相机会话仍为 1531。

云端沿既有链顺序确认批次 `14246–14253`，没有跳过水位。使用新生产容器中的实际 `ReplicaObservabilityRepository` 和实际只读服务账号解密读取：

- DAQ Agent：12 个会话、20 轮问答；每条 agent 身份、family、channel 与预期一致，匿名 session 键与相机无碰撞。
- 20 条 trace 均为 `completed`；原有 outcome 为 resolved 9 / safe_abstained 11，如实导入，没有因本次接入改写旧答案结论。
- 一个已有非空回答的 session → turn → trace 关联一致；trace 耗时 28,748 ms，步骤明确 unavailable。只保留答案摘要哈希，未把正文或个人信息打印到证据收据。
- 云端相机会话 1531，原有积压中的 8 个相机会话通过同一正常队列补齐；DAQ 没有计入相机。
- 副本新鲜度 current。Platform 生产容器执行 DAQ 实际健康探针为 healthy/connected，runtime `daq-5e86db35722f`、主知识 `8ab080a4fc404a2cb88b4c5f1f2ec4f660895b3525c39812f1d095f499300ea3`、SDK `514fbb3cc29c17bbcaa6b7b8b12ee6791b255d29d198cd5a4ef65b7eeb50a041` 均匹配原发布；探针中的模型、引擎和 uptime 保持未知。
- 两个 300 秒任务均 loaded/enabled、最后退出 0；云端任务的临时 disable 已撤销，源任务已自动执行成功。已有未确认队列继续由原任务处理，没有删除或重置状态。
- 最后实际容器核对：Platform API、DAQ、相机健康、重启计数 0；全部原非 API 容器 ID/镜像未变。DAQ 与相机知识、代码、回答运行时未切换，未修改相机仓库。

只读验收脚本的失败也保留：最初全量 `load_config` 的诊断子进程缺少主启动流程注入的钉钉配置，改为读取仅需的真实副本文件路径；随后脚本把健康字段误写为 `runtime_id`，改为源码合同中的 `runtime_release` 后成功。期间没有修改服务代码、配置或知识。未登录管理页面按既有策略返回 401；初始脚本把它误判为必须 200，已更正验收解释，没有放宽权限。完整调用栈和最终收据均保留。

仓库诊断脚本的 `deployment.auth` 取构造器默认标签 `ssh-tunnel`，不作为实际认证模式的证据；另行只读核对的生产 `PLATFORM_CLOUD_AUTH_MODE=dingtalk` 才是运行配置，外部 401 为实际认证边界证据。未读取或复用用户 Cookie。

未执行：登录态浏览器页面验收、完整原始 trace 向云端开放、生产模型问答、生产回滚演练。未将这些写成通过。

## 回滚与证据保留

VM `/opt/orbbec-agent-platform/staging/daq-admin-20261022/backup/` 保存原环境、17 份配置及 `platform_replica` 的 custom-format 数据库 dump，已用 `pg_restore -l` 验证；dump SHA-256 `2813459b1623e1f58b4554f7f2a57479421254610e2ee9ff1bf7001e555ecf91`。本机迁移前另有 schema-only 备份。备份含私密配置，只保留在受控位置，不提交仓库。

受控回滚入口为同目录 `vm_deploy.py rollback`，固定旧 image ID，仅重建 API；可以处理重建失败造成的容器缺失。回滚前停止 DAQ 采集与云端推送；保留签名队列、水位和已导入历史。加法迁移可保留，不自动恢复数据库或删除业务记录。由于不可变 image-ID 回滚容器不满足旧 forced importer 的标签检查，恢复推送前必须核对旧标签到原 image ID 的绑定，并按原 Compose 配置恢复标签式容器身份；未完成前保持推送暂停，不能删除队列使其表面成功。

本机完整受控证据在 DAQ 仓 `tmp/daq-admin-integration-20261022/`；独立评审、RED/GREEN、版本、备份及只读收据由该仓对应发布记录和哈希索引关联。该目录不含发布私钥或复制的用户登录凭据，不能因 worktree 清理删除证据。
