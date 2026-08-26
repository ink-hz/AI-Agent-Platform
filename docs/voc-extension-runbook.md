# Agent Platform VOC extension 运维手册

## 服务边界

员工只访问 Agent Platform 的 `/agents/voc/workspace`。浏览器沿用 Platform Cookie 和 CSRF；Platform 后端从已验证会话取得内部用户 UUID，签发最长 60 秒的 `voc.submit`、`voc.read_self` 身份令牌，再通过 `orbbec-agent-platform-internal` 内部 Docker 网络调用 `172.30.0.8:18130` 上的 VOC workspace。该地址没有宿主机端口映射。浏览器不能指定用户、下游 URL 或能力，也看不到签名密钥。

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

1. 使用迁移专用凭据在 VOC 数据库应用 migration 013–015。
2. 以仅属于 `voc_platform` 的低权限登录运行 VOC readiness。
3. 启动 VOC workspace，并确认只监听内部网络地址 `172.30.0.8:18130`，且没有 `ports` 映射。
4. 配置并启动 Platform BFF。
5. 通过 Platform 同源健康接口和受控员工账号完成浏览器验收。

Platform 配置：

```text
PLATFORM_VOC_EXTENSION_ENABLED=1
PLATFORM_VOC_EXTENSION_BASE_URL=http://172.30.0.8:18130
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

## 故障与回滚

模型不可用时页面保留员工原文，不能降级成自动入库。VOC 不可用时 Platform 固定返回安全的 503，不回显内部异常或响应体。

回滚顺序必须先断开入口，再停止业务服务：

1. 设置 `PLATFORM_VOC_EXTENSION_ENABLED=0` 并重启 Platform。
2. 确认同源健康接口返回 503、其他 Agent 仍正常。
3. 停止 VOC workspace。
4. 切回上一已验收版本；不要删除 migration 013–015 的表、列或员工数据。

恢复时重新按“启动顺序”执行。若只需紧急隔离，保留数据库和秘密文件，先禁用 Platform extension，再停止私网 workspace 容器。
