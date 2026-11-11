# 工程知识库发布合同（候选准备，未部署）

2026-11-11。本批只准备候选与只读证据，未 build/plan/apply、未创建或替换生产容器，未修改生产配置、业务数据或共享 Nginx。正式发布由 controller 在任务审查和主线合入后执行；准确 release SHA 必须以参数传入，不能把任务分支当已合入主线。

## 真实基线与漂移

先前只读证据确认 API runtime `f8f362596ed81cac8360c0aa202acb545509ea21`，API 镜像 `sha256:ef3d0dec46610578042dbb1664a8a7a81026dff19c6c1a75a09958b74294f2ff`、78层、66项有序 Compose、35容器。28线上源文件与该 commit 一致。UI 来自 `3c844cbb1509427f5eaa719c5e3d73197c4ca899`，两个提交的 webui Git tree 均为 `6909858923c2d4a6f997b71e8c2de32baa1b95b3`。这些历史观察不是发布时有效凭据。

本任务新鲜只读捕获在精确基线检查处停止：实际 Compose 已变为67项；镜像为 `sha256:5eaec32b3912e76f20d458755a00648213b2738623d83ad49101ece19b0ff8c3`，应用引用 `orbbec-agent-platform:manufacturing-v2-f8f36259-53958c7c`；API容器 `bb0c6d4ccbaab3692ec490a6b201cbce6f4300929284b902446e3f52002f339f`，启动 `2026-11-11T09:10:28.843056248Z`，仍为78层/runtime f8f36259/35容器，双锁为空。末项为 `manufacturing-workflows-20261111-r1/overlay.json`。随后已重新捕获全部67项原序输入、35容器和guardrails；29线上源文件（含README）逐项hash/size与f8f36259一致，581公共资源/index/vite与11-09 artifact-provenance精确一致。新证据保存为capture-refreshed.json及refreshed-source-ui-proof.json；可采用新immutable基线。容器Created为2026-11-11T09:10:27.950976042Z，镜像Created为2026-11-11T08:56:15.956704291Z。发布前仍需刷新，不允许沿用旧image/66项recipe。

## 候选与安全边界

范围为本批四个 Python 文件（models/repository/validate/main）的实际差异、完整 library（5分类29文章+5分类索引）及精确提交构建的静态资源。其他 Python 差异立即停止。旧 content 不复制、不删除，运行入口只扫描 library；旧14路径由显式 legacyPaths 归一到同一文章。

私有 assembler 逐文件比较新捕获线上源码与 f8f36259；验证 UI Git tree、所有旧 public assets，合并新 Vite allowlist，同名异hash即停。普通 stage 的 migration gate不变，本批不附带无关迁移；assembler显式生成无迁移manifest并经受测validate_stage检查。镜像仅 immutable FROM、release ENV、单条 COPY --chown=10001:10001，新增一层，不RUN、不安装、不pull、不改配置。

私有操作根 `/Users/neo/Developer/work/AI-Agent-Platform/tmp/engineering-knowledge-20261111`；精确脚本哈希与本地验证见 `.superpowers/sdd/engineering-knowledge-task-3-report.md`。0700操作目录、0600元数据/脚本、0644内容、0755 payload目录。严格context只含Dockerfile及枚举payload；receipt独立存放。

## Controller执行顺序

1. 确认新主线提交与origin/master一致、tracked clean；未跟踪项仅允许精确 `backend/.venv`（依赖symlink，不提交）。刷新35容器、完整有序Compose、Admin/Nginx/systemd/worker guardrails与双锁。不得删除/接管他人锁。
2. 用仓库当前helper默认profile重新capture；完整审查其私有证据，不复用历史observer v5适配。以精确merged SHA运行stage-local.py，重新构建dist；dry-review产物明确不可发布。检查Python覆盖证明、静态完整并集、Dockerfile和manifest，再上传枚举context。
3. build-and-plan.py只构建并plan，不apply。候选UID10001逐文件核验，运行verify-container.py真实repository+router只读检查5类29文、14别名、未知404和no-store；再inspect-plan，确认只改platform-api image/release，原全部输入与其他容器保持一致。
4. controller使用apply-reviewed.sh执行受测helper事务；若失败保留receipt与自有锁，先调查实际状态，不自动重试/回滚。完成后verify.py核对健康、实际image/release、其余34容器与guardrails、全部新旧HTTPS静态资源hash/size、旧14公共shell的index内容、匿名401/no-store与容器内真实repo/router。

## 身份验收与回滚

未找到可证明授权且有效的专用验收会话。容器内router测试只证明内容/路由契约，不能冒充真实登录HTTPS验收；不伪造session、cookie或任意staff身份。真实登录接口和浏览器视觉验收留用户，均尚未执行。

准确回滚点必须是发布前新capture与transaction/state.json中的实际旧image应用引用、immutable ID、runtime SHA和完整Compose链，不使用本文件的历史ID。rollback-reviewed.sh仅由controller明确诊断后调用helper rollback；本批无数据库迁移，原应用tag不得删除或重指。发布后在此补实际merged SHA、image、事务路径/receipt、接口/静态/容器检查结果及真实登录/浏览器尚未执行项；不能预写完成。
