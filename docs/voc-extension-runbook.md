# Agent Platform VOC extension 运维手册

## 服务边界

员工只访问 Agent Platform 的 `/agents/voc/workspace`，管理角色通过 `/admin/voc` 查看总览。浏览器沿用 Platform Cookie 和 CSRF；Platform 后端从已验证会话取得内部用户 UUID。员工请求签发最长 60 秒的 `voc.submit`、`voc.read_self` 身份令牌，管理查询单独签发只有 `voc.read_all` 的令牌，再通过专用内部 Docker 网络 `orbbec-agent-voc-extension` 调用 `172.29.0.3:18130` 上的 VOC workspace。该网络只连接 Platform API 与 VOC workspace，且没有宿主机端口映射。浏览器不能指定用户、下游 URL 或能力，也看不到签名密钥。

不要把身份令牌、签名密钥、数据库 DSN 或模型令牌发给员工做测试。VOC 服务没有独立账号和浏览器入口，也不读取 Platform 角色名。

## 创建共享身份密钥

生成一次至少 32 字节的随机原始密钥。Platform 读取服务账号拥有的绝对路径普通文件，权限必须为 `0600`；VOC 读取同一密钥的 Base64 表示。两份秘密必须表达完全相同的字节，不能与 Platform Session、模型或其他 Agent 的密钥复用。

示例仅展示生成流程，不输出秘密：

```bash
umask 077
openssl rand 32 > '/absolute/private/platform-voc-signing-key'
base64 < '/absolute/private/platform-voc-signing-key' > '/absolute/private/orbbec-voc-platform-identity-key-b64'
chmod 600 '/absolute/private/platform-voc-signing-key' '/absolute/private/orbbec-voc-platform-identity-key-b64'
```

将 Base64 文件按 VOC 的 `VOC_SECRET_DIR` 合约安装为 `orbbec-voc-platform-identity-key-b64`；Platform 使用原始文件路径。轮换时先同时安装新秘密，再依次重启 VOC 和 Platform；由于令牌最长 60 秒，不保留长期双密钥窗口。

## 启动顺序

1. 记录待发布版本：VOC 管理读接口基线为 `7d22735e9430f8940d457eaed830764ac678b409`；Platform 发布 SHA 必须由发布包的 `git rev-parse HEAD` 记录到变更单。
2. 先部署上述 VOC 版本，并使用迁移专用凭据应用 migration 013–016。不得在 Platform 页面上线后才补 migration 016。
3. 以仅属于 `voc_platform` 的低权限登录运行 VOC readiness，确认管理只读函数存在且执行授权正确。
4. 启动 VOC workspace，确认只监听内部网络地址 `172.29.0.3:18130`，且没有 `ports` 映射。
5. 由 Platform 服务账号签发临时 `voc.read_all` 令牌，验证 `GET /api/platform/v1/admin/vocs` 和 `GET /api/platform/v1/admin/vocs/{voc_no}`。探针只记录 HTTP 状态、条目数量和耗时，不输出正文、人员 UUID 或令牌。
6. 再部署 Agent Platform，将 Platform API 固定到 `172.29.0.2`，然后完成员工入口和管理入口验收。

Platform 配置：

```text
PLATFORM_VOC_EXTENSION_ENABLED=1
PLATFORM_VOC_EXTENSION_BASE_URL=http://172.29.0.3:18130
PLATFORM_VOC_EXTENSION_SIGNING_KEY_FILE=/run/secrets/voc-extension-signing-key
PLATFORM_VOC_EXTENSION_TIMEOUT_SECONDS=10
```

VOC 服务启动前执行：

```bash
python scripts/verify_workspace.py
orbbec-voc workspace
```

然后检查 Platform BFF，而不是向浏览器发布 VOC 端口：

```bash
curl --fail --silent http://127.0.0.1:8080/api/v1/extensions/voc/health
```

预期只返回 `{"status":"ok","service":"voc-workspace"}`。该健康接口不包含员工身份、数据库或模型信息。

## 浏览器验收

使用普通企业成员账号打开 `/agents/voc/workspace`，完成一次：

1. 输入自然语言并点击“整理成草稿”；此时数据库中不能出现正式 VOC。
2. 修改草稿字段并保存。
3. 点击“确认提交 VOC”，确认出现 VOC 编号。
4. 在“我的 VOC”打开详情并提交一条补充。
5. 用另一个员工账号确认看不到第一位员工的记录。
6. 将测试账号置于目录硬过期状态，确认读取仍可用、写操作在 Platform 侧返回只读保护。

不要求员工复制 Cookie、CSRF、身份令牌或运行数据库命令。

## VOC 管理页验收

1. 使用 `management_viewer`、`platform_admin`、`platform_owner` 各打开一次 `/admin/voc`，确认能看到总览；使用 `member` 打开同一地址必须显示无权访问，直接请求三个管理 BFF 接口也必须返回 403。
2. 验证关键词、Platform 提交人、历史钉钉提交人和日期筛选；只在变更单记录 HTTP 状态、结果数量和耗时。
3. 打开一个 VOC 详情，确认版本条目按顺序展示，并确认页面不存在编辑、删除、分配、状态变更或补充按钮。
4. 浏览器网络面板只能访问同源的 `/api/v1/extensions/voc/admin/*`；不得出现浏览器直连 VOC 容器的请求。
5. 检查 Platform 日志和 VOC 审计：不得出现 VOC 正文、签名令牌或数据库 DSN；管理下游令牌能力必须精确为 `voc.read_all`。

生产探针不得使用 `curl -v`、shell tracing 或打印响应全文。建议由受控验收脚本输出如下无内容指标：

```text
health_status=200 duration_ms=<number>
admin_list_status=200 item_count=<number> duration_ms=<number>
admin_filter_status=200 item_count=<number> duration_ms=<number>
admin_detail_status=200 entry_count=<number> duration_ms=<number>
member_admin_status=403 duration_ms=<number>
```

## 故障与回滚

模型不可用时页面保留员工原文，不能降级成自动入库。VOC 不可用时 Platform 固定返回安全的 503，不回显内部异常或响应体。

回滚顺序必须先断开入口，再停止业务服务：

专用网络首次切换后，不允许自动回滚到仍使用 `172.30.0.8` 共享网络的旧 Platform release；回滚脚本会在停止当前服务前失败关闭。只有同样声明 `orbbec-agent-voc-extension` 和 `172.29.0.3:18130` 的兼容 release 可以作为自动回滚目标。

1. 设置 `PLATFORM_VOC_EXTENSION_ENABLED=0` 并重启 Platform。
2. 确认同源健康接口返回 503、其他 Agent 仍正常。
3. 停止 VOC workspace。
4. 回滚 Platform 到上一已验收版本，使 `/admin/voc` 导航和管理 BFF 先消失；确认员工现有功能不受影响。
5. 如仍需回滚 VOC 容器，再切回上一兼容版本；不要删除 migration 013–016 的表、函数、列或任何 VOC 数据。

恢复时重新按“启动顺序”执行。若只需紧急隔离，保留数据库和秘密文件，先禁用 Platform extension，再停止私网 workspace 容器。
