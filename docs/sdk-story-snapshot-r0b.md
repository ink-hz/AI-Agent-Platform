# R0-b 只读故事分页证据核心

本模块只收集调用身份可见范围内的已报故事页，校验分页和控制数。当前实例、版本、API与只读身份尚未校准；合成测试通过不代表接入或业务验收。

公开接口是 `SnapshotScope(instance_id, declared_zentao_version, product_id, actor_ref, visibility_ref)` 和 `await collect_story_snapshot(reader, actor, scope=scope)`，位于 `app.sdk_workbench.story_snapshot`。reader由调用方管理生命周期，具有native request形状；actor原样传递。scope字符串是调用方声明的无凭据受控引用，不能为空；不能据此称本模块核验了版本或权限。

核心每页仅调用一次固定GET `products/{product_id}/stories`，参数为browseType=allstory、recPerPage=50、pageID从1递增、api_version=2。最多1000故事/20页。它不实例化HTTP/SSO、读配置/凭据、写文件/数据库、认证、重试、建号或调用CI；不提供CLI、路由或UI。

报告schema为r0b-story-snapshot-1。scope和scope_verification保留声明边界；started_at/finished_at及每页requested_at/received_at为本地UTC观测时间。pages保存原生返回值的深拷贝和逐页校验结果，不是原HTTP字节证据。stories仅包含整页通过校验的原生对象，保持所有原生字段和顺序。坏页仍完整保存在pages.raw_response。

outcome为complete、partial或failed。complete只表示此次身份所见的已报页数与recTotal核对通过；partial表示已有有效整页后停止；failed表示第一张有效页之前停止。last_valid_page初始0，只在整页通过后推进；stopped_page记录失败页。没有可恢复游标、自动续采或删除推断。空集只接受第一页stories=[]、recTotal=0且pageTotal=0或1。

pageTotal/recTotal漂移、坏结构/条目、错产品、重复ID（相同payload或冲突分别记录）、控制数不符和限额都保留原响应并停止。pageTotal/recTotal允许非bool整数或ASCII数字字符串；ID原值保留，格式字符串不偷偷改写。非dict条目不静默丢弃。deleted、空status、version、lastEditedDate、title和sourceNote不驱动业务语义或删除判断。

明确源异常只转换为固定code，issues不保存异常原文。ZenTaoRequestRejected已合并多种HTTP状态，request_rejected不能证明403或对象不存在；未知错误、未列明HTTPException和取消原样抛出。输入scope/actor错误在调用reader前抛ValueError。完整code与字段契约见分析仓B05-R0b实施计划。

跨页非原子；permission_filtered_records/unseen_deleted_records恒为unknown，deletion_inference=false、business_state_mapping=not_performed、production_import_eligible=false。对象证据不能批准来源关系、认定需求完成或生成删除事件。

NativeZenTaoClient.request目前仅是本地签名候选。它的401处理可能重登录；account/native_account在有provisioner时可能确保账号。核心GET-only不证明整条网络链零重试或无账号副作用。真实绑定须另核当前入口/精确版本、API分页、受控只读身份和可见范围、禁开通能力、认证副作用以及同一对象原生ID/状态/版本对账；本包没有执行该绑定。

在隔离工作树backend目录运行本地合成验证：

    PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -B -m pytest -c /dev/null -o pythonpath=. tests/test_sdk_story_snapshot.py -q -p no:cacheprovider

无pytest-asyncio依赖，异步核心由asyncio.run驱动。不运行全平台、postgres、live或真实SSO测试。本包没有生产变更；移除本次三个新增文件即可撤销应用代码，原始失败证据应保留。
