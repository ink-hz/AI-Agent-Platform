# 1dfbd40 deadline 修复独立复核

结论：提交 `1dfbd40624db8aeac69879b07dc672c6a3996ef0` 消除了961bac7中已复现的持续慢流绕过请求截止问题，可用于已授权、POSIX主线程、单操作者串行的canary执行。认可的是有墙钟信号中断的网络请求，不是整个进程/文件系统/生产擦除的无条件精确时间保证。原961bac7失败审读及慢流证据未改。

## 源码复核

`api_canary.py:29–58` 的共享bounded_request在发送前要求主线程、setitimer可用、没有活动或周期ITIMER_REAL；非符合环境固定拒绝，不回退到弱timeout。不更改已有timer；无活动timer时保存SIGALRM handler，安装专用handler、以真实剩余秒数启动一次性alarm，finally取消alarm并恢复handler。

RequestDeadline继承BaseException，意图绕过HTTP库常见Exception重试捕获；两canary显式捕获它和httpx.HTTPError，先持久http transport_unknown，再抛CanaryError。继承mutate在网络前已经持久sending，捕获CanaryError后落盘outcome_unknown；重启/恢复只能复用received，不能重新发sending或unknown的上传/DELETE。中断发生在请求已被接受、完整响应未知时依然保守，未伪造拒绝或成功。纯本地超时写盘失败也保留先前sending，不授予重发权。

计时在网络发送前重新取ledger deadline，避免预写日志耗时后仍用旧remaining。原mutation deadline不续期；附件observe仍是独立20秒只读窗口。使用time.time保存跨进程截止、setitimer约束单请求；未声称抵抗系统时钟人为改动。SIGKILL/主机故障与不响应Python信号的OS工作不在本次证明中，新README已明确。没有跨进程锁，因此同一ledger禁止并发执行的操作限制仍必须遵守。

## 证据核对与独立复跑

作者green-final-1原记录为15 passed /32.26s、9条TestClient timeout弃用warning，实际组成8 HR+4附件+3deadline（双canary慢流参数2例及既有timer1例）。两慢流elapsed分别0.156761/0.161244秒；测试不仅检查耗时，还检查handler复原、timer归零、durable unknown、恢复后server只收到一次POST。已有timer测试断言原计时器仍>9秒且handler不变。非主线程分支本次为源码核对，没有独立负例，不称已测试。

作者run开始HEAD7207228，不误称测试发生在1dfbd40提交之后。记录的3个业务/test脚本和2个canary SHA为确切快照；本次固定两个canary及deadline测试的commit字节，与原green命令SHA相同：

- api_canary.py `13fcb02d28132afbe84268b0b5576946f2bc32dbaea8f1c2f74abaf702e0abd5`
- attachment_erasure_canary.py `295fbc85c6d7179577971adf082bebec1140fcdca7149be8c1f4fcc3e4d96573`
- deadline test `abd2660f3b1e4cc9de59735c7450dc2aa430ec0acc3c66f08a68eb48f6cabfa8`

本次只重跑exact commit快照的附件慢流1例，命令/退出码见command.json，原输出见local-slow-stream.log：1 passed /0.56s，实际0.1688816249370575秒（0.15秒预算），unknown持久化及不重发断言同时通过。测试映射到自有loopback socket，无生产、真实身份或模型调用；没有重跑全套。`-c /dev/null`令pytest尝试在/dev建cache并产生1条权限warning，不影响测试结果，原warning保留。

## 仍然分别验收的边界

此前身份/CSRF/owner与附件范围审读结论未扩大；物理S3擦除仍需root准确对象图、版本、job和实际对象存储观测。新README已明确原四附件测试使用含100及102–105的root，不证明旧95物理擦除；404仍不代表物理删除。没有替换或修改staged release，没有执行生产或提交代码。

`snapshot/`固定本次commit源码和作者原证据，fingerprints.json完整列SHA。本报告只批准所审小修，不预先批准未来改动。
