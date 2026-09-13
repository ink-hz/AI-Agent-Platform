# S3 版本桶附件擦除修复报告

## 结论

生产只读证据确认附件桶版本状态为 `Enabled`，且本次只读请求返回 HTTP 200、`writes=false`。原 `S3ProcessingObjectStore.delete()` 和 `AttachmentObjectWriter.delete()` 都只调用不带 `VersionId` 的 `DeleteObject`；在该生产配置下，它们只写入 delete marker，历史对象版本仍然存在。维护擦除随后可把数据库任务记为 `completed` 并抹除全部对象引用，上传孤儿清理也可把尝试确认成 `cleaned`，两条路径都存在对象仍可按历史版本读取的假成功。

本地代码现已让维护擦除、衍生物确定性回滚和上传孤儿清理共用准确 key 的版本删除实现。没有执行生产写、生产迁移、模型调用或浏览器操作；封存的 ce6/4f08431 运维包未修改。

## 生产证据与基线

- 只读证据：`../s3-versioning-readonly-1/stdout.json`
- 证据内容：`status=Enabled`、`http_status=200`、`writes=false`
- 证据 SHA-256：见 `source-sha-after.json`
- 修改前仓库 HEAD：`ef459d579085eea28b98efc38257882aab49aab6`
- 修改前源文件与首次 RED 测试 SHA：`source-sha-before.json`
- 修改后源文件 SHA：`source-sha-after.json`
- 完整工作树补丁：`working-tree.patch`

## 实现

新增 `backend/app/attachments/s3_erasure.py`：

1. 先读取桶版本状态。响应缺失或状态不是未启用、`Enabled`、`Suspended` 时失败闭合。
2. 未启用版本控制时执行一次准确 key 的普通删除，保留旧桶兼容行为。
3. `Enabled` 或 `Suspended` 时，以准确 key 作为 `Prefix` 分页调用 `ListObjectVersions`，同时读取 `Versions` 和 `DeleteMarkers`，只选择返回项中 `Key == object_ref` 的版本。
4. 完整列举一轮后，逐个用准确 `VersionId` 删除普通版本和 delete marker。没有使用批量删除，避免 HTTP 200 中逐项错误被忽略。
5. 删除后从第一页重新完整列举。擦除期间到达一次或多次的新版本会被后续 sweep 捕获；最多执行八轮，持续写入或无法收敛时抛错，不能报告成功。
6. `IsTruncated`、下一页双游标、版本条目结构或游标推进不可信时抛错；权限、网络及客户端错误也全部向 adapter 传播，再清洗为原有公开异常。

两个 adapter 的公开异常契约保持不变：维护擦除把失败对象记入 `partial`，上传孤儿清理不会对删除失败的尝试调用 `acknowledge_orphaned_write`。

上传清理按所有权证据收窄：

- `put_object` 已成功返回且后续本地长度校验失败时，若响应给出 `VersionId`，只删除该次返回的版本；未启用版本控制且成功响应无 `VersionId` 时删除该次准确 key。
- `put_object` 自身抛错时，响应是否丢失属于不确定状态，没有可证明的版本身份，因此不再发可能只写 marker、也可能影响当前 key 的盲删。上传尝试仍由数据库标成 abandoned，并由后续孤儿清理按整 key 权限执行。
- `put_derivative` 自身抛错同样不做无版本身份的盲删；成功写入后遇到已判定为非歧义的数据库失败，现有 `worker.py` 会调用共用的整 key 删除。歧义提交仍保持不删并先 reconciliation。

## RED 与 GREEN

首次 RED 保存在 `red/`：

- 命令：`backend/.venv/bin/python -m pytest -q tests/test_attachment_s3_version_erasure.py`
- 结果：exit 1，13 failed、1 passed。
- 失败覆盖旧版本与 marker 残留、未查询版本状态、Get/List 错误被假成功、坏分页被接受、并发版本未清、erasure 错记成功、orphan 错误确认、上传异常盲删和成功写入未按返回版本精确清理。

修复后的原始日志保存在 `green/`：

- 新增定向测试：exit 0，16 passed，0.11 秒。
- 附件相关回归：exit 0，386 passed、15 warnings，43.23 秒。警告均为已有 Starlette/TestClient 弃用提示；stderr 为空。
- Ruff：exit 0，`All checks passed!`。

状态化 S3 替身明确模拟了 Enabled/Suspended 桶的裸删只新增 marker、跨页普通版本和 marker、同前缀兄弟 key、`VersionId="null"`、一次并发写、持续并发写，以及 Get/List/Delete 网络或权限失败。该替身是本地边界测试，不称为真实 MinIO、真实 S3 或生产验收。

## 尚未闭合的跨系统并发边界

多轮列举只能清除每次列举时已经可见的版本，无法阻止最后一次空列表之后才完成的外部 `put`。当前 064 的 `cancel_upload_v64` 会在 S3 写仍可能进行时把 `claimed` 尝试改为 `abandoned` 并清除 upload 上的当前尝试；`claim_attachment_erasure_job_v64` 不等待该写的 lease 或外部 I/O；`record_attachment_erasure_result_v64(completed)` 又会抹除 attempt 的对象引用。

因此仍存在如下时间窗：擦除列举为空并完成数据库抹除后，在途 `put` 才落盘；如果写进程在自己的后续清理前退出，数据库已经没有可恢复的 key。此次补丁没有把多轮空列表包装成永久并发保证。上线若要宣称强擦除，必须另行加入经过验证的数据库/写入完成栅栏，并证明旧 API、维护 worker 与迁移窗口共同遵守；该设计不在本最小版本 helper 中。

## 生产启用前置

实际附件凭据除原有对象写删权限外，必须对准确桶具备以下能力，并用非个人测试 key 或权限模拟做受控预检：

- 读取 bucket versioning 状态；
- 列出 bucket versions；
- 删除指定 object version/delete marker；
- 未启用版本控制兼容路径仍需普通对象删除。

任一权限缺失时新代码会保守失败；此行为可防止数据库继续把残留版本记为已擦除。生产切换、旧 worker 停止、迁移、受控 canary 和回退仍由本轮发布总流程执行，本报告没有改写或执行封存运维脚本。
