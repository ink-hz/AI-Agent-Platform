# 全景功能被旧镜像覆盖：恢复记录

2026-09-28 线上平台 API 容器运行 2026-09-14 提交 `1f12b429` 的镜像 `sha256:0327cc3a6259994b2546aaf3d3e3ca3fa13911e7d53afe7a78d4a9342a293213`。当时 `/opt/orbbec-agent-platform/current` 仍指向 `2cecab5e`，主线也包含全景核心信息；但容器中前端 bundle 没有 `panorama-ai-core`。容器的 Compose 输入只有基础平台、HR 和当前 HR generation 三份，没有全景布局状态挂载与镜像覆盖。容器镜像版本才是实际线上版本，`current` 链接和仓库 tip 不足以证明页面已上线。

本次从已经归并的主线 `184b7d86c7dd10765462c7caf424c4771067c532` 构建镜像 `sha256:a83a32a16df4ba9f266c781cc3016585737dd08d8ec85548c6eb665895ff977c`，包含前次全景改动与随后接入的访问记录改动。以当时正在使用的三份 Compose 输入为基线，仅为 API 增加该镜像、版本标记及原有全景状态目录挂载。没有重建当前 HR generation、附件、FAE、VOC、行政或其他容器。恢复前后原全景发布版、草稿、上一版及修订号哈希一致。

前一次部署残留 `deploy-input.lock` 与 `agent-brain-action.lock`，记录的身份、时间与当时失败部署一致，且没有对应运行进程。取得共同发布锁后，按记录身份调用标准 helper `release` 清理占用，随后执行 API-only 事务发布。事务检查原镜像与服务健康、三份配置输入的摘要、候选镜像来源、只允许 API 的镜像/版本/状态挂载变化及其余 24 个容器不变；失败时可用基线配置恢复。

结果：API healthy、重启 0；线上 35 项资源、鉴权、代理与服务检查通过，其中加载的 JS/CSS 含 AI 全景核心标记；平台 `/ai-engineering` 恢复 200，未登录的受保护 API 仍为 401。前端全景和访问记录相关 9 个测试文件共 103 项通过；构建时四层图、CJK 导出及隔离发布/恢复烟测通过。浏览器中管理员实际视觉仍由用户验收。

当前应用版本 `184b7d86c7dd10765462c7caf424c4771067c532`；镜像 `sha256:a83a32a16df4ba9f266c781cc3016585737dd08d8ec85548c6eb665895ff977c`；源码树 `ca28f2a2495582dab2a84c50157f71947c441388`；归档 SHA-256 `e5a84430e43fddb16947cf09cf9d5386673faabb26e9d8e5e48adf233d032497`。原始证据与构建回执在 `/tmp/panorama-restore-release`；宿主机私有回执在 `/opt/orbbec-agent-platform/private/panorama-97284f7fd30b4ef68777fb83a376b412/future-maintenance.json` 同目录。后续维护必须从该文件的 4 份 Compose 输入（当前 HR 三份 + API 恢复覆盖）继续，发布前对比当前容器与候选配置，不能仅从基础 Compose 重建 API。

**仍有部署流程风险：**标准 `remote-stage.sh` 有 HR overlay 门禁，但没有通用门禁确保候选 API 配置继承当前容器的全景挂载及其他后置覆盖。此轮已恢复线上实例并记录准确维护输入；今后的跨团队发布仍必须经过该差异检查。
