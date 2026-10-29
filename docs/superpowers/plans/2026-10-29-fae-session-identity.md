# FAE 会话身份链路修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在平台会话列表、详情中显示 FAE 已验证提问人的姓名和内部/外部身份，历史无归属记录保持未知。

**Architecture:** FAE authoritative owner columns → office importer typed verified_subject → explicitly enabled signed replica export → encrypted cloud replica → authorized control-plane subject projection → session API → shared frontend identity formatter. Names resolve on the server; names, provider IDs, phone and email are not added to replica exports.

**Tech Stack:** Python/FastAPI, PostgreSQL, signed JSONL/encrypted replica, React/TypeScript/Vitest.

## Global Constraints

- 修复 channel/context 身份追踪边界，不修改模型、提示词或 FAE 问答工作流。
- 不根据 IP、问题正文、昵称或 metadata 猜测历史归属；不扩大外部用户管理权限。
- 管理员与有 FAE observation grant 的观察者可以解析；普通成员、失效会话拒绝。
- 不做生产模型评测，不主动使用浏览器；生产验收使用授权接口与只读聚合。
- 保留主线和其他工作树的用户材料；发布记录准确的合并版本与线上版本。

## Task 1: 服务端可信身份解析

- [ ] 用现有 71 项相关基线验证开始状态。
- [ ] 新增失败测试，覆盖内部/外部/未知、伪造名字、类型不匹配、查询失败。
- [ ] 新增窄 SQL projection（active viewer session + owner/admin/FAE observation grant，最多 100 个 UUID），只返回可信显示名材料。
- [ ] 新增 `observability/identity.py`，批量解析已授权返回结果中的 FAE references，解密外部姓名，不暴露身份目录接口。
- [ ] 在 session list/detail API 接入，保持 turn 身份与 summary 一致；失败明确返回不可用。
- [ ] 验证真实 PostgreSQL 权限、身份、加密与接口边界。

## Task 2: 同步与展示

- [ ] 先写同步配置与前端失败测试。
- [ ] `push-replica.sh` 增加严格显式 opt-in，保留普通 export 默认不包含身份引用。
- [ ] 共享 FAE 身份格式化函数用于列表/详情，显示内部/外部、未知、姓名不可用，保持已有其他来源行为。
- [ ] 验证 source owner → signed/encrypted replica → authorized identity API → frontend component；构建前端。

## Task 3: 审查、主线、发布验收

- [ ] 独立代码审查，修正实质问题，核对生产差异。
- [ ] 合并主线，备份并发布准确提交，启用实际定时同步 opt-in。
- [ ] 用正常签名同步重发已验证归属的历史会话；不伪造 owner，不修改无归属记录。
- [ ] 只读核对云端身份引用/解析覆盖、匿名拒绝、服务与静态资源；记录测试/生产证据和页面验收边界。

## 已证实根因

2026-10-29：最近 7 天 FAE 源库 19 个 enterprise-owned、29 个 partner-owned、35 个无 owner legacy 会话。云端最新 20 个 FAE 会话均无 verified_subject/姓名。实际 LaunchAgent 使用主线 `push-replica.sh`，export 未传 `--include-verified-subject`；现有 frontend list 排除了 FAE，backend 未解析 subject profile。用户已批准上述链路方案并要求开始实施。
