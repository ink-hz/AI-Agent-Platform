# C1/W8 独立审计与真实擦除修复（2026-09-11）

审计范围为 root 新增的个人材料出站授权、候选 routes/service/main/worker/resources 与五项本地 API/服务测试，以及099附件外键对真实擦除的影响。只使用本地一次性PostgreSQL、真实上传和虚构文本；模型提供方为 ScriptModel，未查看C3私密配置，未调用生产或真实模型。

## 先于修复固定的结论与文件计划

099外键没有阻止既有擦除SQL：`record_attachment_erasure_result_v64` 将附件置为deleted并清除加密对象定位等字段，保留附件身份行，不执行DELETE。独立诊断通过一次正确claim、真实对象删除及原record函数，证实已确认候选随来源失效而不可读，item不返回私人正文。

真实 `AttachmentErasureService.process_next` 仍失败，发现两处既有缺陷：

1. `backend/app/attachments/erasure.py:66` 使用 `SELECT (claim_attachment_erasure_job_v64(...)).*`，PostgreSQL展开复合列时重复调用volatile函数。实际返回的job_id非空而attachment_id为NULL；多个任务还可能被同一语句错误领取。
2. 同文件随后读取uploads和upload_write_attempts，而064只授予maintenance对attachments/derivatives/task_grants/erasure_jobs的SELECT。真实maintenance身份报 `permission denied for table uploads`。原有erasure单测使用内存Repository，未覆盖此权限与SQL调用行为。

最小修改文件：`backend/app/attachments/erasure.py` 改用 FROM 函数，只调用一次；新增公共 `backend/control_migrations/100_attachment_erasure_worker_access.sql`，仅向maintenance/preview授予uploads(attachment_id,write_attempt_id)及upload_write_attempts(attachment_id,attempt_id,object_ref_ciphertext,object_ref_key_version)的列级SELECT。擦除为平台公共附件服务，因此100放公共迁移目录，不放hr_agent；全局100事先检查未占用，不改064或099，不给普通app附件UPDATE权限。

新 `backend/tests/test_hr_agent_candidate_erasure.py` 保留真实上传→候选人工确认→真实申请擦除→maintenance worker→对象删除→候选/对象scope/列表不可用链路，以及多个任务一次claim只领取一个的回归。两个正式测试先运行失败，确认失败发生在真实maintenance权限边界后才改实现。

## Root 装配审计

root五项测试本地重跑5 passed（2.78s）。个人出站门先执行当前来源授权，再沿reference_edges寻找personal_materials，默认无callback拒绝，callback异常同样拒绝；新增临时真实持久测试仅选择派生research引用时也阻断，模型请求数0。ResourceReader的新候选分支通过read_candidate验证全部来源，且没有candidate对象授权递归。HTTP入口仍经身份、Origin/CSRF及HR权限；服务将身份owner传入，不从请求接受owner。

覆盖边界：五项测试中“foreign item”使用随机不存在ID，不是第二个已认证owner的真实existing item；retry路由、确认同键HTTP重放及真实独立worker进程故障尚未在该五项中覆盖。服务层相关用例不能替代这些HTTP/进程证据。后台个人处理callback抛出非HrAgentProblem异常时，intake.advance_one尚无本项错误隔离，worker会退出；真实处理回调仍未配置，因此不作为已开放生产能力。

## 结果

先运行两个正式用例，均因真实maintenance缺权失败；仅新增100后仍两项失败：单任务找不到attachment，双任务得到第一job_id与第二attachment_id的错配。改用FROM函数后，真实服务完成对象删除及状态提交，多任务一次claim只领取一项。

最终命令：`cd backend && .venv/bin/python -m pytest -q tests/test_hr_agent_candidate_erasure.py tests/test_attachment_erasure.py tests/test_hr_agent_candidate_routes.py tests/test_hr_agent_foundation.py`，47 passed（17.46s）。新文件三项验证真实maintenance全链、兄弟任务领取隔离及列级权限；原始对象存储为空，attachment/erasure状态为deleted/completed，candidate、派生research、解析text和candidate对象scope均不可读，候选列表仅保留ID与available=false。维护角色不能读取uploads.declared_mime、不能UPDATE附件；普通app附件UPDATE仍为false。新测试Ruff与本次diff检查通过。

100 SHA256：`15355874fce1ea58d00056eb07233a0fb3ef4a3cd7e807ea6e6608deb3668177`。100须按公共控制面迁移路径应用，现有HR就绪检查仅核096–099，不会自动检查100。未修改既有迁移、未授予普通app新权限。上述回归是本地API/服务/数据库/真实对象删除验证；模型仍是脚本边界，没有独立OS进程kill测试。真实资料、浏览器和生产验收仍关闭。
