# SDK 禅道静态缓存修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 避免禅道页面切换时重复下载相同的 JS、CSS、字体和图片，同时保持实时鉴权。

**Architecture:** Nginx 依据 `/sdk/` 下的资源路径选择私有静态缓存或动态无缓存；每次网络请求仍执行 `auth_request`。先在隔离容器验证，再按线上实际配置生成并发布候选。

**Tech Stack:** Nginx、pytest、Docker。

## Global Constraints

- 只改 `/sdk/` 代理缓存头；HTML、接口和登录响应保持 `no-store`。
- 静态目录仅 `/sdk/js/`、`/sdk/css/`、`/sdk/static/`、`/sdk/theme/`，缓存 `private, max-age=86400`。
- 生产共享 Nginx 禁止独立配置 `nginx -t -c`；默认 `nginx -t` 前检查运行用户与临时目录属主。
- 行政服务和其他路由的配置与静态内容不得变化。

---

### Task 1: 缓存策略配置与回归

**Files:** `backend/tests/test_sdk_direct_zentao_proxy.py`、`deploy/cloud/agent-domain.nginx.conf`。

**Interfaces:** HTTP 级 `map $uri $sdk_zentao_cache_control` 为静态目录给出 `private, max-age=86400`，其他路径给出 `no-store`。`location ^~ /sdk/` 隐藏上游 `Cache-Control` 和 `Expires`，添加唯一策略头，并保留 `auth_request /_sdk_access`。

- [ ] 在现有 `test_sdk_entry_checks_platform_identity_and_uses_scoped_zen_proxy` 中加入断言：SDK 代理有 `proxy_hide_header Cache-Control;`、`proxy_hide_header Expires;`、`add_header Cache-Control $sdk_zentao_cache_control always;`、`auth_request /_sdk_access;`；新增测试断言 `map` 的默认/静态值。运行 `PYTHONPATH=backend /private/tmp/sdk-zen-sso-venv/bin/python -m pytest backend/tests/test_sdk_direct_zentao_proxy.py -q -p no:warnings`，先看到新增断言失败。
- [ ] 在 `deploy/cloud/agent-domain.nginx.conf` 中实现上述 `map` 与 SDK 代理响应头；其他站点块不改。重跑同一测试，要求通过。
- [ ] 运行 `backend/tests/test_sdk_direct_zentao_proxy.py`、`backend/tests/test_sdk_cloud_deployment.py` 和 `git diff --check`；提交配置与测试。

### Task 2: 隔离与线上验证

**Files:** `docs/operations/2026-10-22-sdk-static-cache-release.md`。

- [ ] 读取线上实际 agent vhost，断言其 SDK 块等于发布前基线；只向该块加入缓存策略。隔离 Nginx 容器使用独立 PID、日志、临时目录与端口测试语法和带 Mock 鉴权的 HTTP 响应：静态资源只出现 `private, max-age=86400`，页面 `no-store`，匿名仍跳登录，API 仍 403。
- [ ] 保存线上配置备份，核对当前 SHA、`www-data` 工作进程和临时目录属主；安装候选后只执行默认 `nginx -t`，验证通过再 reload。若任一步失败立即恢复备份。
- [ ] 核对 `/sdk/`、旧 IP 拒绝路径、行政 HTML/JS/CSS SHA、代理临时目录属主、权限错误日志和容器健康。将测量与未完成的员工浏览器验收写进发布记录并提交主线。
