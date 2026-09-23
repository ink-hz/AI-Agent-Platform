# Mermaid 放大样式修正

2026-09-23 已上线。链路核查：Mermaid strict 渲染 → DOMPurify 安全清理 → SVG data URL → img，大图复用相同 SVG，未转为 PNG。

用户反馈放大模糊。共用大图样式对图片设置了 `will-change: transform`，同时用 transform scale 放大；[Chrome 官方说明](https://developer.chrome.com/blog/re-rastering-composite)指出该提示可保留旧倍率的栅格缓存。本次仅移除此提示，保留缩放、拖动、键盘、关闭和安全隔离；不引入新的渲染器或重新生成图表。此项是代码与官方资料支持的原因判断，未在用户浏览器复现或测量清晰度，最终视觉效果待用户验收。

- 应用提交：`6ebecdd2abe219eb9594f67df50b4ea778f24805`；前一应用：`4e2e1b9dce12a6e7ea36833ccda4bbbfb53c6d5a`。
- 镜像：`sha256:e82a03dc49e94d677a452bee74bc058b21e991e1df9689e417148e8ed7873b62`；API 容器：`f2fd9ff28bd54e13a132e5b2b7ad51addba847e54361e20e7a80bbe3ac4dd7f3`。
- 源码树：`3579070066d6594c45fdabb85ad1fa115ba34a0a`；归档 SHA-256：`8c7aba72c8df7dcf12c7d841893cfe681097e9b4594da3c7f7ef8e2249ea1c91`；清单 SHA-256：`924341e412971a07959d92d4b59c9132105435d3c58aa7e5d3cdb6c916657390`。
- 后续维护：`/opt/orbbec-agent-platform/private/panorama-2db0b81f27014bcf8753044257be5017/future-maintenance.json`；沿用 29 份输入，仅追加 API 镜像/版本覆盖，共 30 份。

15 项现有图表/大图测试通过，涵盖所有 HR 设计及已发布工程笔记 Mermaid 的实际渲染、SVG 安全清理、缩放、拖动与关闭；生产构建与 15 项发布事务测试通过。自审确认仅改变图表 CSS，不改变源文档、API 或权限。

线上 34 项检查通过，提供的图表样式已不含 will-change，资源哈希与镜像一致；API healthy、重启 0、锁释放，24 个其他容器、Nginx、systemd 与挂载未改变。接口和静态资源检查不等同于浏览器清晰度验收。

私有证据：`/opt/orbbec-agent-platform/private/panorama-2db0b81f27014bcf8753044257be5017`；本地：`/tmp/mermaid-sharp-zoom-release`。本记录提交不改变应用版本。
