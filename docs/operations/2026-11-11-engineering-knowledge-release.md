# 工程知识库发布记录

2026-11-11 已完成代码合入、部署与下列自动验收；真实登录 HTTPS 正向及浏览器视觉/交互验收由用户负责，尚未执行。

## 本次结果

`/ai-notes` 更名为“工程知识库”，保留原路径和 `/api/v1/ai-notes` API。共29篇：原14篇AI正文保持完整字节、slug、日期与标签，新增15篇精编文稿；显式legacyPaths兼容全部14条旧分类/文章链接，规范replace保留query与hash。

| 主分类 | 篇数 |
| --- | ---: |
| AI 与 Agent | 14 |
| SDK 系统架构 | 1 |
| 制造业务与流程 | 8 |
| 企业系统与集成 | 3 |
| 智能制造架构 | 3 |

制造手册、完整目标方案和总体架构各保留全文及章目录；材料所述现状、目标设计、2026-11-09实施快照分别标注。SDK为固定八仓基线的架构导读，不包含场景卡；未构建SDK、连接设备或做现场制造实施验收。来源与必要编辑见[收录来源清单](../knowledge-library/2026-11-11-source-manifest.json)，写作准入见[工程知识库标准](../standards/ai-engineering-notes.md)。本次只写平台副本，未批量导入FAE知识库或其他被排除稿。

## 主线与实际运行版本

- 已审查并合入、推送的应用提交：`d32a3b1bed8e480ba16ed02653d65d9c9e4a3e52`。
- API容器：`63f378f9f7a2c6596683da9ff2927607afb6235428372a8020816407a2c116f0`；启动于 `2026-11-11T09:58:47.173731473Z`（UTC）；healthy，RestartCount=0。
- 当前不可变镜像：`sha256:f1d1058feb71cbaf5d5e344f26dca2edc23273c0c60ec1e7e74878460a97fb7e`；由实际最新基线增加一层至79层。
- 应用镜像引用：`orbbec-agent-platform:scoped-api-d32a3b1bed8e480ba16ed02653d65d9c9e4a3e52-288c16b719b8be5dc3184e5ad7e6def1e4291b9349ea60689845bff265ac82b7`。
- 原不可变镜像：`sha256:c6ebf87da70839debd71824991f0b543113bad953b72dea63489eb8b5855e878`，原引用 `orbbec-agent-platform:manufacturing-v2-f8f36259-43a893dd`，原runtime为 `f8f362596ed81cac8360c0aa202acb545509ea21`。
- 保留原68项Compose有序输入，本次candidate override为第69项。API有效配置只改image/release；其他34容器及Admin/Nginx/systemd/worker保持本次捕获状态。
- Manifest SHA256：`288c16b719b8be5dc3184e5ad7e6def1e4291b9349ea60689845bff265ac82b7`。104个payload文件：4个Python、34个library Markdown（29正文+5分类索引）、66个精确提交构建的静态文件。旧content层保留，main/validate只扫描library。

UI旧基线来自 `3c844cbb1509427f5eaa719c5e3d73197c4ca899`，其webui Git tree与f8f36259一致；581项原公共资源/index/vite逐项核对后，新manifest保留旧hashed assets白名单，与新资源合成638项。同期制造模块更新已作为最终基线保留。

## 验证与证据边界

