# 恢复事件展示实施计划

> **For agentic workers:** 使用当前会话实施，各任务完成后独立审查。采用 `executing-plans` 的逐项执行与验证流程。

**Goal:** 已自动恢复的故障在事件历史中显示“已恢复”。

**Architecture:** 仅修改共享 `OperationalEventItem` 的展示推导，复用 `status=resolved`；原始事件严重级别和历史事实保持原样。

**Tech Stack:** React、TypeScript、Vitest、Vite。

## 约束

- 不把 active 或仅 historical 的 attention/critical 事件标为恢复。
- 不删除历史故障、恢复事件或后台告警规则。
- 不增加依赖或浏览器自动化验收。

## 任务

- [x] 检查源事件、云端状态及共享组件，确认根因。
- [x] 运行既有 `src/operationsUi.test.tsx`、`src/operations.test.ts`：30 项通过。
- [x] 在共享组件定义唯一的展示推导并用于标题徽标和容器样式：

```typescript
function eventTone(event: OperationalEvent): EventTone {
  return event.event_family === "recovery" || event.status === "resolved"
    ? "recovery"
    : event.severity;
}
```

- [x] 运行同一组既有组件测试和 `npm run build`：30 项通过，构建通过；低风险展示修复不新增重复实现的测试。
- [x] 独立审查、归并主线；从实际 `104f9816` 运行基底发布 `1f1e920f`，349/349 项资源、健康及身份保护校验通过，详见本批操作记录。
