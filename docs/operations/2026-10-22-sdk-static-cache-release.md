# SDK 禅道静态缓存修复发布记录（2026-10-22）

## 原因与变更

- 源码提交：`175efdb0f06406c72577e41c65a01cbe06748089`。先前 `/sdk/` 代理对包括静态文件在内的所有响应添加 `Cache-Control: no-store`，覆盖了禅道原有的静态缓存意图。连续页面访问期间，同一组 ZUI CSS、JS、图标字体各被重复取回约 11 次；线上 `/sdk/js/` 请求中位耗时约 0.17 秒。禅道本机读取代表性 CSS 约 1 毫秒，SDK 准入数据库查询每次新建连接的中位耗时约 14.5 毫秒。
- 仅调整 agent 域名的 `/sdk/` 代理响应头：`/sdk/js/`、`/sdk/css/`、`/sdk/static/`、`/sdk/theme/` 使用 `Cache-Control: private, max-age=86400`；页面与接口保持 `no-store`。隐藏禅道原有缓存头，避免冲突。`auth_request`、Cookie 过滤、准入检查与公开 API 禁止规则保持原样。平台 API 和禅道容器均未重建。

## 验证与发布

- 配置回归测试 **12 passed**。候选从线上实际 agent vhost SHA-256 `236abb7ed381f5e4f33d2ffbeed3dba48daa88236a2051b9f0d5d83f2da7e732` 生成；差异仅为一段缓存 `map` 和 SDK 代理内三条响应头设置。
- 隔离 Nginx 容器语法检查通过。带 Mock 鉴权的 HTTP 验证：静态 CSS 的唯一 `Cache-Control` 为 `private, max-age=86400`，HTML 为 `no-store`；上游 `Expires` 不透出；匿名静态请求跳登录，公开 SDK API 返回 403。
- 线上变更前备份：`/opt/orbbec-agent-platform/staging/sdk-static-cache-20261022/agent-before.conf`。候选 SHA-256 为 `f7abaf5733d4a5a1f1048d573a1e9360f63db7652807ddca3f06016f95dae486`。生产只执行实际完整配置的默认 `nginx -t` 并重载，没有执行独立配置的 `nginx -t -c`。
- 上线后 `/sdk/` 匿名请求 302 至平台登录，域名和旧 IP 的 API 禁止路径均为 403；平台 API 和禅道数据库健康，Nginx 活跃。代理临时目录仍为 `www-data:www-data`、`0700`，检查的错误日志没有权限错误。
- 行政服务 HTML/JS/CSS 与上线前 SHA-256 一致：HTML `1c36dd2768959dd26f0195e38ebbe992030f063d17cbc50a4f5dd46c87c6c378`；JS `02fd67f8306bfcd74dc2f2c5045524802761cc1cc014e7197475a7b4046b88a6`；CSS `6bae263f9322baef52cafe0e50993810b9eed2ae227226a95afdba1a485323a6`。

## 未完成与回退

- 已验证配置行为及线上非登录路径；员工已登录浏览器的连续页面切换速度仍需业务方复测。当前没有真实员工浏览器会话，不能把体感改善写成已实测通过。
- 若出现问题，将备份 `agent-before.conf` 恢复到 `/etc/nginx/sites-available/agent-domain.conf`；先核对 `www-data` 运行用户和代理临时目录属主，执行默认 `nginx -t` 后重载，并复测行政服务完整静态哈希与 SDK 入口。无需回滚数据库或重建容器。
