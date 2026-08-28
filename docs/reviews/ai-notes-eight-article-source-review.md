# AI 工程笔记八篇迁移：源稿研究台账

隔离 worktree：`/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/ai-notes-eight-article-batch`

本台账仅记录迁移前的源稿清单与研究状态。源稿位于只读目录
`/Users/neo/Developer/personal/starship-blog-source/src/content/blog/`；行数与
SHA-256 已于 2026-08-28 使用 `wc -l` 和 `shasum -a 256` 复核。

| 源文件（绝对路径） | 行数 | SHA-256 | 目标文章 | 完整阅读 | 保留 | 删除 | 事实核验 | 去重 |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/身份认证与访问控制-深度理论知识.md` | 2625 | `9df1b094bf6064974314e93985e65332803c500061a495f8fffc9f0408662ea5` | agent-identity-access-control | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/身份认证与访问控制-理论架构设计.md` | 2140 | `c693c2e5ca30a47019191e001f0a8708beaed585dc03e7cdf67413e253efac01` | agent-identity-access-control | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构深度指南.md` | 2483 | `f21f1316a7c66c5a5c920efe8c0f352dc2532af15d82265d35b58dfbab7dc784` | llm-inference-serving-engineering；辅助 llm-agent-observability | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构理论指南.md` | 1550 | `c897f77ba9511c48358164c2c50b8f144703c983c0aa1dcedde9b20a6b145d8f` | llm-inference-serving-engineering；辅助 llm-agent-observability | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/ai-cloud-native-opportunity.md` | 124 | `9c04bf78390c08bbe3c89e685bf4f407434c6f96c0ec63828662db75239c0a07` | ai-cloud-native-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Kubernetes与容器编排深度指南.md` | 250 | `4908263dc8ebdbe69106f50b8aa8f2b5030dd0a7e9fc5827945784e02dd31df4` | 辅助 ai-cloud-native-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Kubernetes与容器编排理论指南.md` | 1705 | `12361a569b863b0de58f43366420c822f5b8568b42f243c38aab16aa0eef53ff` | 辅助 ai-cloud-native-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/可观测性与监控-深度理论知识.md` | 1857 | `546ea435a32227f0ec75e73e946e45e4cb5a6e092ee55deaf7d456de3c43b8ed` | llm-agent-observability | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Hermes-Agent架构分析与思考.md` | 390 | `aac8a4c575a11ae1a129c3e03d49e6217d2c02a92363b0689d5c5306c70227ad` | open-source-agent-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Clawdbot架构理论指南.md` | 284 | `838ae6a89bfeca305bb70bc016f975d7b12f34abef9fe73dd4638c22dedb6961` | open-source-agent-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/MetaBot架构设计理论分析.md` | 515 | `f526d770501328c0aa12bb926ae379c640bebb3cd9540fe4b569a581caebded5` | metabot-agent-control-bus | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/主流Agent框架深度分析-从架构本质到生产可用性.md` | 323 | `4edd175b19b9ac82be0ac9a92ed10d69ddfe14cac7611fa7b3df9f0a5866054b` | agent-framework-selection | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/干掉用户旅程-意图驱动的业务平台架构设计.md` | 379 | `3a165ec7de6b712d9cbbc999ee6d7752b9954691f904f41a80d289bc0585d52b` | intent-driven-ai-business-platform | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |

## 精读证据格式

后续精读时，每条保留的事实或观点按以下格式记录，不将来源中的品牌、宣传或未经核验的主张直接迁入草稿：

```text
- 目标文章：<slug>
  源文件：<绝对路径>
  定位：<标题 / 行范围>
  事实或观点：<简要转述>
  保留决策：<保留 / 删除>
  事实核验：<待核验 / 已核验，依据>
  去重关系：<与何处重合，处理方式>
```
