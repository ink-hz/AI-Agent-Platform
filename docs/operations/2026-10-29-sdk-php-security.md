# SDK 禅道 PHP 应急安全候选：Task 2

状态：2026-10-29，候选构建、定向原生/Apache 与完整 entrypoint 验证已结束，提交正式审查；**没有切换生产**。

目标：修复 R01 中 PHP 解释器的 CVE-2026-109618、CVE-2026-109619，保留禅道 22.6、员工 SSO、需求及附件能力和现有评论禁改防护。PHP 8.1 已结束上游维护，本次官方应急回补不代表恢复长期支持；迁移受支持版本须另行完成应用及扩展兼容验收。

## 已核实基线

- 工作树 `fix/security-first-batch-20261029`，起点 `cd041f307a5d0111640e48e158d3942649b16fea`。本线只修改 `deploy/sdk/`、`backend/tests/test_sdk_php_security.py` 和本记录。
- 生产容器 `orbbec-sdk-workbench-trial-zentao-1`，镜像 `sha256:1deeb004b41a691ef45fd8670a4794ef446bb3c0362e402a2e87a705cf26d5ce`，Debian 11 amd64。Apache `2.4.62`，构建于 2024-12-17；PHP CLI `8.1.31`，同日构建，NTS，PHP API `20210902`，Zend 扩展 `API420210902,NTS`。
- Web 主进程 `/proc/182/maps` 实际加载 `/opt/zbox/run/apache/modules/libphp.so`，不是 PHP-FPM。Apache 配置为 `LoadModule php_module modules/libphp.so`。CLI 和 Web 模块没有容器层修改记录；未取得任何供应商回补证明，不能因只有 CLI 版本证据就推定 Web 已修复。
- 旧 CLI SHA-256：`d89e0362e240db730bfab0abe0fa09a201ba1906201c7aaaaff659c2c5e83567`；旧 Web 模块：`adb7be4936aaab3dd178372ff1317d03e0a089ba95b46c7aa5da4fa3f0b800d2`；旧 OPcache：`24e9975e27c5432a4566cd63c3b5683402418c54f20bce14e96004398d47d8c5`。
- 已加载动态扩展为 ionCube 14、OPcache、APCu、igbinary、Redis、xlswriter、pgsql、pdo_pgsql。完整已安装扩展二进制摘要、已加载模块、原 configure options、构建信息及 image ID 保存在专用目录 `php81/baseline.json`；未读取环境变量或业务配置。
- 现有防护对 `/apps/zentao/module/action/control.php` 固定摘要 `9c6710dab686d7c39f8e3e49f9ea91ab575fc51c384bd9dd3d19ac12eb883b88`；它处理禅道评论漏洞，不能证明 PHP 解释器安全。

## 官方来源与选定路线

