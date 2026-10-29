# SDK IP 证书续期与只读核验

2026-10-29。实现范围：DNS 专属续期 dispatcher、只读证书健康 JSON 核验器及对应本地测试；生产安装由主代理按既有授权执行，实际结果见文末。脚本不自动部署或发送通知。

## 已核实的责任链

| 证书 lineage | 实际客户端和调度 | 认证与部署 |
|---|---|---|
| `agent.orbbec.com.cn` | `/usr/bin/certbot` 2.9.0，`certbot.timer` | webroot `/var/www/letsencrypt`，无 nginx installer；全局 deploy hook |
| `fae.orbbec.com.cn` | 同上 | 同上 |
| `sdk-ip` | `/opt/orbbec-agent-platform/certbot-ip-venv/bin/certbot` 5.8.0，`orbbec-sdk-ip-cert.timer/service` | webroot 相同，`preferred_profile=shortlived`；全局 deploy hook 加 SDK reload hook |

IP 的实际证书已在 10/23、10/27 自动更新。10/29 外部 TLS 信任、IP SAN 校验通过，指纹与磁盘 cert3 一致；到期为 **11/3 04:27:55 CST**。这是约 6.7 天短有效期证书，不能仅按日期临近强制重签。

旧通用任务未指定 cert-name，误处理 IP 配置，出现 `domains or ipaddrs` 为空的错误；独立 IP 任务正常。生产已安装的 2.9 `renewal.py:481-484` 在解析前按 cert-name 选择单个配置，`storage.py:66-72` 仅返回该名称的文件；因此明确指定两个 DNS 名称可以隔离旧任务，无需升级客户端或迁移 lineage。5.8 的相同逻辑位于该 venv `renewal.py:650-653`。

现行两个 hook：

- `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx`：`nginx -t` 检查实际完整默认配置，失败只记录本机日志并 exit 1；成功后 reload。
- `/opt/orbbec-agent-platform/bin/reload-sdk-ip-nginx.sh`：同样检查默认完整配置后 reload。IP 成功更新时两者都执行，10/27 journal 记录两次 reload。此次保持该有效链，后续整理重复 reload 需要单独验证。

IP 的 HTTP ACME location 与 HTTPS `/sdk-sso/consume`、禅道入口均保留；不调整安全组、SSH 或其他服务。同域 `/sdk/` 虽已启用，旧跨源 SSO 仍作回退，不能删除 IP 入口。

## 只读核验器

`deploy/cloud/certificate_health.py` 只调用 `openssl x509 -noout`、`systemctl show` 和使用系统信任库的 TLS 握手，不运行 certbot、不写配置、不发送通知。默认只读取真正负责 IP lineage 的独立 timer/service，不查询无关旧 `certbot.service`。

```sh
python3 deploy/cloud/certificate_health.py \
  --warning-hours 24 --max-check-age-hours 26
```

输入时钟通过 Python `check_health(clock=...)` 注入，便于固定时间测试；生产使用 UTC 当前时间。检查磁盘与外部证书指纹、起止时间、证书剩余小时数、责任 timer 的 active/enabled、责任 service 最近结果/退出码及检查新鲜度。systemctl 在 `LC_ALL=C,TZ=UTC` 下采集，避免服务器时区误判。24h 为短证书预警阈值，26h 检查年龄阈值覆盖两个最多 12.5h 的自然调度周期，均可显式调整。

退出码：0 健康；1 检测到风险；2 无法完成采集/输入不合法。正常报告是单个 JSON 对象，仅含公有证书元数据、固定分类、布尔值与时间，不含私钥、ACME account、challenge、凭据、业务内容或上游异常正文。TLS 验证失败时返回固定 `collection_failed`，不以跳过校验取得证书来制造健康结果。CLI 参数解析错误仍使用 argparse 的标准 stderr/非零退出。

该核验器不等于通知闭环；需要由既有监控消费 JSON/退出码并验证实际通知送达。当前两个续期 service 的 OnFailure 均为空，云端证书告警覆盖未知。

## 最小生产变更候选：只改通用任务 dispatcher

原 unit 为 `/usr/lib/systemd/system/certbot.service`：Type=oneshot、PrivateTmp=true，ExecStart 为 `/usr/bin/certbot -q renew --no-random-sleep-on-renew`。原 `certbot.timer` 的日程与独立 IP unit 均不变。

`deploy/cloud/renew-domain-certificates.sh` 固定执行两个 DNS lineage，各执行一次；首个失败仍执行第二个，任一失败则聚合 exit=1。每项仅输出固定 JSON 与退出码，Certbot stdout/stderr 不进入 service 日志；诊断仍可由负责人脱敏检查 Certbot 自有日志。`CERTBOT_BINARY` 是用于隔离行为测试的显式依赖覆盖，生产不设置，默认 `/usr/bin/certbot`。脚本拒绝额外参数，不提供临时 IP 或强制续期入口。

安装前由发布负责人按以下顺序操作，任一失败停止：

