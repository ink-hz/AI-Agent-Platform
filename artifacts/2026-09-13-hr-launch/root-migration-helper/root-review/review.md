# Root 独立审读

逐行审读 host Supervisor 与 companion diff、两份新增真实PG测试及既有模拟边界变化。允许仅 production 的 root 模式；默认 hr/all 保留。root 只执行根目录，校验001–088与hr_web089–095完整既有账本，允许已存在100/102–105的同SHA重跑，不接受已进入hr_agent子迁移的阶段。具名容器、GRANT前风险回执、墙钟、四信号和stop/kill/会话及membership归零复用同监督器。after账本必须与所选根文件集合精确一致，源变化不报成功。

现有042 job-kind脚本不能直接接受091合法worker_direct_v5，新显式baseline95路径校验真实已验证CHECK和精确job/binding/attempt/turn关联。原2参路径仍保留；这不是104排空计数，不取消queued记录。源码中的HR限制与direct_admission现行仅hr-bot路径一致。

已读取最终原日志45 passed/90.75s，并核对冻结两份host source SHA。Root另通过生产只读SQL实际核对001–095每项SHA与当前源完全一致，执行同SHA companion --baseline95返回classified/exit0；证据在production/root-baseline-preflight。该只读运行没有迁移、授权、取消或模型调用。

未发现阻止此具体production迁移计划的源码问题。执行仍须先完成私有备份，持部署独占锁，先停旧附件并inspect，再root迁移，禁止恢复迁移前附件镜像。新helper须独立固定SHA放维护目录，与immutable ce6c0f3 runtime image分开记身份，不修改已staged release。HR opt-in随后显式 --environment production，preview无写入。SIGKILL/主机故障仍需外部回执核验，不能把进程内清理写成无条件保证。
