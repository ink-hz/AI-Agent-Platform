# FAE 总体架构设计发布

2026-09-23 已上线。「AI 工作 → Agent 设计」顶部选择「FAE Agent · 总体架构设计」。复用现有阅读页、章节导航、安全 SVG 图表和管理员访问限制。

源：`AI-FAE-Agent/FAE总体架构设计.md`，1423 行、16 张 Mermaid。采集时源仓库 HEAD 为 `66fd24f83e3bfeaa4fcdb890c50255fe1cf185da`，该文件与已提交版本一致；SHA-256：`1bedd572d8c6a3fd54f31f9561bdfc0bcff882dcb925d0726e0611c5c0a18044`。原文与“目标架构，未全部实施”说明保留。HR 原有正文与来源记录未变，仍是默认首篇。FAE 仓库及其他未提交文件未修改。

显式同步：`python3 scripts/sync-agent-design.py --agent fae --source-repo /path/to/AI-FAE-Agent`。脚本按指定 Agent 更新快照与来源，保持登记顺序和其他篇目；原 HR 命令继续支持。

- 应用提交：`a98e7da0f2b27d38fcd98759950dc4ecbf7057d3`；前一应用：`6ebecdd2abe219eb9594f67df50b4ea778f24805`。
- 镜像：`sha256:43c916404ce111d9d36a100e6fd54d80e3604c8dcbd00efb7877c1135ee27829`；API 容器：`ddb2a708d91700ed7bbb60761a610733145d090f34217ab63c1ee37e3afa959a`。
- 源码树：`3f0ae5a2ad63e00db91b242087a0af039c061d62`；归档 SHA-256：`3e9e3b7afabfc7348f3d676eb88205eec31b88b3a14ae3954c7cfc0fbe6fe7c9`；清单 SHA-256：`e25337e7837f723faec57d8995f86872122a22b87544bab422037d169381eb53`。
- 后续维护：`/opt/orbbec-agent-platform/private/panorama-cf5ba175c55b42a39cfb3d290a59674f/future-maintenance.json`；沿用 30 份 Compose 输入，仅追加 API 镜像/版本覆盖，共 31 份。

验证：新增 FAE 读取用例先因 404 失败，接入后后端 8 项通过；前端 14 项通过，包括 HR/FAE 全部 32 张 Mermaid 的实际渲染、所有原文锚点、切换/权限失效与大图交互。构建、15 项发布事务测试通过。自审确认原文完整、HR 快照未变、选择器复用现有能力。

线上 35 项检查通过：FAE API 匿名 401/private/no-store，运行镜像内两篇文档 SHA-256 与元数据一致，FAE 16 张图完整；静态资源哈希与镜像一致。API healthy、重启 0、锁释放，24 个其他容器、Nginx、systemd 与持久挂载未改变。未修改 FAE 执行逻辑、权限或业务数据。未运行浏览器；真实阅读和点击效果由用户验收。

私有证据：`/opt/orbbec-agent-platform/private/panorama-cf5ba175c55b42a39cfb3d290a59674f`；本地：`/tmp/fae-design-release`。本记录提交不改变应用版本。
