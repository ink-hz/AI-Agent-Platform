# HR 旧执行关联补充盘点

只读观察：2026-09-11T11:51:55.240078+00:00。生产 release `fe10fae1969b4c42f8c05d48c8eaf507a787b2b5`，镜像 `sha256:1aaa9fb03529df5ed342c0a8fc53bb435c10ba8c7ff130deb6cc2a58181986a8`。实际 app 身份和只读事务均核实；无数据库写入、claim、角色修改、业务正文或任务身份导出。34 个固定查询中 13 项缺失、4 项无权、0 查询错误，缺失/无权不转为零。

| job_kind/status | 数量 | 关联与处置依据 |
| --- | ---: | --- |
| worker_direct_v5 / queued | 3 | 有 direct binding、mission run、Turn 引用；所关联 Turn/Attempt 无非终态，无待确认 stop。v5 正常完成更新 Attempt/Turn，命令记录仍可 queued。不要取消或改写该记录来凑排空零。 |
| metabot_local / interrupted | 15 | 有 terminal_at，无 Turn 引用、无待确认 stop；保留历史技术终态。 |
| legacy_brain / interrupted | 3 | 有 terminal_at，无 Turn 引用、无待确认 stop；保留历史技术终态。 |
| direct_agent / interrupted | 10 | 有 mission run/Turn 引用，关联 Turn/Attempt 均终结，有 terminal_at、无待确认 stop；保留历史技术终态。 |

另外 completed 13、cancelled 2，合计 46 条 HR job。没有把 interrupted 当成功，也没有从记录终态推导本机/内网进程已停止。本次聚合不导出 job UUID，也没有执行维护身份的 103 精确排空函数；正式窗口的精确 lineage、排空及旧 Worker 停止必须另验。

`production_readonly_references.py` 保存当次实际执行程序（内嵌固定 inventory 源码），JSON 保存 capture_program_sha256。源程序后续修订不得冒充当次使用的源码。`production-job-references.json` SHA-256：`94ad39fa399cb919c1343ff130d927b980a1e3fd0421a5b909ff671cb6040c15`。

本地测试命令（工作树根）：

```bash
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_inventory.py backend/tests/test_hr_execution_inventory.py -q
```

`red-valid-fixture.log` 是固定有效夹具的缺查询 RED；`green.log` 为 13 passed。初始夹具错误也保留，不能当作实现失败证明。