| 验证 | 实测结果 |
| --- | --- |
| 内容发布校验 | 5类、29篇，作者/日期/正文/图示发布策略通过 |
| 本地后端知识库与发布工具 | 213 passed，1个可选历史源稿校验skip；本轮15份源hash独立完整核对 |
| 前端回归 | 166 passed，含96图真实Mermaid渲染与DOMPurify；suffix修复后reader/router定向95 passed，tsc通过 |
| 构建 | 精确主线提交npm run build成功；旧chunk大小及jsdom/localStorage提示保留 |
| 内容保留 | 原AI14篇正文逐字节一致；15份源稿286章节、原表格及53张新图关系完整核对 |
| 候选镜像 | UID/GID10001读取679个文件hash/size通过；单层派生与image Config继承通过 |
| 线上内容与真实router | 容器内5类29篇、34个Markdown、14别名与完整正文、未知404及no-store通过；此项不代表真实登录HTTPS |
| 线上公共静态资源 | 638项、46,719,273 bytes，完整HTTPS SHA256/size一致，CSS MIME正确 |
| 旧14公共shell | 直接200、text/html、no-store、完整服务端响应字节hash/size均通过 |
| 匿名API | 目录及未知文章路径均401/no-store，共2路；未伪造session/cookie/staff身份 |
| 共享生产保护 | 其他34容器Config/HostConfig/Mounts/PID/StartedAt/RestartCount、Admin/systemd、共享Nginx文件、worker12配置hash及只读health通过；自有双锁已释放 |
| 日志 | 当前API双日志流未见PermissionError、permission denied或Traceback |
| 未执行 | 真实登录HTTPS正向、桌面/手机视觉与交互、现场业务/设备/真实模型验收 |

未执行数据库迁移、模型调用或Nginx配置/重载/独立检查。浏览器验收按AGENTS.md由用户完成；组件和真实图示渲染不替代该项。

## 发布期间的保护与校验修正

初次fresh capture遇到外来action锁，等待其正常释放，未删除或接管。第一候选构建后，另一次制造模块发布改变运行镜像；plan以runtime_changed拒绝，未apply。随后重新捕获最新基线，保留全部68输入并生成最终候选。失败证据及未发布候选保留，不用于回滚当前版本。

首次线上verify最后一步把原始index文件pin用于公共shell，产生假阴性。既有 `routes_auth._shell_response` 会改写相对资源路径并注入identity-enabled：原文件536 bytes/SHA256 `bebc7d6f2a7d738cb808745b19168574c88efdf1500ef2a1d914a47bcab57a66`，真实响应532 bytes/SHA256 `31184b0bc9387466992370b89baa6e2e30a7ca11268b5d1af401baa6b1544bb2`；实际HTTPS与真实renderer完整字节一致。应用无需改动。v2先验证原文件pin，再调用运行镜像内真实renderer生成完整响应pin，保留全部旧门禁并检查14 shell的no-store；独立审查和3项离线RED/GREEN通过后，完整只读生产验收exit0。未strip未知差异、降低hash检查、伪造登录或覆盖首次失败结果。

## 事务、归档与回滚

最终事务目录：`/opt/orbbec-agent-platform/private/engineering-knowledge-20261111-d32a3b1bed8e480ba16ed02653d65d9c9e4a3e52-20261111T095637Z/transaction`。

最终独立只读验收目录：`/opt/orbbec-agent-platform/private/engineering-knowledge-postrelease-v2-20261111T101304Z`。原verify失败证据留在事务父目录，v2结果另存。

本机私有证据归档：`/Users/neo/Developer/work/AI-Agent-Platform/tmp/engineering-knowledge-20261111/postrelease-receipts.tar`；SHA256 `74b08b46b9c364c50f1a80d8460450463ad2a81c1cd3f874bb25b3ef6d676815`。控制状态与原始/修正验证日志位于本机 `tmp/engineering-knowledge-20261111/`，均为私有证据，不提交运行态或凭据。

仅在明确诊断需要恢复时执行以下已审流程；恢复实际原c6ebf87镜像、原f8f36259 runtime与完整68项输入，不删除/重指原应用tag，也不自动回滚：

```sh
sh '/opt/orbbec-agent-platform/private/engineering-knowledge-20261111-d32a3b1bed8e480ba16ed02653d65d9c9e4a3e52-20261111T095637Z/rollback-reviewed.sh' '/opt/orbbec-agent-platform/private/engineering-knowledge-20261111-d32a3b1bed8e480ba16ed02653d65d9c9e4a3e52-20261111T095637Z'
```

当前部署及上述自动验收完成，用户登录与页面验收待完成。后续仅更新本发布记录的文档提交无需重新部署应用。