1. 保存原 unit、现有 drop-in（含不存在状态）、两个 timer 状态、公有证书指纹和到期时间；核对没有续期任务正在运行，避免 Certbot 锁冲突。保留独立 IP 自动调度。
2. 只读确认两个 DNS renewal 的 authenticator 仍为 webroot、webroot_map 仍匹配、没有新增 pre/post 或 nginx installer；核对全局 deploy hook 内容没有变化。
3. 运行以下**有写入的 staging 验证**，每个名称一次，不扩展到全量 renew。它会写挑战文件、staging/日志等，不属于只读检查。2.9 `hooks.py:262-265` 在 dry-run 默认跳过 deploy/renew hooks；不加 `--run-deploy-hooks`、`--force-renewal` 或 `--break-my-certs`。只读调查阶段未执行，后续发布阶段结果见文末。

```sh
/usr/bin/certbot renew --cert-name agent.orbbec.com.cn --dry-run --non-interactive --no-random-sleep-on-renew
/usr/bin/certbot renew --cert-name fae.orbbec.com.cn --dry-run --non-interactive --no-random-sleep-on-renew
```

4. 两个 staging 验证均成功后，把经验证 runner 安装为 `/opt/orbbec-agent-platform/bin/renew-domain-certificates.sh`，root 拥有、其他用户不可写。保存下列 drop-in 为 `/etc/systemd/system/certbot.service.d/dns-lineages.conf`：

```ini
[Service]
ExecStart=
ExecStart=/bin/sh /opt/orbbec-agent-platform/bin/renew-domain-certificates.sh
```

5. daemon-reload 后检查有效 ExecStart/PrivateTmp 与两个 timer 状态。先由负责人决定是否启动一次常规 DNS 检查；该检查即使尚未到期也会写 Certbot 日志/状态，不能称为只读。不强制续期 IP 或 DNS。
6. 验证 dispatcher 两项 JSON 均成功、service exit=0；检查三个公网证书仍有效并与对应磁盘 lineage 一致。IP 的专属 timer/service 必须仍正常。下一自然调度继续留证；不能把 service 成功等同于已重新签发。

若 staging 验证失败，保持原 unit，不安装候选。若切换后失败，恢复原 drop-in/不存在状态及 runner 文件，daemon-reload；保留旧 failed 事实与排查证据，不停用仍负责 DNS 的原 timer，也不删除 IP renewal。生产 Nginx 配置失败不得重启或用独立 `-c` 配置绕过；证书不一致、业务入口异常或 CA 限流均停止后续发布，保留当前可用证书和运行服务。

## 本地验证与限制

```sh
backend/.venv/bin/python -m pytest backend/tests/test_certificate_health.py -q
sh -n deploy/cloud/renew-domain-certificates.sh
```

测试以固定时钟覆盖短有效期当前健康、阈值、过期、指纹不一致、责任 service 失败、timer 停用、检查超龄、忽略无关旧 service、TLS 验证路径、安全 JSON/退出码；dispatcher 使用合成客户端验证 DNS 精确参数、两项均执行及失败聚合。动态生产 staging、安装与业务验收由发布负责人单独记录。

## 生产执行收据（2026-10-29 10:40—10:47 CST）

- 代码 `8a909223`，本地 23 项 RED→GREEN、0 失败；独立审查 spec/quality 均通过，无阻断项。健康调度配置 `a2f15ce8` 另行独立审查通过，均已归并主线。
- 两个准确 DNS lineage 各运行一次 staging：平台退出 0 / 18.07 秒，FAE 退出 0 / 9.81 秒。未请求 deploy hooks、强制续期或 IP 重签；日志保存在云端 root 私有本次任务目录。
- 原有效 unit 和三证书指纹已保存；当时没有额外 drop-in，两个续期服务均未运行。
- 精确代码安装在 `/opt/orbbec-agent-platform/maintenance/releases/8a909223f7d4e93397bb88c6a089e4cb879ac42f/`，root 拥有且其他用户不可写。`dns-lineages.conf` 仅替换旧 service 的 ExecStart，保留 PrivateTmp、原 timer 和独立 IP 调度。
- 常规 `certbot.service` 实际执行一次：两 DNS 均输出 ok，Result=success、ExecMainStatus=0。未到续期窗口不等于重新签发，三证书指纹均未变化。
- `orbbec-sdk-certificate-health.service/timer` 经实际 `systemd-analyze verify` 通过后安装；root 受控 selector 指向准确已审版本。小时检查已启用，服务在 ProtectSystem=strict 等约束下实际运行退出 0，报告无 alerts。
- 本机从外网核对平台、FAE 和公网 IP：三者均通过系统信任及域名/IP 校验，且指纹与磁盘一致。平台/FAE 到期仍为 2027-01-09；IP 到期仍为 2026-11-03 04:27:55 CST。
- 当次 IP 健康报告剩余约 113.7 小时、最近独立检查约 8.5 小时前，专属 timer active/enabled、service success；没有因旧任务错误而重签或停用正常链路。

本次没有修改 Nginx 站点、hook、IP 业务入口、安全组或 SSH；没有用户浏览器/登录态 SDK 业务验收。外部通知渠道和实际送达仍未绑定/验收，小时检查只提供本机安全 JSON、journal 与非零健康状态，不宣称告警通知闭环。下一自然续期调度的长期稳定性继续观察。
