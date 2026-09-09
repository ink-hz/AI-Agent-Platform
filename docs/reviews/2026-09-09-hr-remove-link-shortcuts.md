# 删除 HR 飞书关联与招聘快捷短语

2026-09-09 已上线。用户明确删除“关联飞书”，随后指出“招聘协作”只是填入一句预设消息、没有实际任务行为；两项一起移除。不要重复部署或运行已完成检查。

## 最终行为

主对话保留岗位选择、查看 JD / JR、材料选择与普通输入；删除 HrChannelLink 组件、快捷短语数组和“招聘协作”菜单。查看 JD/JR 与标准保存能力不变。

Platform 删除关联码生成/解除接口、关联身份服务、专用 Worker 路由、授权登记和本地代理。MetaBot 删除 HR 渠道拦截及仅为关联采集的身份字段，HR 飞书消息继续进入独立 Bot 处理，不转入 Web 会话、不要求关联。历史消息中的来源字段、数据库历史迁移与已存记录保留；旧关联记录没有运行中的读取/执行入口。没有清空岗位标准或对话。

## 验证边界

- 一次性真实 PostgreSQL、Web 会话/CSRF 与签名 Worker HTTP：先复现旧关联 POST 200，删除后 Web 旧路径被中央授权拒绝 403、签名渠道路径不存在 404。正常 v6 读取、提交、部分标准确认与幂等检查通过。
- MetaBot 定向回归：HR Web v6 仍开启时，飞书原生 reset 指令正常进入 Bot 命令处理，未被关联拦截。提供方与发送器使用测试替代，没有发送生产业务消息。
- Python 模块解析、MetaBot bridge 编译、网页 TypeScript 和 Vite 构建通过。删除型 UI 变更未追加浏览器巡视，未重新运行模型或故障套件。

## 生产

- Platform API / 本地 Signed Worker：`1ee646b1654f12af1851381b5e142ffc74bd0576`。
- HR MetaBot：`82ecbb93b46fed4ca1ec0c0890786fe090fc0b88`。完整源码与编译 dist 安装到固定 release，保留原私有配置、依赖、模型和角色包。Team role/knowledge 未变。
- 切换前 HR 非终态轮次 0、独立 Bot idle。本地两个进程切换后在线，认证 v6 readiness healthy；其他 PM2 进程 PID/重启次数不变。
- 云端仅更新 API，其他容器（含独立 HR Worker）身份与镜像、Nginx 哈希不变。健康检查通过；线上源文件确认关联模块与挂载点不存在；入口资源为 `index-D-Oe5qyI.js` / `index-UreVbVab.css`。不迁移数据库。
- 云端首次切换后附加 create_app 检查未加载入口进程注入的私有配置，触发自动回退；服务自身 health 正常。随后以同一镜像复核实际安装文件与公开入口，完成发布。没有重新构建或重跑工程验证。
- 容量门禁通过；原始磁盘记录为元数据 `df.initial`，最终为 `df.after`。保留源码 current + `56465b1` + `294506a`，归档沿用既定 10 版/30 天规则。发布锁和 staging 已清理。

云端证据：`/data/orbbec-agent-platform/release-metadata/1ee646b1654f12af1851381b5e142ffc74bd0576/`。本地快照及 readiness：`/Users/agentops/AgentRuntime/instances/hr-bot/remove-link-20260909/`。未代用户完成真实飞书聊天或模型业务验收。
