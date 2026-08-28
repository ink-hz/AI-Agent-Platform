# AI 工程笔记内容维护

`content/` 是线上 AI 工程笔记的唯一内容源。应用只在启动时扫描该目录，生成只读白名单索引；个人博客、数据库和外部 CMS 都不是运行时依赖。

## 目录与排序

每个一级分类使用带数字前缀的目录，并包含一个 `_index.md`：

```text
content/
└── 01-foundations/
    ├── _index.md
    └── 01-agent-system-handbook.md
```

目录和文件的数字前缀只控制显示顺序，不进入界面标题或 URL。调整数字前缀不会改变文章链接。

分类 frontmatter 只能包含：

```yaml
---
title: 基础与原理
slug: foundations
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

系统全景图使用低饱和浅色 `subgraph` 表达层级，普通流程图使用固定语义色。颜色只帮助识别角色，节点名称和连线标签仍需完整表达含义，不能把颜色作为唯一信息通道。

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

架构边界、组件关系、数据流、状态机和多阶段流程优先使用 Mermaid；协议取值、公式、短命令和需要原样复制的内容保留文本或代码块。每张图前说明观察重点，图后解释关键路径或权衡。发布前必须用平台锁定的 Mermaid 版本真实渲染，并在桌面和手机宽度下检查；手机上缩小后无法辨认的图必须拆分。

## 校验与预览

在仓库根目录执行：

```bash
cd backend
.venv/bin/python -m app.ai_notes.validate
```

校验会检查目录结构、符号链接、未知文件、frontmatter、slug、日期、危险链接和已发布文章中的旧组织标记。任何失败只输出固定错误，不回显正文、绝对路径或拒绝清单内容。

发布前还需运行后端测试、前端测试与生产构建，并在认证后的本地 Shell 中检查长文、表格、代码、链接、标题锚点和 Mermaid。

## 旧文章逐篇迁移

旧文章迁移必须一次只处理一篇：

1. 从 `/Users/neo/Developer/personal/starship-blog-source` 复制候选稿到本仓库，先设置 `draft: true`；不修改个人博客仓库，也不建立运行时链接。
2. 清除或重写上一家公司的名称、域名、项目、人员、客户、内部链接、组织语境和旧日期。
3. 核对易变化的技术事实，优先使用官方文档、规范和研究论文等一手来源。
4. 更新标题、摘要、标签、分类、代码示例和 Mermaid。
5. 运行内容校验与完整平台测试，并本地预览。
6. 由内容负责人逐篇确认。
7. 将 `publishedAt` 设置为实际内部发布日期，更新 `draft: false`，再随平台发布。

首次迁移前必须把已识别的旧公司名称、域名、项目代号和其他禁止标记加入 `legacy_markers.yaml`。清单已经包含首批迁移确认的旧站和个人品牌标记；后续发现新标记时必须继续追加。
