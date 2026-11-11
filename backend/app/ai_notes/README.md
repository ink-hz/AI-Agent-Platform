# 工程知识库内容维护

`library/` 是线上 工程知识库的唯一内容源。应用只在启动时扫描该目录，生成只读白名单索引；个人博客、数据库和外部 CMS 都不是运行时依赖。

## 目录与排序

每个一级分类使用带数字前缀的目录，并包含一个 `_index.md`：

```text
library/
└── 01-ai-and-agents/
    ├── _index.md
    └── 01-agent-system-handbook.md
```

目录和文件的数字前缀只控制显示顺序，不进入界面标题或 URL。调整数字前缀不会改变文章链接。

分类 frontmatter 只能包含：

```yaml
---
title: AI 与 Agent
slug: ai-and-agents
---
```

分类正文必须为空。分类 slug 必须匹配 `[a-z0-9][a-z0-9-]{0,63}`，并在全部分类中唯一。

## 文章格式

文章 frontmatter 示例：

```yaml
---
title: Agent 工程学习地图：从模型循环到生产系统
slug: agent-engineering-learning-map
description: 用可运行实验和验收标准，串起 Agent 工程从最小闭环到生产系统的学习路径。
author: 苍渊
motto: 博观而约取，厚积而薄发。
publishedAt: 2026-08-27
updatedAt: 2026-08-27
tags:
  - Agent
  - 架构设计
draft: true
---
```

约束：

- `title`、`slug`、`description`、`author`、`publishedAt`、`draft` 必填；`motto`、`updatedAt`、`tags` 可选。
- `author` 不得为空白；`motto` 存在时不得为空白。作者和座右铭由阅读页作为结构化署名展示，不写进正文。
- 文章 slug 必须匹配 `[a-z0-9][a-z0-9-]{0,127}`，并在所属分类中唯一。
- `publishedAt` 不得早于 `2026-05-25`，不得晚于校验当天。
- `updatedAt` 不得早于 `publishedAt`。
- 未准备发布的文章必须使用 `draft: true`，不能使用未来日期占位。
- 草稿也必须通过结构校验，但不会出现在目录中，也不能通过深链接读取。
- 阅读时长由后端根据正文规模派生，不写入 frontmatter。
- Markdown 不支持原始 HTML；图表使用 fenced `mermaid` 代码块。

## 图示语言

系统全景图使用白底 `subgraph` 表达层级，普通流程图使用固定语义色。颜色只帮助识别角色，节点名称和连线标签仍需完整表达含义，不能把颜色作为唯一信息通道。

统一语义类如下：

```mermaid
classDef input fill:#DBEAFE,stroke:#60A5FA,color:#172033;
classDef model fill:#EDE9FE,stroke:#A78BFA,color:#172033;
classDef data fill:#CCFBF1,stroke:#5EEAD4,color:#172033;
classDef policy fill:#FEF3C7,stroke:#F59E0B,color:#172033;
classDef tool fill:#DCFCE7,stroke:#4ADE80,color:#172033;
classDef success fill:#D1FAE5,stroke:#10B981,color:#172033;
classDef risk fill:#FEE2E2,stroke:#F87171,color:#172033;
classDef infra fill:#F3F4F6,stroke:#9CA3AF,color:#172033;
```

架构边界、组件关系、数据流、状态机和多阶段流程优先使用 Mermaid；协议取值、公式、短命令和需要原样复制的内容保留文本或代码块。每张图前说明观察重点，图后解释关键路径或权衡。发布前必须用平台锁定的 Mermaid 版本真实渲染，桌面和手机宽度的浏览器视觉验收由用户负责，组件渲染不等于该项通过。

## 校验与预览

在仓库根目录执行：

```bash
cd backend
.venv/bin/python -m app.ai_notes.validate
```

校验会检查目录结构、符号链接、未知文件、frontmatter、slug、日期、危险链接和已发布文章中的旧组织标记。任何失败只输出固定错误，不回显正文、绝对路径或拒绝清单内容。

发布前还需运行后端测试、前端测试与生产构建，开发侧验证接口与组件、链接、标题锚点和真实 Mermaid 渲染；浏览器验收由用户按 AGENTS.md 负责，未执行时明确记录。

## 精编收录与来源

五类按序为 AI 与 Agent、SDK 系统架构、制造业务与流程、企业系统与集成、智能制造架构；每篇一个主分类，文档类型用标签表达。精度优先，保留完整手册、方案、架构及目录，必要上下文不机械去重。

本批29篇仅包含原14篇与确认的15篇新增文稿，来源及最小改动见 `docs/knowledge-library/2026-11-11-source-manifest.json`。原14篇正文、日期、slug、标签不变。源文件只读，新增副本先以 `draft: true` 整理、验证与审查，再输出 `draft: false` 发布候选。收录日期、原研究日期、目标设计身份与实施历史快照分开标记；未进行的真实系统、设备或业务验收不得声称完成。

文章互链使用规范 `/ai-notes/{分类}/{文章}`；未收录配套材料指向核实存在的源仓库固定提交。SDK未确认可公开远程的引用使用文内来源附录，保留相对文件身份、行号与八仓固定基线，不导入排障场景卡。`legacy_markers.yaml` 继续检查已识别旧品牌。

应用启动与校验器只扫描 `library/`；Git移除旧 `content/`。COPY-only镜像继承层可以保留旧文件，但不参与任何运行或测试发现。

## 分类迁移与旧链接

文章可以声明可选的 `legacyPaths` 列表，例如：

```yaml
legacyPaths:
  - foundations/agent-engineering-learning-map
```

每项为不带前导斜杠的 `分类slug/文章slug`，两段分别遵守上述分类和文章 slug 语法。别名不得重复，也不得与任何规范路径或其他文章的别名冲突；草稿也参与冲突校验。未知路径和草稿别名均返回 404。

加载时统一建立白名单别名索引，不按请求路径访问文件系统。旧链接返回同一篇文章及其规范分类/slug，API 字段保持不变；阅读器以 replace 导航切换到 `/ai-notes/{规范分类}/{文章slug}`。
