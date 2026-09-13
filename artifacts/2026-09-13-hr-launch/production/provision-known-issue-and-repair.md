# Provision 日志 mode 已知问题与定点补救

执行对象为冻结的 `provision_hr_release.py` SHA256 `b649b93987c7333d49e9927aa721321b60b28012561c40917cfd48ec8e87afcf`，deployment `4cd463bbaf897639b891b04555bd0c78`。任务本身 exit 0，配置、知识和卷安装成功，preflight 只含预期 blocker `database_not_checked`，没有迁移或服务变化。

任务结束后的只读 `lstat` 发现，metadata 父目录为 root:root 0700，但 `stdout.log`、`stderr.log` 是 root:root 0644；同目录其他任务文件均为 root:root 0600。原因是 `_remote_launch` 的 nohup stdout/stderr 重定向发生在被启动 job 的 `umask 077` 之前，外层 shell 沿用了 022。metadata 目录权限使非 root 不能遍历，因此未形成非 root 可读路径，但这两个文件不符合任务制品统一 0600 的约束。

已对下面两个准确路径执行定点 mode 修复，未改内容：

- `stdout.log`：0644 → 0600；SHA256 始终为 `345e531bdc1720966bdadcba4bc1ee19deca04410b86c531735ba2438362cec5`。
- `stderr.log`：0644 → 0600；SHA256 始终为 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

修复源码和命令先验证准确 metadata 目录为 root:root 0700、两个目标为非 symlink 普通文件且 SHA 精确匹配，再只执行 `chmod 600`，最后复核 SHA 未变。完整回执位于 `production/provision-log-mode-repair-1/`：source SHA256 `7ca236adaa38cd6dd2f3ecdd49be43812a1f5a15e9fa995614d4bd96a15d5895`，结果 SHA256 `70cfdc32b20f1f01cd8c651429a40daa7a7b9b9454acff6befa7e91e45244b82`，stderr 为空。

冻结脚本保持原字节，不能把本次补救描述为原脚本已具备的性质。任何后续复用版本都必须在 nohup 重定向创建日志之前，于外层 shell 设置 `umask 077`，然后重新做源码审读、渲染后 `bash -n` 和实际 mode 负例。
