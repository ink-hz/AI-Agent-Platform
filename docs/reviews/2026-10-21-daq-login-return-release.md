# DAQ 钉钉登录回跳修复发布

2026-10-21。天启从 `/daq/` 进入钉钉登录后落到 Platform 根页面。DAQ 前端发出的登录地址已带 `return_path=/daq/`，Platform 后端也接受该路径；Platform 前端 `loginReturnPath` 的白名单遗漏 `/daq/`，将它改成了 `/`。当时公网登录页使用的 `index-weVndMcv.js` 与修复前主线的隔离构建 SHA-256 均为 `cad287de88840c70add500f84e76c505b7611e68e056761e52f060b89789579b`，确认了线上同一故障代码。

## 修改与验证

- Platform 主线提交 `21d82d60155076dcd0086fa76fa2a86cd6457ce9`，已推送。登录回跳仅新增 `/daq/` 和规范的 `/daq/conversations/<会话 ID>`；拒绝 DAQ API 路径、嵌套路径、`.` 与 `..`。相机仓、DAQ 仓和 Platform 后端均未修改。
- 新增的 `/daq/` 回跳用例在修复前分别失败：`loginReturnPath` 得到 `/`，扫码和钉钉内免登均向登录流程传入 `/`。修复后前端定向测试 **135/135**、后端回跳边界测试 **60/60**，前端构建成功。复审指出的点段拒绝与 `%3A` 会话深链均已覆盖两种登录流程。
- 无真实用户凭据或业务问答测试；天启本人登录后的浏览器验收仍须由天启确认。

## 切换身份

| 项目 | 结果 |
| --- | --- |
| 原 Platform API 镜像 | `sha256:cfb1b593c9362f0f1eefe0888af04cd391444882372b225bd60f80abae900722`，标签 `08e9e2584946091cdda4bcafe24c6326daaa58e4` |
| 新 Platform API 镜像 | `sha256:bb9735c95d0f5bbec189bb2fbe51c1d67508b2f3b9f84e3631c2fb3f0bf26743`，标签 `21d82d60155076dcd0086fa76fa2a86cd6457ce9` |
| 新前端入口 JS | `index-Dwtkhu-4.js`，公网与镜像 SHA-256 同为 `c96a05bdd2904e0e3b0f1fbf2191c7edeb3c659fddf0926d65399ddf1855a6dc` |
| 静态发布包 | SHA-256 `3cffff73ee14cbf2ceb4be9ab683cd852844883fec576b21a890661612b5474f` |

以原运行镜像为基底，只覆盖由上述提交的干净 Git 归档构建出的静态目录。主机暂存与 `preflight.json`、`cutover.json` 位于 `/opt/orbbec-agent-platform/staging/daq-login-21d82d60/`。暂存脚本 SHA-256 为 `2796a99930b62020e251495406bc608df0c31f1fa7fe0d52374d351f6fd4aa87`；切换脚本 SHA-256 为 `51acb4514a8504786582d9b85d062cef26d7336a6af9c97f51fdcc0a8dfe2dfc`。

切换前对当前容器标签中的 **16 份有序 Compose 文件、9 个服务**做配置对比，唯一变化为 `platform-api.image`。只重建 Platform API；其余 Platform 容器 ID 不变。切换后 Platform API、DAQ 和相机容器均健康；公网 `/api/health`、`/login?return_path=%2Fdaq%2F`、`/daq/`、`/office/` 均返回 200，新 JS 公网哈希与镜像一致。没有改 Nginx、数据库、授权 grant、DAQ 知识或模型服务，也没有向生产回答接口提问。

## 回退

原镜像和 16 份 Compose 配置保留。若需回退，以切换前 `preflight.json` 中的原容器与镜像身份为锚点，去掉 `daq-login-21d82d60/overlay.json`，按原有有序 Compose 文件只重建 `platform-api`，等待原镜像健康，并核对其他服务 ID 与公网入口。切换脚本在健康或公网静态资源检查失败时执行此路径；本次切换成功，未触发回退。
