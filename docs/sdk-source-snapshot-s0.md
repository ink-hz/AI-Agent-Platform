# B07-S0 离线源码提交输入清单

`app.sdk_workbench.source_snapshot.freeze_source_snapshot(observation: dict) -> dict`
只消费调用者已经解码的观测，不执行 Git、文件读写、网络、应用初始化或服务调用。
模块仅用 Python 标准库，不依赖既有 R0-a/R0-b 包、EvidenceService 或数据库。
本包是 B-04/B-07 的有限源码输入子包，不完成 B-07 父任务。

输入采用 `b07-s0-observation-1`：带时区的 `observed_at`、固定 `scope`、
`collection` 声明、`positions` 和 `gitlinks`。各对象只接受准确 schema 字段；
仅原生 dict/list/str、指定 null/bool 合法，SHA 必须为 40 位小写十六进制。
位置最多 18 条，锁最多 16 条，每个 labels/未跟踪路径列表最多 1000 条。
当前只接受 `identity_state=pending_registration` 和 `native_project_id=null`。
collection 的无网络、未读未跟踪内容、非原子采集及仅元数据对象检查是调用者声明，
核心不证明采集行为。

固定范围为以下九个 checkout 位置、八个逻辑仓；两个 SDK 位置的提交可以不同，
只要分别符合父提交的 gitlink。总仓标签不传播为组件版本。

| path | repo_ref | 父位置 / 父内 entry_path |
| --- | --- | --- |
| `.` | `OpenOrbbecSDKGroup` | 无 |
| `FrameProcessor` | `FrameProcessor` | `.` / `FrameProcessor` |
| `OBEnhancedDepthFilter` | `OBEnhancedDepthFilter` | `.` / `OBEnhancedDepthFilter` |
| `OBFilterLib` | `OBFilterLib` | `.` / `OBFilterLib` |
| `OBSDKPrivFilterLib` | `OBSDKPrivFilterLib` | `.` / `OBSDKPrivFilterLib` |
| `OpenOrbbecSDK` | `OpenOrbbecSDK` | `.` / `OpenOrbbecSDK` |
| `OpenOrbbecSDKExt` | `OpenOrbbecSDKExt` | `.` / `OpenOrbbecSDKExt` |
| `OpenOrbbecViewer` | `OpenOrbbecViewer` | `.` / `OpenOrbbecViewer` |
| `OpenOrbbecViewer/third_party/OpenOrbbecSDK` | `OpenOrbbecSDK` | `OpenOrbbecViewer` / `third_party/OpenOrbbecSDK` |

显式观测文件由主线提供，测试只读取 `SDK_SOURCE_OBSERVATION` 指定的单个文件，
没有默认路径、目录扫描或 skip。第四批输入路径为
`/Users/neo/Developer/work/AI-SDK-Platform/artifacts/sdk-platform/platform/wave-04/source-observation.json`，
文件 SHA-256 为 `761abd73b1a0bbdbe20a4e3ddf3267dbc10747157ee46c22dea388336f153444`。
该文件不复制入应用仓。

调用和独立复算示例（在 backend 的 Python 导入路径下执行）：

```python
import hashlib
import json
import os
from pathlib import Path
from app.sdk_workbench.source_snapshot import freeze_source_snapshot

explicit_path = os.environ["SDK_SOURCE_OBSERVATION"]
observation = json.loads(Path(explicit_path).read_text(encoding="utf-8"))
report = freeze_source_snapshot(observation)
print(json.dumps(report, ensure_ascii=False, indent=2))
if report["source_content"] is not None:
    canonical_bytes = json.dumps(
        report["source_content"], sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")
    assert report["source_snapshot_id"] == "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
```

这里的 `Path.read_text` 是调用者 I/O，纯核心没有 I/O。核心不实现 JSON decoder 或 CLI。
摘要内容仅含 schema、scope、排序后的九条 path/repo_ref/commit_sha 和八条完整锁记录。
时间、输入列表顺序、二次 HEAD、labels、origin、未跟踪路径和 gaps 不进入摘要。
origin 只保留为原始别名，不用于确认真实 GitLab 身份。

shape 错误抛 `SourceSnapshotError`，`code=observation_schema`，`path` 使用
`$.positions[1].observed_sha` 这类 JSON 字段路径，首错即停。
语义问题返回 `b07-s0-report-1`，`source_state=quarantined`、content/ID 为 null。
issues 按 `(path, code)` 排序并去重；语义 path 是固定位置或原始列表索引。
重复位置/锁即使内容相同也隔离，不选取覆盖，不继续依赖重复组比较。
缺位、未知位置/边、错仓引用、未取得 HEAD/状态、HEAD 漂移、tracked dirty、
锁缺失/类型/父 SHA/子 pin 不一致均阻断冻结。

`missing` 的两个 SHA 和 tracked 状态必须全 null；`unavailable` 可保留取得的部分
SHA/状态，但始终隔离。`observed` 的空 tracked 字符串表示声明范围 clean，
非空表示 dirty，null 表示状态未知。未跟踪路径仅保存名称，不 stat/open 内容，
不会单独导致 tracked dirty。真实输入中的总仓 `['docs/']` 保留原样，不清理或读取该目录。
返回 raw 为深拷贝，保留未知、重复和不可用记录；输入不变，嵌套可变对象不共享。

无 issues 才返回 `source_state=frozen` 和 `sha256:<64hex>`。该身份仅代表有限的
提交元数据输入清单，不验证 tree/blob 字节或完整源码闭包，也不证明可重建性。
报告始终保留以下 gaps：

- `native_gitlab_project_ids_unverified`
- `source_tree_blob_bytes_not_independently_verified`
- `other_gitlinks_and_third_party_sources_not_frozen`
- `untracked_and_ignored_inputs_not_frozen`
- `copied_binary_dependencies_unknown`
- `effective_oem_and_build_parameters_unknown`
- `toolchain_and_environment_unknown`
- `ci_run_attempt_and_artifacts_not_collected`
- `business_acceptance_and_release_approval_not_collected`

`identity_verification` 恒为 `caller_supplied_repo_refs_not_native_gitlab_verified`。
`build_ready`、`promotion_eligible` 恒为 false，没有 candidate_id 或批准接口。
冻结不代表内部合入、构建、测试、业务验收、制品交付或公开发布已完成。
本包不运行 SDK 构建、采集 CI、接入禅道/SSO、持久化或部署。

定向验证命令在隔离树 backend 下执行，真实输入缺失即失败：

```bash
SDK_SOURCE_OBSERVATION=/Users/neo/Developer/work/AI-SDK-Platform/artifacts/sdk-platform/platform/wave-04/source-observation.json PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -B -m pytest -c /dev/null -o pythonpath=. -q -p no:cacheprovider tests/test_sdk_source_snapshot.py
```
