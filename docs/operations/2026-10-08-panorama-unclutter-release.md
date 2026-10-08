# 全景主图撤下探索提问：发布记录

2026-10-08 用户否定全景图顶部的“AI 工程判断”横幅及业务节点、产品层上的提问贴片。已从归并并推送的主线 `552c4bf98cb2dc67fd6b36451735067bd57b4415` 发布平台 API 镜像 `sha256:441f76f26630df1e1fd445e0113773e5400b8c3ff8afb79cd307f1c6c5a992e6`；发布前线上为 `c7447fd71130e49673365d2c26f739e2af7ba1af`。源归档 SHA-256 为 `e8caf3452bac6ba335bbabe168487b284a20000745d6a4587aad23785e6a44a0`，逐文件清单已核对。

默认业务图不再显示任何探索提问或附加口号，节点恢复原高度。原四层布局、名称、业务关系、详情和动作保留；搜索只匹配业务内容。“AI 探索”仍须主动点击工具栏入口，详情内以领域选择框切换当前布局中可用的主题；关闭后恢复普通图。探索内容不写入发布版、草稿、正式连线或 Agent 执行规则。

相关前端 8 个测试文件共 92 项通过，TypeScript/Vite 生产构建通过，独立复核另跑相关测试 51 项并通过。线上 API healthy、重启数 0，`/opt/orbbec-agent-platform/current` 与容器版本均为 `552c4bf9`。事务核对 26 个页面与鉴权请求；其他 24 个容器、Nginx 与业务进程不变。实际 `/ai-engineering` 加载的 `index-Dco1Qxhf.js` 和 `index-B9Bz2d2t.css` 不含旧横幅或节点提问样式；JS 中保留主动入口“AI 探索”。全景 SQLite 修订号发布前后均为 0，发布版内容 SHA-256 均为 `c374e3056af36906b33d3e1900dda20969a7810810fcfcc96d7804ac27ff72d5`，草稿和上一版仍为空。管理员浏览器及小屏视觉由用户验收，未由开发侧操作。

首次预检后，有一个与平台服务无关的临时测试容器退出，致使全部容器快照比较失效；事务在触碰 API 前停止。重新取得稳定基线后的事务完成发布。成功回执为 `/opt/orbbec-agent-platform/private/panorama-9c3bae7d6d8cb0b9b41bd580a4feaa7e/receipt.json`，后续维护输入在同目录 `future-maintenance.json`；包含当前 HR generation、全景持久化挂载和连续 API 覆盖的六份 Compose 输入。