1. [PHP 10 月 21 日公告](https://www.php.net/archive/2026.php#2026-10-21-5)要求更新解释器；[OOM 公告](https://github.com/php/php-src/security/advisories/GHSA-w8hw-w9fx-j79w)和[全局状态公告](https://github.com/php/php-src/security/advisories/GHSA-6wmf-4rv5-p3rp)覆盖全部 Web SAPI，尤其 mod_php。受支持修复版本为 8.2.35、8.3.36、8.4.27、8.5.12。
2. [禅道官方补丁](https://www.zentao.net/download/patch-86832.html)的 Linux 二进制包明确限定 PHP 7.4.33；不能套在当前 PHP 8.1 容器。禅道 22.6 发布资料提供 PHP 8.1 版本，尚无本实例上 PHP 8.2+ 和现有 ionCube/动态扩展的兼容证据。
3. 父任务明确选择 [PHP 官方 8.1 最小源码回补分支](https://github.com/php/php-src/tree/PHP-8.1-CVE-2026-109618-109619)，固定提交 `1d7dec178a094e6e75b229ce22754045f8c6070a`，实际版本 `8.1.34`，同时重编 CLI、Apache libphp、OPcache。该提交修改 Zend 分配/异常清理、SAPI/请求解析、session、OPcache，不能只替换 CLI。
4. 完整官方源码归档：[codeload 固定提交](https://codeload.github.com/php/php-src/tar.gz/1d7dec178a094e6e75b229ce22754045f8c6070a)，18,098,488 字节，SHA-256 `dfedb39ecb7b6d51f11af460c1c8bacdff8b19a16aa9f6e4f00bf5827c93b958`。身份锁在 `deploy/sdk/php-source.json`，不跟随可变分支构建。
5. [PHP 10 月 23 日补充公告](https://www.php.net/archive/2026.php#2026-10-23-1)说明 session serialize handler 切换的补充风险 CVE-2026-115560，将在后续常规版本修复。当前候选从启动前系统 ini 关闭 `session.upload_progress.enabled`，CLI 和 Apache 均须验证生效；明确这是一项补充配置控制，不宣称所选提交含此后续源码修复，更不能以该 ini 代替前两个 CVE 的源码更新。

## 实施步骤和职责

- [x] 只读核实实际 Web 模块、CLI、全部扩展 ABI、configure、旧 image ID 和 SSO/IP 依赖。
- [x] 测试先行：候选校验器缺失及启动门缺失时，17 项测试实际失败。新增依赖库完整性测试也先失败，再实现；校验固定来源/版本/ABI、CLI+Web+OPcache 完整集、私有库摘要、符号链接、路径穿越及重复 JSON 字段。
- [x] `build-php-runtime.sh` 在受限容器校验完整源码摘要，安装构建依赖，保持原内置扩展和 NTS ABI，`make -j2`，执行 10 个准确适用 Zend/session/OPcache 原生测试。构建或适用测试首错阻止成功收据；两例已裁定 OOM 兼容诊断另列，不能冒称通过。
- [x] 用 `verify_php_artifacts.py` 验证收据与三个解释器产物及非 glibc 私有依赖库；不把测试夹具或伪造版本当成真实二进制验收。
- [x] `Dockerfile.hardened` 在固定禅道上游 digest 上重现原评论补丁，替换 CLI/libphp/OPcache，保留 ionCube 等现有动态扩展；私有依赖位于独立 `/opt/zbox/php-security-lib`，不覆盖供应商 `/opt/zbox/lib`，不打包第二套 glibc/动态加载器。
- [x] 候选运行前 `verify-and-start.sh` 同时核验全部解释器、保留的动态扩展与私有依赖字节摘要、版本/ABI、必要扩展及补充 ini，然后保留原评论控制器摘要门；任何不符都不进入原 entrypoint。这是启动完整性门，不代表供应商运行目录已全部改成只读；基镜部分 `/opt` 目录及模块仍由 nobody 持有，其所有权治理属于尚未完成的运行权限风险。
- [x] 在无网络、无生产挂载、无公开端口的独立容器运行 `probe-php-apache.sh`：使用真实 Apache 2.4.62 加载新 libphp，检查全部原已加载扩展、8.1.34/NTS/API、OPcache 与补充 ini。固定单 worker，用有界 8-MiB POST 在 4-MiB PHP 限额下触发请求解析 OOM，正面证明该 worker exit=1/signal=0/core=0、父 PID/容器稳定和新 worker 状态为空；再验证切换 session serialize handler 后的普通 fatal 与同 worker 下一请求恢复。失败阶段和隔离错误日志输出到操作收据，不删除首错证据。此为状态/恢复回归，不能称为执行过任意代码利用验证。
- [ ] 主 agent 复核新镜像摘要、真实 Apache 和启动门收据后，另行建立当前禅道一致 DB/data 恢复点。隔离副本须验证个人 SSO、无权账户、需求读写和附件上传/下载；未有真实附件样本不能记为通过。
- [ ] 只有父任务授权切换后才只重建禅道 Web 容器；不得迁移数据库、修改业务数据或通过关闭入口代替解释器修复。

## 隔离构建、资源与可复现打包

专用宿主目录 `/data/security-build-20261029/php81`；源码、工作区、产物和日志均在此目录。构建容器 `sdk-php81-security-build-20261029` 限制 2 CPU、3 GiB 内存及 swap 总额、256 pids，`no-new-privileges`，非 privileged、无宿主 socket、无公开端口；仅挂载该目录，镜像自带 `/data` 是新建的空匿名卷。网络为不连接生产服务的专用构建网络；最终运行探针使用 `--network none`。8 CPU/15 GiB 主机核验时 MemAvailable 约 7.6 GiB，磁盘可用 39 GiB；初估下载 150 MB、临时盘 5 GiB、构建/验收 30–60 分钟。

### 同类构建失败的架构复核（继续构建前的决策）

共同原因是**供应商基镜 Debian Bullseye 的安全仓生命周期，与活动 URL 仍返回的索引和实际包归档位置脱节**。腾讯源首先返回 security 包 404；简单换到另一个活动 mirror 或 Debian 活动兼容 URL 没有改变仓库生命周期，因此同一签名索引仍引用已迁走的包。已核对官方安全索引日期 `2026-09-12`，其中 `perl-base 5.32.1-4+deb11u5` 的包在活动官方/镜像 URL 均为 404，在 `archive.debian.org/debian-security` 为 200。第二次修改只匹配 HTTPS 而源文件实际为 HTTP，也没有改变实际来源；该失误及失败均保留，不能把重试次数隐藏为一次成功构建。

原设计只锁定业务基镜和 PHP 源码，没有锁定 builder 的分发仓库生命周期及依赖身份，故“再换活动镜像站”不能修复此类故障。后续统一采用 **Debian 官方归档 main/security 两个来源**，不逐个包改站点；固定归档签名索引 SHA-256、由 Debian keyring 验证签名，并由 apt 校验签名索引内的包摘要，同时保存实际包版本/来源清单。源码、三个解释器产物、私有动态库和打包镜像分别保留身份。若签名、索引身份或明确依赖仍不可取，停止该构建阶段，保留失败后另评估可重复的新 builder，不降低完整性要求、不反复跑到通过。

这是为了保持当前 PHP 8.1/禅道及第三方扩展 ABI 的**临时兼容构建方案**。Debian 11 与 PHP 8.1 的生命周期风险仍在；原有以及新编译依赖的全部 OS/CVE 风险没有在此批次完成审计或修复。不得把两个 PHP CVE 的回补、归档签名通过或 31 项工程回归，写成运行 OS/整个禅道/整个平台安全验收通过。

失败日志保留在 `logs/build-vendor-mirror-failure.log`、`logs/build-mirror-http-alias-failure.log`、`logs/build-active-bullseye-source.log`；故障均在隔离构建依赖下载阶段，未影响生产。归档统一方案替代上述活动源尝试，禁止绕过签名验证。

已用基镜内 Debian archive keyring 在无网络隔离容器验证两份归档 InRelease：main 的 Bullseye/Bookworm/稳定发布三个签名及 security 的两个签名均 `Good signature`。固定 main 索引 SHA-256 为 `7a888c9463d8c14a5fb7e8520b8ca942d0c08e5c446aa0066693131cc0e6761a`，security 为 `405ed3dd62cd1fb797b1f5340ccef3f14b32352e564ba65b4d68c29933660ff4`；记录在 `php-build-indexes.sha256`。构建脚本先验证所存索引签名，再经 apt 正常签名校验取回索引，并在安装前比对固定索引摘要。架构复核期间暂停隔离容器，补齐来源门后再继续；未放宽验证或单独替换某个依赖包。

### 实测下载成本和统一并行预取

串行官方归档约 30 KB/s，APT 待取 134 项、约 116 MB，意味着纯下载可能约 60 分钟，超过初估。父任务明确批准最多 4 连接的统一预取；不改变官方归档来源、固定签名索引或 SHA-256 验证，不引入第三方 apt-fast。

串行下载容器已停止并保留为 `sdk-php81-security-serial-download-20261029`，其日志另存 `logs/build-verified-archive-serial-download.log`，完整 cache 保留。仅把本任务容器的已验证索引/cache 状态保存为中间 builder 镜像 `sha256:a7acbc055b2b6cff276d60463bd41b33c837be603af6f3bf86b68a85d5782742`；不是运行时发布镜像。新的同名 builder ID `9199156feb53ca71a6371a03a71d594a227e76e7c20d9e421c46e58e27eab487` 继续保持 2 CPU/3 GiB/pids256、无公开端口和生产挂载。

`prefetch_php_deps.py` 消费 APT 导出的固定待取 URI 清单和 APT 已验证的完整 Packages 内容，逐项匹配同一归档 URL、大小、SHA-256，拒绝未知来源、未签名包身份和 MD5 替代。最多四个并发请求，禁止重定向和代理，不自动重试；任一下载/大小/hash 失败会停止新请求，其他请求在下一个有界读取处取消。仅复用完整 SHA-256/大小匹配的缓存，部分下载保留为 `.part`，不生成成功收据。清单/输入摘要保存在 `prefetch-plan.json`，下载日志 `logs/prefetch.log`。完成后把完整包放入专用 builder 缓存，APT 仍验证固定签名索引和每包 SHA-256 后安装。新的 6 项预取契约测试已执行红绿验证。

预取链的生产者/消费者复核：首次导出假设 APT Packages 是无后缀文件，实际为 `.lz4`，因此导出停止；随后计划解析假设 `--print-uris` 必有第四列 MD5，实际安全仓已经不提供 MD5，只输出 URI/文件名/大小三列，故在任何网络预取前停止。共同原因是尚未用实际 APT 生产者输出完整定义适配器输入合同，不应逐字段猜测补丁。统一合同现为：精确选择单个允许压缩后缀的索引、交由 APT 自身解压；URI 清单只消费前三个必有字段，允许且不信任第四列；包 SHA-256 必须来自固定已签名 Packages。新增 SHA256-only 安全仓三列回归先失败再修复。保留首个解析失败记录，修正合同后另建预取批次，不把此失败藏成网络重试或换源。

预取成功：本批实际 **912.980 秒**；127 项待取共 111,491,800 字节均通过，另复用 7 项完整缓存。APT 再验证安装记录为 `Need to get 0 B/116 MB`。实际总耗时在原生/镜像验收结束后单独记录，不能把缓存复核的短耗时冒充首次下载成本。

### configure 首错与统一构建前缀复核

依赖安装成功后，首轮 configure 停在 `Cannot find imap library (libc-client.a)`，未进入 make。该库实际由已签名 `libc-client2007e-dev` 安装在 `/usr/lib/libc-client.a`。原因不是缺包，而是把 PHP 全局 `--with-libdir` 设为 `lib/x86_64-linux-gnu`，误把 Debian 多架构目录规则施加给了仍使用 `/usr/lib` 的旧 IMAP 库。完整源码 `ext/imap/config.m4` 只查全局 PHP_LIBDIR/c-client；`ext/ldap/config.m4` 已原生支持在默认 `/usr/lib` 下回查 `<machine>` 子目录。因此统一改用 Debian 默认 `lib`，保留 IMAP、LDAP 和其余扩展，不添加逐库软链接、不改源代码、不换依赖来源。

这是对供应商自定义 `lib64` 布局与 Debian 开发包布局的构建契约复核：原配置移植设计把全局库目录当成单一多架构目录，未把各扩展已实现的探测责任核对完整。已保存 `logs/configure-multiarch-failure.log`、`logs/config-multiarch-failure.log` 和该轮 configure options，修改统一前缀后另建构建批次。签名/固定索引/完整 cache/源码摘要门继续执行，不能删除扩展后称兼容通过。

### 相邻 SASL 首错后的全量依赖合同审计

统一 lib 前缀通过 IMAP 后，下一轮在 `libsasl2` 缺失处退出 1，仍未进入 make。共同设计问题是移植时凭经验列举开发包，没有对基线全部 configure 能力/默认模块/保留扩展逐项建立生产者合同；仅修当前报错会重复暴露相邻缺口。因此暂停，统一审计 40 个原 configure 选项、52 个加载模块、12 个已安装 `.so` 字节与原 NTS/API，完整结果只缺 SASL 开发包。

| 能力边界 | 已审依赖和保留办法 |
|---|---|
| build/prefix/sysconf/include/ini scan/Apache/libdir | 40 项参数固定在 `php-configure-args.txt`；仅允许 APXS 和 libdir 两项布局适配，其余与已记录原参数逐项相等，未知、遗漏或重复参数直接失败。恢复初稿遗漏的显式 build 和 PEAR 参数。 |
| mbstring/ZIP/FFI/XML/OpenSSL/cURL | oniguruma、libzip、libffi、libxml2、OpenSSL、libcurl pkg-config 均实际可用；原版本约束继续由 PHP configure 执行。 |
| GD/freetype/JPEG/WebP/zlib/bz2/readline/intl/gettext | libpng/libjpeg/libwebp/freetype2/zlib/ncurses/ICU 三模块；bz2/readline/libintl 手工头库检查。 |
| IMAP/SSL/Kerberos/LDAP/SASL | c-client 头与 `/usr/lib/libc-client.a`、PAM/crypt 头库、LDAP/lber 多架构库、OpenSSL/Kerberos/SASL；只缺 libsasl2，保留全部能力。 |
| bcmath/pcntl/ftp/exif/calendar/mysqlnd/MySQL PDO/OPcache | PHP 内置源码负责，无需额外数据库客户端或真实数据库；OPcache 随核心重编。 |
| PEAR/SQLite/CGI/默认扩展 | PEAR 原树保留且不运行 make install/联网安装 PEAR；保留 SQLite/CGI 禁用。默认模块和第三方扩展按原 52 个模块/API/NTS/全部已安装二进制摘要复核，不通过删除扩展取得成功。 |

`php_build_preflight.py` 将上述全量门放在 configure 前；先真实 RED，`logs/build-preflight-red.json` 退出 1 且只列 `pkg:libsasl2`。另有 11 项 RED/GREEN 单测覆盖缺失能力、所有缺项一次报告、未知特性、保留模块字节/API及 configure 产物 ZTS。最小新增包由相同固定已签名 main 索引解析为 `libsasl2-dev 2.1.27+dfsg-2.1+deb11u1`，280,936 字节，SHA-256 `c88b720741ff894e1ba1cd2c3e1a565a4de62c590c8a295f54f55d1b303df7e7`；父任务批准仅补此包，预取实际 2.871 秒。其余 134 项不重下。

APXS 风险已实测：`/usr/bin/apxs` 提供 Debian Apache 头，逐项查询 TARGET 为 apache2，但没有对应系统 httpd，不能把缺失的 threaded 输出称为发现 prefork。初次多项查询为空的收据保留，最终以逐项查询记录为准。真实捆绑 Apache `-V` 为 2.4.62/prefork/threaded:no。configure 成功后、make 前再检查 `main/php_config.h` 明确 undef ZTS，并记录两侧 APXS/MPM 证据，任何 ZTS/缺失标记/非 prefork 均停止；旧 CLI 是 NTS 不能替代此生成产物门。

### 已批准的最小原生和 HTTP 验收范围

不执行无关 Zend 历史 bug 全套、全 session 或全 OPcache。初选 12 个准确文件中，现将 10 个仍适用文件固定到 `php-native-tests.txt`：Zend 的 bug40770、bug76846、gh12073、exception_before_fatal；session 的 session_decode_basic_serialize、session_encode_serialize、session_regenerate_id_basic；OPcache 的 001_cli、bug76275、bug78185。原 expected 不改的 gh11189、gh11189_1 单列 `php-oom-compat-tests.txt` 兼容诊断。每文件单独运行，首个非零/FAIL/BORK/WARN/SKIP/XFAIL 或未实际通过即停，保留该 log/out/diff，不重试。官方最小提交未新增 CVE 命名 PHPT，故不捏造官方 CVE 专测；需 CGI 的 RFC1867 PHPT 也不冒称已覆盖，CGI 继续关闭。

**父任务已在测试执行前裁定接受此精确 8.1 应急分支的 fail-stop OOM 边界。** 源码 `Zend/zend_alloc.c:371–421` 对所有 `zend_mm_safe_error` 打印后 `_exit(1)`，没有受支持新分支的 soft/hard 双级行为。因此 OOM 不运行缓冲回调、析构或关闭钩子，不保留原 worker；gh11189 两例期待的回调 `Success` 与此明确冲突，只记录兼容差异，不能改 expected 或宣称 12 全过。支持版本迁移仍是后续必需工作，不能将临时应急分支写成长期完成。

实际 Apache HTTP 单次小场景负责 multipart 合成上传、请求解析 OOM、session fatal 和后续早期 INI 诊断消费者。OOM 必须证明 Apache 父 PID/容器未重启，原子进程 exit=1/signal=0/core=0，且无 SIGSEGV/SIGBUS/cgroup OOMKilled；新 worker 处理正常请求且状态干净，不能以任意新 worker 的 200 掩盖崩溃。普通 session fatal 仍要求同 worker 恢复；初始化 handler 必须由 Apache 针对该文件的 `php_value` 设置为 php_serialize，应用仅切换 php 并断言前后值。multipart 仅为有界合成上传，记录数量/大小/hash，不充作真实禅道业务附件验收。

首个原生批次已按首错停在 bug40770：`.out` 的多余行来自我手工 `TEST_PHP_ARGS` 与 make 自动 `PHP_TEST_SHARED_EXTENSIONS` 重复提供了相同新 OPcache；真正 Fatal 正文匹配原 expected。原 `.sh` 明确出现两个相同新模块参数，不能忽略 warning 取得 PASS。统一改由 make 负责唯一模块加载，并将 PHPRC/扫描目录指向独立空配置，防止新 CLI 先载入供应商旧扩展；保存 `logs/native-harness-duplicate-opcache/` 后另建适用批次，不重编源码、不改 PHPT expected。

构建日志 `logs/build.log`，原生测试日志 `logs/php-tests.log`，基线 `baseline.json`，固定源码 `sources/`，产物 `artifacts/`。打包前复制本目录部署文件到专用 `package/`，再将已验证 `artifacts/` 复制为 `package/php-runtime/`。二进制不提交 Git。命令：

```sh
python3 deploy/sdk/verify_php_artifacts.py /data/security-build-20261029/php81/artifacts
# 仅以下入口允许打包；它校验实际 COPY context 的 manifest/源码锁/字节，
# 从已验证 manifest 重生成 checksum，并固定 FROM digest 与 Docker 参数。
python3 deploy/sdk/verify_php_artifacts.py --build-package /data/security-build-20261029/php81/package
python3 deploy/sdk/run_php_apache_probe.py sha256:<候选镜像完整ID>
python3 deploy/sdk/run_php_entrypoint_probe.py sha256:<候选镜像完整ID>
```

## SSO、入口和回滚契约

正式 `/sdk/*` 经 Platform 身份、SDK 准入与个人禅道会话校验，员工留在同域；每页代理过滤 Platform Cookie。个人会话经 Docker 私网调用禅道换取，不依赖 IP 跨源表单。旧 IP vhost 仍承载回退入口和精确 `/sdk-sso/consume` POST 票据交接，当前代码的旧 `SsoService.start()` 仍生成该地址。故不能在没有调用方证据时删除 IP 站点；本次不改 Nginx、不改 SSO 代码、不读取票据/密钥。部署资料见 `deploy/sdk/README.md` 和 `docs/operations/2026-10-22-sdk-direct-zentao-release.md`。

回滚组合必须保存旧禅道 image ID `1deeb004…`、现行 SDK Compose/环境身份、`ZT_WEB_ROOT=/sdk`、一致 DB/data 恢复点和旧运行时摘要。只切镜像，不变更 DB schema 与持久卷；若业务失败，停止新 Web 并用原组合重建旧 Web。新写入不能用历史卷覆盖抹掉；是否恢复数据须由实际数据故障单独判断。旧镜像保留已知 PHP 风险，回滚仅为业务恢复，必须继续处置 R01。

## 停止条件与验收状态

源码/产物摘要不符、ABI/必需扩展失配、动态库缺失、原生测试首错、Apache 探针失败、原评论门丢失、内存 OOM 或触及生产可写挂载，均停止该阶段并保留失败，不隐藏重试或降低要求。若固定 8.1 源码仍不能兼容，先复核共享构建/ABI 契约，不叠加业务兜底。生产切换需父任务复核，不自动执行。

截至提交前：本地候选验证器、SDK 部署、代理、SSO 路由和个人客户端定向回归 **122 通过、0 失败**（PHP 专属 55，其余 67）；SSO 使用 ASGI/MockTransport 边界，不能充作真实禅道业务验收。Ruff、shell 语法和 diff 检查通过。隔离原生/Apache/完整 entrypoint 结果见下；真实 SSO、真实业务附件、生产切换、生产验收尚未执行，浏览器最终交互由用户负责。无模型调用、业务消息或生产数据写入。

### 真实 SAPI 与正式启动链复核（打包前闭环）

原生 make 约 03:36:30–03:41:40 UTC（约 5 分 10 秒，2 CPU）；依赖全量门与生成 NTS 门真实 GREEN。适用原生 10/10 PASS、0 SKIP/WARN/FAIL；兼容诊断 gh11189 为 1 FAIL（差异仅缺失预期输出回调 Success，符合已裁定 fail-stop），gh11189_1 按首错停止，1 未执行。原 expected 未修改，未以新子例替代旧失败。

第一候选 `98046f1f…` 已通过打包时 CLI/52 模块、ionCube、OPcache、ABI 和原评论门。首个隔离 Apache 批次 `apache-attempt-eizi3v1a` 在正常请求首错停止，尚未触发 OOM；错误为 required_extension_missing。随后真实旧/新 Apache 模块诊断显示二者均为 **51 个、差集为空**：pcntl/readline 只由上游构建进 CLI，Apache 额外提供 apache2handler。共同合同错误是用 CLI 模块合同代替 Web SAPI 合同，而非候选丢失模块。校验器现在分别约束 CLI 原 52 个与 Web 原 51 个，不删除任一原有能力；旧/新诊断 JSON 保存在 logs/web-modules-{baseline,candidate}.json。首错容器和错误日志保留。观察器原先仅保存 Docker stdout，Docker stderr 中的错误未进入 probe.log；新增 RED/GREEN 后统一保留两者，并将该首错完整 Docker 日志补存，不隐藏失败。

正式链路是 verify-and-start → vendor entrypoint → s6-init → s6 Apache run → apachectl。只读 vendor 源码确认：`/etc/s6/s6-init/run:43–45` 从 php.ini.tpl 重生成 php.ini；`/etc/s6/s6-available/apache/run:65–66` 在启动 Apache 前执行 control_php_ext，其 phpenmod/phpdismod 复制或改名 conf.d 配置。该链未发现覆盖 CLI/libphp/OPcache/扩展二进制的操作，但前置 guard 不能代表最终配置。职责改为入口前门检查运输完整性，Apache 最终消费前门检查初始化产物；固定原 Apache run SHA-256 `f3f46226116154e84199023e17c471718dbc886d0630267591e5dd3ae75d4c56` 后，只在其最终 exec 前插入同一只读校验器，并将新 run 自身纳入运行摘要。初始化渲染责任仍归 vendor，不额外重写一套 php.ini。

生产三个已变化配置仅取摘要，不读取正文或业务凭据：config.php `2d44c9598fda676255b8a5185d5a02b37001fd7be2873d8aac6340eb2415584f`；php.ini `80ef531d92c5b7da9da33114672b38b64c73f77255fbfd0e98030192c7a2785a`；httpd.conf `45dffa3ffe2dcf334223f3635115dfe77b532201ddc62f767745d41bca48d185`。这些现场值不等于基镜值，不能从源码镜像推断已运行配置。安全环境白名单仅见 MYSQL_INTERNAL=false、ZT_WEB_ROOT=/sdk，无显式 PHP_EXT_*。

完整 entrypoint 验收采用本任务独立空 MariaDB（固定本地 image ID `88ac718d…`）与候选 Web 共用该 DB 的 network-none 命名空间，只以 127.0.0.1:3306 通信；不接生产网络/DB/data，DB 数据只在新 tmpfs。DB 限 1 CPU/768 MiB/pids128、core0、lognone，Web 限 2 CPU/3 GiB/pids256、core0；均无公开端口、无生产可写挂载。新合成口令只经 stdin 入容器、不放 Docker Env；记录 ID/image/资源/命名空间和精确清理。验证 vendor 外部 DB 分支、/sdk 路径、初始化后完整 guard、真实 httpd maps 中的新 libphp/OPcache/ionCube 及摘要/99 ini；不是 SSO 或禅道业务质量验收。

OOM HTTP 接受结果在执行前固定为 curl=0/HTTP500，或在生成头前明确 EOF curl=52/HTTP000 且头/体为空。两种都必须同时具备目标 worker exit1、无 signal/core、父/容器稳定与后续干净请求；timeout、其他 curl/HTTP、任意其他子进程退出均停止。原响应头/体以合成收据保存，不添加代理兜底或重试。

| 阶段 | context / ini / modules 的生产与验收责任 |
|---|---|
| 原生 CLI 构建测试 | PHPRC 与 scan 目录均为空，make 唯一注入新 OPcache；只跑准确 PHPT，不能先载入供应商旧扩展、不能把此阶段叫完整 Web 兼容。重复 OPcache 的首错单独保留。 |
| vendor 初始化后 CLI guard | vendor 渲染 php.ini、control_php_ext 生成启用列表后，Apache 消费前复用同一 guard；保留原 CLI 52 模块/新二进制/ABI/安全 ini 和原评论摘要，并输出无秘密的最终门标记。 |
| 真正 Apache/Web | 同旧 exact image、同 prefork/httpd/PHPIniDir 配置取真实 Web 基线，再与新 Web 完整集合对比；应为原 51 模块、差集为空、仅新安全 ini 增项。PHP 源码 ext/pcntl/config.m4:1–4,28 与 ext/readline/config.m4:1–10 明确 CLI/CGI only，不能把 CLI 52 要求直接套到 Web。 |

完整 entrypoint 第一批 `entrypoint-attempt-_oeeqvel` 已首错保留并清理两自有 target：readiness 紧跟异步 docker start 即读取 created 状态，将其当成退出，而 finally 记录已 Running。完整链职责应分为：stdin 负责一次性凭据投递，容器内无秘密 marker 确认消费；Docker daemon 的完整 ID/image/attempt label、Running/StartedAt 表示服务生命周期，不能以 attach client 是否结束表示服务退出；最终 guard marker、真实 maps 和单次 HTTP 表示消费者就绪。只修适配器合同、另建批次，不增加业务调用重试。公用 sentinel 诊断证明 stdin 可一次投递完整 25 字节，结束时输出仅长度，测试凭据不入 Docker Env 或日志。新门对 created 状态 RED/GREEN。

完整 entrypoint 第二批 `entrypoint-attempt-ttwi4oee` 已走过 vendor 初始化、最终 guard 和 Apache 启动，但 maps 消费者误把所有 `(deleted)`（包括匿名/OPcache 共享内存的正常形态）当作 PHP 文件删除而首错停止。该批原 maps 在校验后才写盘，故缺少原始输入，不能声称已证明具体是哪条映射；这是证据生产者到消费者合同的缺口。统一改为原 maps 先落盘，只拒绝实际 PHP 核心/扩展/私有依赖文件的 deleted backing，并对匿名 deleted 与 PHP deleted 正负门 RED/GREEN。此前同样的合同问题还包括 CLI/Web 不同 SAPI、Docker created/Running：一次把真实生产者边界与证据保存责任写明，不能用宽泛猜测或跳过检查来追 PASS。第二批两自有容器也已精确清理；新独立批次只重验受影响 entrypoint，不重复已通过原生/HTTP，不重编 PHP。

## 最终候选与可审收据

- 最终 image ID：`sha256:7470cf4de0b483b753c380fac7dbfda7c98fe4badcda17109e85fe7f9fe50172`，tag `orbbec-sdk-zentao:22.6-php81-security-20261029`。本地代码只记录源码/配置/校验器，不提交产物或任何凭据。生产只读复核仍为旧 `1deeb004…`。
- 新 CLI SHA-256 `14386b089a1b4eb326727286385fec9652013341a8a09144df2023fdb9359bd2`；libphp `5163f3003b3d86263be8d10ca4c5d1a3016c3b16f98478cc4193d6d463e577fc`；OPcache `3db70ce76a535865a56c7246843b8c56868304879c88afe719f1502d040ef0e3`；补充 ini `a71ccc9991febaa7661d3c2c1fc902fcadb5bc049b861981000c238428716abd`。
- 适用原生：`logs/native-applicable/combined.log` 与 `native-applicable.exit=0`，10 PASS、0 FAIL/SKIP/WARN；兼容诊断 `logs/native-compatibility-diagnostic/`，1 已知 FAIL、1 按首错未执行。不得写成 12 全通过。
- 真实 Apache HTTP：`apache-attempt-r4_luxtp/`、容器 `1e93f2eeac9db15616c90301a714f959de11b38fe6a7002be428706ff019bdcc`，04:05:37–04:05:41 UTC。原 worker 容器 PID30/宿主1334855，父 PID27/1334845；仅向已确认该 cgroup/namespace 的父进程附着 wait4/SIGCHLD。收据明确 exit1/signal0/corefalse、父稳定、RestartCount0、OOMKilledfalse、cgroup oom/oom_kill均0；curl52/HTTP000/空头空体，worker53恢复；普通 session fatal 后同 worker、早期 ini 诊断、1文件16字节合成 multipart SHA `69f5f955235d32ec03eda5ec36fb684d3e6b22120833a8787844b748fc9bd0d3` 均通过。
- 上述 HTTP 批次使用 `43e41985…`；最终 `7470cf4d…` 只增加 guard 的成功标记。两 image 的实际 runtime.sha256 文件摘要均 `152d519ae858c2df4aabac328acb71a38a80066c4e53973be343bce19bf03a2f`，产物 manifest 文件摘要均 `f13704ddcd6d7599f0eb9c0de7a26a2fdf697f27878753e38c93a989bcf8a2f5`，核心与扩展字节未变。未为 marker 再跑无关原生/HTTP批次；最终 image 另跑完整正式入口及真实 Web 验证。
- 完整 entrypoint：`entrypoint-attempt-gsetsfzk/evidence.json`、`apache.maps`、`startup.log`，04:09:23–04:09:27 UTC，status=ok；凭据 consumed、最终 guard 标记、真实 `/sdk/` Web51模块与旧 exact image差集为空，maps实际新 libphp/OPcache/ionCube 和运行摘要通过。该次原 maps显示5项 `/dev/zero (deleted)` 正常共享内存，核心/扩展文件映射无deleted。DB ID `778e6d91c7077ca29add22506e34eeba7cd626d275f6aecf6e11fbe1c416055b`；Web ID `26269cd24936dbf4a131dd7d3697b1b010de2cded90009e0248cf05234bb778f`；共同 net namespace `net:[4026534110]`，不等于宿主。两 target 已删除、复核不存在，匿名卷/tmpfs随之清理。最终生产入口验证仍待父任务。
- 初始化后 config.php、php.ini 摘要分别与现场旧生产3文件中的前两项完全相同。隔离 httpd.conf为 `23cd95dc3540c9fcd66ab300371a938827a88f35679d7b6cbb8af574698679e1`，该测试显式禁用 Adminer，不能声称第三项与生产配置相同；不复制或覆盖生产文件。
- 最终 public 树：私有 `public-static/candidate-final-www-manifest.json` 与旧 `old-www-manifest.json`，1436项全树、1414项非 `.php` 集合（明确定义用于聚合，包含相关静态/控制文件）、其中590项JS/CSS；差集为空。全树规范JSON SHA-256 `7f1cd43e632580163af27c3a30a0714463c354b5b7f62a60b5806b90574c635e`，非PHP聚合 `a47194b865ae5356f5a968deade1a78ac180d5925b3563c3541bd7f851f5ea40`。原评论控制器仍9c6710da…；没有丢失/替换 JS/CSS。父任务切换后须以这些私有清单验证实际完整内容与来源，而非仅HTTP200。
- 成本：统一四连接预取912.980秒；只补libsasl2-dev2.871秒；原生make约310秒。最终 configure批次03:36:03UTC到完整入口通过04:09:27UTC的实测墙钟33分24秒，包含打包、保留的首错与受影响验证；此前活动源/依赖合同架构复核、串行下载成本另列，不能忽略或把它们算作成功缓存命中。全部失败批次和适配器差异保留。

正式审查重点：唯一打包入口是否绑定实际 manifest/source lock/FROM；Web/CLI与扩展ABI、vendor渲染到Apache消费的最后门；OOM fail-stop边界及session普通fatal同worker；失败批次分类；回滚是否仅Web镜像且不会抹掉新写入。审查通过及一致恢复点就绪之前，不执行生产切换。PHP8.1/归档Debian依赖生命周期和广义OS漏洞、第三方扩展运行权限及受支持PHP迁移均仍是后续风险，未由本候选消除。
