# 运行中心页面样式修复上线

2026-11-02 实际发布 aff9cbd2b9d413ddee912c8773a0107c717cece3。本记录的文档提交不代表应用版本。

原 CSS 的线上状态、MIME、完整哈希和选择器正常；新页面仅有基础栅格，技术来源和业务状态默认全部展开。此次沿用平台视觉增加指标卡、健康标签、分区和输入趋势图。来源、运行详情、更多业务指标和每日表格可展开，全部原指标及口径保留。未知、零、部分覆盖分别用问号、圆点和斜纹表示；30 天图和窄屏可横向查看。不改变数据接口和授权，不增加依赖或内联 style。

验证：10 项页面、20 项 HTTP 契约、14 项轮询回归通过；TypeScript/Vite 构建通过，原有大 chunk 提示保留。新增案例有首错证据；两处夹具/测试入口纠正单独记录。独立源码复核最终 0 Critical / 0 Important / 0 Minor。未执行浏览器视觉验收，该项由用户承担。

只复制 66 项前端产物，从实际在线 e7d11023 镜像增量构建，原 71 层加一个 COPY 层。无 RUN、依赖安装和远端拉取，最小上下文仅有 Dockerfile/payload。526 项镜像文件的哈希、大小及 UID/GID 10001 实际读取通过。原完整 Compose 输入和受控 plan/apply 均通过，只重建 API；同步器和数据库不变，没有重载 Nginx/systemd。

实际 API：e3f1df08fce5fdeb6b7b5d0e35ae23214b7f5b49ab07d93d557c17d89d4da7c3。镜像：sha256:08485a2b6c7b14f3df6972b59d6f4475e348e268a0f7f6eaecb7e05f529eb59b。API 健康；其余 34 个容器身份、配置、PID、启动时间和重启数相同，同步器四源健康。

线上验收：523 个 HTTPS 公共资源完整哈希/大小通过，共 38,501,790 字节；CSS MIME 正确，登录文档引用 assets/index-C3LdDykh.css。3 项未登录看板请求均 401/no-store；观测挂载只读；API 两个日志流没有权限错误或 traceback；发布锁正常释放。真实已登录页面仍由用户验收，不以资源检查代替视觉验收。

私有记录：/opt/orbbec-agent-platform/private/runtime-dashboard-presentation-20261102 及本地对应 tmp 目录。14 份回执归档 225,280 字节，SHA256 9efee6e3fc365e333998fc48ca0123ad149d280cf5f27e0f8e7f1bb1be50beaf，下载后已核对。原镜像和回滚输入保留；其他仓库、会话和既有未跟踪材料未改动。
