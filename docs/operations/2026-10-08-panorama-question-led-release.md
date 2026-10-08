# 全景图 AI 探索问题回到业务图：发布记录

2026-10-08 从已归并并推送的主线 `c7447fd71130e49673365d2c26f739e2af7ba1af` 构建并发布平台 API 镜像 `sha256:83b7f1483be9df938b4584c977d4665c184cce98d7ca8586729bc3abbf2f5dbd`。线上 `/opt/orbbec-agent-platform/current` 与 API 容器的 `PLATFORM_RELEASE_SHA` 均指向该提交；API healthy、重启数 0。源归档 SHA-256 为 `31b545184e209255cb80c54c00de4ad972b366603acaca776ee8822efb6a1bb8`，逐文件清单已核对。发布前线上 API 为 `587e7072e1b03dffa79b18024a16376a14f84557`，不是此前记录的 `184b7d86`。

默认全景移除了图外“四阶段”和九专题导航，改为图内一条“AI 工程判断”及业务节点上的具体问题。问题仍标注待验证；点击打开原专题推演，节点名称及原业务动作保持。自定义布局若删去某专题唯一的问题节点，但仍保留该专题其他关联节点，探索详情提供该具体问题入口。图的四层业务结构、布局发布数据、正式连线和 Agent 执行逻辑未改。

前端相关 8 个测试文件共 95 项通过，TypeScript/Vite 生产构建通过，独立复核通过。线上事务核对 26 个入口和鉴权请求、24 个其他容器、Nginx 与业务进程均未改变；HR、FAE、VOC、行政路径响应与发布前一致。实际 `/ai-engineering` 加载 JS `index-Dv3rYXZn.js`，资源中含新的主问题文字。全景 SQLite 修订号发布前后均为 0，发布版内容 SHA-256 均为 `c374e3056af36906b33d3e1900dda20969a7810810fcfcc96d7804ac27ff72d5`，草稿和上一版均为空。未进行管理员浏览器视觉与投屏验收，该项由用户完成。

首次事务在挂载校验处停下：检查脚本沿用“首次增加全景挂载”的比较方式，错误地把旧 API 已有的全景挂载算作额外挂载。事务自动回滚到 `587e7072`，校验旧镜像 healthy、重启数 0；修正校验后重新准备并发布成功。该失败未改动全景状态。成功回执在 `/opt/orbbec-agent-platform/private/panorama-6dce36c38de2c5fd63b5e0474e9fc679/receipt.json`，后续维护输入在同目录 `future-maintenance.json`。发布保留了前次 API 覆盖、现行 HR generation 与全景状态挂载，未来发布须继承五份 Compose 输入并再次比对运行配置。
