# HR 招聘情报后台生产运行手册

## 边界

招聘全景分析由独立后台任务生产，HR 工作台只读取通过质量门禁的发布版本。普通对话、岗位任务和网页访问都不会发起采集。失败批次不得替换当前有效版本。

每个版本保留三层独立数据：

1. 原始公开响应：内容寻址、不可变，位于 `/data/agent-platform/hr-intelligence/evidence/sha256/`。
2. 标准化岗位明细：数据库中的岗位快照和批次观测记录，包含来源、观测时间和内容 SHA-256。
3. AI 分析：独立版本，包含事实、推断、未知项、模型版本和事实依据。更换模型只能新增分析版本，不能改写前两层。

## 首次准备

来源目录必须部署到：

```text
/data/agent-platform/hr-intelligence/source-catalog.json
```

目录由代码评审和后台运维维护，不在 HR 页面开放编辑。所需配置通过 secret file 和环境变量注入，禁止把数据库口令、模型密钥或响应正文写入日志。

```bash
cd /opt/agent-platform/current/backend
.venv/bin/python -m app.hr.panorama_cli seed-sources \
  --catalog /data/agent-platform/hr-intelligence/source-catalog.json
```

## 定时生产

调度器执行：

```bash
cd /opt/agent-platform/current/backend
.venv/bin/python -m app.hr.panorama_cli run --trigger schedule
.venv/bin/python -m app.hr.panorama_cli status --current
```

建议按月完整生产；需要提高时效时可增加只读监控频率，但不要从业务页面触发。一个公司可以配置多个官方渠道，各渠道独立采集，单个渠道失败不取消其他公司。

当前代码直接读取公开、免登录的招聘官网数据：飞书招聘渠道使用官网自身的公开岗位接口，北森
`zhiye.com` 渠道使用官网公开岗位列表接口；请求一次取回完整列表，并核对响应声明的岗位总数。
如果声明总数大于实际返回数，批次将该渠道标记为 `response_truncated`，保留原始响应，但不会把
不完整列表伪装成完整数据参与发布。其他官网先按 JSON、JSON-LD 或公开页面结构解析；暂未适配的
结构记录为来源覆盖缺口，不从搜索摘要或模型记忆补造岗位。

成功标准：命令返回 `batch_id`、`insight_version_id`、岗位数和覆盖状态；`status --current` 返回相同批次的当前发布指针。`partial` 表示至少一个渠道失败，报告仍会明确展示失败来源，不能把未采集到解释为停止招聘。

## 恢复

查询批次状态后，只恢复 `queued`、`running` 或 `analyzing` 批次：

```bash
cd /opt/agent-platform/current/backend
.venv/bin/python -m app.hr.panorama_cli resume <batch_id>
```

`analyzing` 恢复直接读取已经固化的岗位快照，不重复抓取。采集和写入均使用确定性身份，重复执行不会覆盖原始证据。

## 失败与最后有效版本

- 无可用岗位、模型输出不符合证据约束或发布门禁失败时，批次标记失败。
- 当前发布指针保持不变，HR 工作台继续展示上一有效版本。
- 日志只记录批次 ID、来源 ID、URL、次数和规范化错误码；不得记录响应正文、请求头或密钥。
- 原始响应即使解析失败也应保留；网络连接前失败时允许没有响应证据。
- 不得在数据库中手工伪造成功批次或移动当前发布指针。

## 数据核验与下载

工作台报告应同时出现：

- “AI 分析”：事实、AI 推断、未知项和模型版本；
- “原始岗位数据”：完整岗位明细、来源 URL、观测时间和内容 SHA-256；
- “原始来源响应”：每次有响应的采集证据，可按 SHA-256 下载；
- PDF：包含 AI 分析、原始岗位数据附录和原始来源证据索引；
- Excel：包含 `原始岗位`、`AI分析`、`来源覆盖`、`证据索引` 工作表。

原始证据下载必须先验证证据属于所选发布批次，不能按任意文件路径读取。

## 发布与回滚纪律

- 发布 staging 只使用 `/data/staging/agent-platform/<deployment_id>/`，成功或失败均以 trap 精确清理本次目录。
- 持久证据、数据库备份和长期日志只能位于 `/data/agent-platform/`，不得进入 release、`/tmp` 或根盘持久目录。
- 发布前运行 `df -B1 / /data`；根盘可用低于 25GB或预计发布后低于 20GB时停止。
- 根盘只保留当前版本和两个回滚版本；历史版本归档到 `/data/archive/agent-platform/releases/`，最多 10 个或 30 天，取更严格者。
- 不修改共享 Nginx，不重启无关服务，不清理其他应用镜像。
- 代码回滚不会删除证据或发布历史。回滚后再次确认 `status --current` 和业务页面仍读取最后有效版本。
