# 大制造工作台首版上线

2026-10-27，[大制造工作台](https://agent.orbbec.com.cn/manufacturing)已上线。初始开放平台 owner/admin，使用现有登录、权限、侧栏和页面外壳。真实账号浏览器验收由用户完成。

业务源码位于同级 `AI-Manufacturing-Platform`，平台仅承载认证、授权、路由和前端挂载。首版支持内部自研相机（含数采相机）、3D 打印机及外部 OEM 的流程现状、待办和参与方/交接资料/系统关系登记；真实 ERP/MRP/MES 接口尚未验证。

| 发布项目 | 实际版本 |
| --- | --- |
| 平台增量源码 | `b141294d0fab99ea58383c17690a9584dd01c1b8` |
| 制造源码 | `3cc02ffe5b9c02f332c75c3db84383a7fd20750c` |
| API 镜像 | `sha256:bce8f41283043cef158dc04192ed508b197366a1c3aabda48447cc6be307e496` |
| 维护清单 | `/opt/orbbec-agent-platform/staging/manufacturing-v1-20261027-r2/future-maintenance.json` |

候选从当时实际运行的 `900c4181…` 镜像派生，保留原线上补丁，仅 COPY 本次七项后端文件及平台前端构建产物；并非全量重建主线镜像。原 37 项 Compose 输入保持并追加一项制造 overlay，仅重建 platform-api；原 13 项挂载、四个网络、安全设置及其他 29 个容器保持。Nginx 未修改或重载。

制造源码/JS/CSS 从 root 控制的不可变版本目录只读挂载到 `/opt/manufacturing`。独立 UID/GID10001、0700 数据目录挂载到 `/app/data/manufacturing`，SQLite 文件为 `workbench.sqlite3`；回滚必须保留该目录。

相关后端回归 1001 项通过，归并后桥接 58 项通过，平台前端 261 项通过并完成 TypeScript/Vite 构建。最终制造包 71 项、发布守卫 15 项、制造前端 26 项通过。隔离候选以合成身份、真实应用工厂及真实中间件验证权限/CSRF、保存/冲突/幂等、版本化资产，以及另一独立进程读回 SQLite。生产完成 healthy、匿名边界、完整内容摘要及其他入口资源保持检查；没有冒用员工 Session，没有写入演示业务记录。

完整设计、版本、证据、回滚说明和用户页面验收路径见同级项目的 [首版发布记录](../../../AI-Manufacturing-Platform/docs/release-2026-10-27.md)。后续维护须使用实际运行标签和完整有序清单，不能用过时 current 链接替代；文档提交不改变上表实际部署源码。
