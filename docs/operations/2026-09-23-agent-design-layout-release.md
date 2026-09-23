# Agent 设计页面边距修正

2026-09-23 已上线。根因是文档页继承 `.page` 的 1240px 居中限宽，宽屏时导航栏与章节目录之间产生大段空白。为该路由设置独立页面布局，铺满导航右侧，桌面外边距 24px、窄屏 16px。目录与正文布局保留。

- 应用提交：`4e2e1b9dce12a6e7ea36833ccda4bbbfb53c6d5a`；前一应用：`4fadbdbb174db288ab7f1c1530c4a5d00413298d`。
- 镜像：`sha256:0db6d7f708e9d7d9f65527603f5f5374ffe26828fc0762b6087377d4128ebdb0`；API 容器：`402709df6e52b486f7a7bdd594f1569b7b131eb797ee32005ebd58f7d4f96fe4`。
- 源码树：`57aced5b491b83596676cbff5c6c23c4c61d5abb`；归档 SHA-256：`a87cc8e01a45a9210a2f23ce07ee448520f05a2e3cb1cc94702199ce14b5e88e`；清单 SHA-256：`f830f6d395c2f250066ec14d7c40eff0c5dcbb600bf9a36966f7282a83207ee5`。
- 后续维护：`/opt/orbbec-agent-platform/private/panorama-b5d51247d01346629db924887e2968fc/future-maintenance.json`；沿用 28 份输入，仅追加 API 镜像/版本覆盖，共 29 份。

29 项现有平台外壳/文档组件测试、生产构建、15 项发布事务测试通过。自审确认改动仅在该路由生效，未更改通用页面宽度、正文、API 或授权。线上 34 项检查通过，新布局 CSS/JS 已提供且哈希与镜像一致；API healthy、重启 0，锁释放，24 个其他容器、Nginx、systemd 与持久挂载未改变。未执行浏览器，实际布局由用户验收。

私有证据：`/opt/orbbec-agent-platform/private/panorama-b5d51247d01346629db924887e2968fc`；本地：`/tmp/agent-design-layout-release`。本记录提交不改变应用版本。
