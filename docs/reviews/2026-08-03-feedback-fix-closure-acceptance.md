# 反馈修复闭环生产验收记录

**日期：** 2026-08-03  
**复审者：** Codex  
**Platform runtime code：** `a524499f5560fb7698da6fb1a4e35743f01b78ae`  
**FAE production release：** `ai-fae-agent-20260803032258`  
**FAE production SHA：** `8f817d66ea6d47cc33669b0daaf1b57f07f4b221`

## 结论

反馈修复闭环已上线并完成一条真实问题簇的端到端验收。Gemini 330 主动/被动双目问题的
3 个负反馈回答已合并到同一 canonical issue，合并证据、生产部署祖先关系、逐题 dev
真实复跑、运行时身份门和 Codex 独立语义复审全部通过。事项由系统自动进入 `closed`，
没有手工 close/force-close 入口。

- issue：`9900d929-7c64-4ee7-a2b4-f8f88d4bfb10`
- 最终状态：`closed`
- 缺失硬门：0
- 真实复跑：3/3
- Codex 语义复审：3/3 passed
- 自动闭环事件：`b1c675ec-1d05-465f-81a9-177933d7aedd`

## 当前飞轮计数

- 反馈总行数：82
- 负反馈行数：53
- 负反馈回答数：52
- 正反馈行数：29
- issue：52
- 状态：49 `pending_triage`、2 `duplicate`、1 `closed`

所有 52 个负反馈回答均已纳管；重复事项不计作已闭环。

## 工程证据

- 修复提交：`bf38f6534456421d3268323c6cee6ba527df753e`
- merge evidence：`300fba4b-4eb3-4123-a76c-393296c58dc4`，机器验证 `verified`
- deployment evidence：`2e1b80dc-4d6c-406c-8a16-d3465b8d953e`，机器验证
  `contains_merge=true`
- production deployment SHA：`8f817d66ea6d47cc33669b0daaf1b57f07f4b221`
- dev replay 的实际 build/version/model 与上述生产产物完全一致

## 逐题真实复跑结果

### Replay 1

- replay：`0b20031e-f592-4274-8148-9d23fd1b208f`
- trace：`afbf8da2d7a4d385b98e905e192001aa`
- duration：22,822 ms
- runtime gate：passed
- model：configured `claude-opus-4-8` / provider echo `claude-opus-4-8`

最新答案：

> **结论**
>
> 不是“结构光”。Gemini 330 系列的技术路线是**双目立体视觉（Stereo Vision）**，
> 官方系列口径明确为**主动 + 被动双目立体视觉（active and passive stereo vision）**。
>
> 准确表述应为：330 系列是“主动 + 被动双目立体视觉”相机，而非“主动/被动双目
> 结构光”。二者是不同的深度技术路线——它靠左右双目图像的视差匹配求深度，主动
> 模式是通过红外散斑/图案投射器为双目匹配提供纹理增强，投射器的存在并不把它变成
> 结构光路线。
>
> **依据**
>
> 系列级硬事实核验：`tech_route = Stereo Vision`；`stereo_mode_support = Active &
> passive stereo vision`，覆盖 335 / 335L / 335Le / 335Lg / 336 / 336L。
>
> **注意**
>
> “主动双目”指用投射器打散斑增强双目匹配的纹理，深度仍由双目视差计算得出，与
> “结构光”依赖已知投射编码求解深度的原理不同，请勿混称为结构光。

### Replay 2

- replay：`e0ccfa15-9cf6-48af-b56d-aaf8104c4fbd`
- trace：`cdc36a3880ba70fd9b43050d024cb6f5`
- duration：63,584 ms
- runtime gate：passed
- model：configured `claude-opus-4-8` / provider echo `claude-opus-4-8`

最新答案：

> **结论**
>
> Gemini 330 系列是**主动 + 被动双目立体视觉**，**不是结构光**。被动模式使用环境
> 纹理，主动模式由 850nm VCSEL 红外投射器补充散斑/纹理；两种模式的深度都由左右目
> 视差求解。设备带投射器不等于走结构光路线。
>
> 系列级 `stereo_mode_support` 与六个成员的 `tech_route` 共同支持该结论；来源措辞
> 详略不同不代表成员能力不同。

### Replay 3

- replay：`428eab8a-cbd4-4ee7-9185-cca61f6ff04e`
- trace：`29749bcec09438a13fd8af856a18890a`
- duration：27,597 ms
- runtime gate：passed
- model：configured `claude-opus-4-8` / provider echo `claude-opus-4-8`

最新答案：

> **结论**
>
> “主动加被动双目结构光”这个说法混淆了术语。正确说法是 Gemini 330 系列为
> **主动 + 被动双目立体视觉**；它通过双目视差计算深度，主动模式的红外图案/散斑
> 只用于增强匹配纹理，不是 Structured Light。
>
> 系列成员 335、335L、335Le、335Lg、336、336L 均由受治理的系列事实覆盖。

## Codex 独立语义判断

3 条均为 `passed`。理由：核心结论一致且正确；明确区分 active/passive stereo 与
structured light；系列作用域和六个成员完整；证据来自经 FAE 核验的官方系列声明、
型号事实与 direct-evidence sentinel；没有把投射器等同为结构光，也没有生成新的型号
能力外推。

## 失败反例与根因修复

首次验收保留了 6 次 `blocked / unsafe_replay_target` 记录（例如
`a94e6dfd-55a2-4c51-ba7d-ef9ebb3385e1`）。这些尝试没有 answer/trace，也没有进入
语义复审或 closed。脱敏诊断最终定位为 dev health 经系统代理返回 502；replay 专用
HTTP client 已设置 `trust_env=False`，确保 dev 目标和 Authorization 不经过环境代理。

本轮真实验收还发现并修复：

- stale `running` replay 的可见失败与恢复；
- inbox 关联已有 issue、错误 link 移动；
- deployment 环境/时间和 SQL fallback 硬门过宽；
- 语义复审参数名与内部 `_run(method=...)` 冲突导致 500；
- duplicate 合并后 backfill 试图复活原 primary link；
- Keychain 子进程无超时导致服务/测试永久等待。

## 同步、权限和冒烟

- 定时同步 LaunchAgent 第 17 次执行：exit 0
- FAE sync run：`cd674f55-96aa-4393-9010-afa3013631cc`
- Admin sync run：`8720dab9-4ac8-4486-b9cf-4341e30af4ae`
- backfill：created issues/links/events 均为 0，53 行 / 52 回答，幂等通过
- Review writer 修改 source schema：denied
- Review writer 删除 event：denied
- Sync writer 修改 review schema：denied
- Analyst 读取 review：52 issues
- `/api/health`、`/review`、Review API、Sessions API：HTTP 200
- 平台重启后 issue 仍为 `closed`，三条完整答案和唯一 `issue_closed` 事件仍存在
- FAE production 仅执行 health smoke，没有在 production 运行 eval/replay

## 自动化回归

- Platform backend：360 passed
- Platform frontend：154 passed
- Platform frontend production build：passed
- FAE prerequisite backend：1866 passed, 3 deselected
- FAE frontend：128 passed

## 运维说明

数据库角色密码已轮换，三种最小权限 DSN 与 FAE dev replay token 后续统一迁移到
`~/Library/Application Support/OrbbecAI-Agent-Platform/secrets/` 下的当前用户私有
文件。凭据值不进入仓库、plist、飞轮、日志或页面；文件缺失或权限不合格时相关能力
fail-closed，不会使用 owner DSN 或静默降级。迁移设计与验收标准见
`docs/superpowers/specs/2026-08-03-local-secret-files-design.md`。
