# 平台公共布局统一发布

2026-09-27 已上线。平台内部采用同一内容起点和边距，紧凑标题及操作区；普通页面取消多层居中限宽。共用浅蓝灰目录表面与阅读区，图表、阅读、管理列表和对话保留各自用途与滚动方式。文档源标题层级、SVG 安全渲染与清晰缩放保持。独立跳转业务应用及旧内嵌 HR/FAE 路由排除。

公共实现：platformLayout.ts 分类路由，AppShell 登记 data-platform-layout，platformLayout.css 仅针对登记外框管理尺寸与样式，PlatformPageHeader 统一 11 个页面标题。桌面外边距 24px、窄屏 16px，二级目录 230px。权限内容的内层居中容器一并解除。共用 SessionsView 默认保留原 FAE 标题结构，只有平台会话页显式启用紧凑标题；页脚样式同样限定外框。

- 应用提交：`7a7564fcec215723a9b65adf3ef7eb448037f910`；前一应用：`a98e7da0f2b27d38fcd98759950dc4ecbf7057d3`。
- 镜像：`sha256:5946bc60c8cddaa41f3eddbdb527efda9c84dce812232aff61130a00435a3ab2`；API 容器：`f31f4d4759618f8f515881ecd07bba4b0e0d450d5e127f6182a2cee3eabb6d24`。
- 源码树：`e693817092abcdb6352a3e1eaab0c14d223415bf`；归档 SHA-256：`a9c26019d7661980b337eebf44064628ab5170ffb94f8d58603c1e8ce0de1fd5`；清单 SHA-256：`8dbc45b3e1923850fcaea05bde6ded4f7be0559c41f46463697ed5c3c3a516d4`。
- 后续维护：`/opt/orbbec-agent-platform/private/panorama-b47540b95cf64412921c8edbba01eabc/future-maintenance.json`；沿用 31 份 Compose 输入，仅追加 API 镜像/版本覆盖，共 32 份。

验证：路由分类用例先因缺失模块失败，完成后 19 个前端测试文件共 157 项通过。覆盖外框、权限分区、FAE 排除、会话、文档切换/锚点、HR/FAE 32 张 Mermaid 实际渲染、图表编辑、组织树与对话/阅读抽屉。生产构建、15 项发布事务测试通过。独立审查两项问题（权限内层居中、共用标题/页脚跨 FAE 边界）均修正，复核通过。

线上 35 项检查通过：公共外边距/目录变量、路由布局标识与紧凑标题组件已提供，静态资源哈希与镜像一致；受保护接口匿名访问及 no-store 正确，原组织/历史/管理员仓库的只读检查通过。HR/FAE 原文快照哈希未改变。API healthy、重启 0、锁释放，24 个其他容器、Nginx、systemd 与持久挂载未改变。

未改变后端、数据库、授权、模型或 Agent 执行。未使用浏览器；响应式真实布局、对齐、投屏、阅读和点击效果仍由用户验收，工程回归不代替视觉验收。

私有证据：`/opt/orbbec-agent-platform/private/panorama-b47540b95cf64412921c8edbba01eabc`；本地：`/tmp/platform-layout-release`。本记录提交不改变应用版本。
