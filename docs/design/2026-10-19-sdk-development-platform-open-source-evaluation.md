# SDK 开发管理平台：开源产品预选型

日期：2026-10-19。本文是基于官方资料与项目 GitHub 仓库的桌面评估，尚未部署或完成真实 SDK 场景验证。业务验收标准以[需求草案](2026-10-19-sdk-development-platform-requirements.md)为准。

## 建议

先试运行**禅道开源版**，用它承载需求、任务、Bug、测试单、版本和发布等通用研发记录；AI-Agent-Platform 保留统一入口、AI 分析与 SDK 特有证据能力的设计空间。GitHub 上的候选项目各有强项，但目前没有证据表明其中一个能在“不动现有 GitLab”的前提下，以单套开源系统更完整地覆盖这条流程。先不在现有平台重写整套通用研发管理页面。试运行不接 GitLab 开发事件，也不改变现有仓库。

选择禅道作为首试对象的依据：官方开源版说明覆盖需求、任务、用例、构建、测试、缺陷和发布，支持私有部署；官方使用手册给出从需求到发布的流程；开源版提供 API 2.0 和 Docker 镜像。参见[开源版说明](https://www.zentao.net/zentao-os.html)、[最简流程](https://www.zentao.net/book/zentaopms/1703.html)、[Docker 部署](https://www.zentao.net/book/zentaopms/docker-1111.html)、[API 2.0 发布说明](https://www.zentao.net/download/pms21.7.8-85834.html)。

## 候选对照

| 产品 | 官方资料确认的可复用能力 | 对本项目的主要边界 | 本轮判断 |
| --- | --- | --- | --- |
| 禅道开源版 | 需求、任务、构建、测试单、用例、Bug、发布；Docker 私有部署和 REST API。 | 开源版不提供自定义工作流和 LDAP；虽有第三方免密登录 API，其签名没有绑定用户名，需单独安全评审。构建 API 的标准字段主要是构建名称、源码/下载地址、描述，完整子模块 SHA、产物摘要与按证据阻断需实测或扩展。 | **首选试运行**，先验证现成流程，再量出专属差距。 |
| OpenProject Community | 项目、需求类工作包、自定义字段、版本；有 Docker Compose 部署方案。 | 官方将测试管理定位为轻量的工作包建模，明确不替代完整自动化或实验室管理。 | 若团队更看重项目计划，可作备选；SDK 质量闭环匹配度较低。 |
| Tuleap | 官方文档有需求追踪、测试活动和 JUnit 结果导入。 | 官方安装文档当前以 Enterprise Edition 为主，Test Management 的免费可用范围需单独核实；不宜预设能免费直接上线。 | 暂不作为免费首选。 |
| 现有 GitLab | 具备发布、包仓库和测试报告等能力。 | Requirements 功能官方标为 Ultimate；用户已明确首期不打通 GitLab 开发流程。 | 保持现有代码托管用途，不作为本期研发管理选型。 |

对照依据：[禅道版本差异](https://www.zentao.net/book/zentaopms/961.html)、[禅道开源版工作流答复](https://www.zentao.net/thread/296492.html)、[禅道开源版 LDAP 答复](https://www.zentao.net/ask/599349.html)、[禅道第三方免密登录 API](https://www.zentao.net/book/zentaopms/344.html)、[禅道构建 API](https://www.zentao.net/book/api/post-builds-2230.html)、[OpenProject 测试管理](https://www.openproject.org/docs/use-cases/test-management/)、[OpenProject Docker Compose](https://www.openproject.org/docs/installation-and-operations/installation/docker-compose/)、[Tuleap 测试管理](https://docs.tuleap.com/user-guide/testmanagement.html)、[Tuleap 安装指南](https://docs.tuleap.com/install.html)、[GitLab Requirements](https://docs.gitlab.com/user/project/requirements/)。禅道采用 [AGPL/ZPL 双许可](https://www.zentao.net/zentao-os.html)；若要修改源码并与现有平台深度组合，须按选定许可核对具体义务。

## GitHub 候选复核

| 项目 | 从项目仓库确认的强项 | 对本期目标的判断 |
| --- | --- | --- |
| [Plane](https://github.com/makeplane/plane) | 界面和协作重心在工作项、周期、模块、文档与路线图，可自行部署。 | 适合需求和迭代管理；仓库介绍没有把测试活动、构建产物和发布证据作为完整内建流程，不能凭现代界面替代质量闭环。其 [Community Edition](https://github.com/makeplane/developer-docs/blob/master/docs/self-hosting/editions-and-versions.md) 与商业版为不同代码库，需按免费版实际能力评估。 |
| [OneDev](https://github.com/theonedev/onedev) | MIT 许可，问题工作流、Git 托管、CI/CD 矩阵、产物仓库和 AI 辅助较完整。 | 如果计划替换现有代码平台，值得深入试用；本期保留 GitLab 且不接开发流程，OneDev 的主要优势无法直接使用，同时未见内建的需求验收与人工测试单闭环。 |
| [Kiwi TCMS](https://github.com/kiwitcms/Kiwi) | 专注测试计划、用例、运行、构建版本、自动化插件与 API；官方[数据模型](https://github.com/kiwitcms/Kiwi/blob/master/docs/source/guide/introduction.rst)明确按构建和环境分别记录测试运行。 | 对设备/固件测试可能比禅道更合适，可作为测试专项备选；它不是需求、开发任务到发布审批的一体化管理系统。 |
| [Redmine](https://github.com/redmine/redmine) | 稳定的项目与问题管理基础，支持插件扩展。 | 测试闭环依赖额外插件和维护；与“单套系统直接跑起来”目标相比，优先级低于禅道。 |

此外，[SquashTM Community](https://www.squashtm.com/en)有更深入的需求与测试管理，官方[源码仓库位于 GitLab.com](https://henixdevelopment.gitlab.io/squash/doc/squashtm-doc-en/v11/faq/offer.html)。它适合测试专项评估，不能据此推断已覆盖开发任务、SDK 构建产物和发布审批。以上“未见内建能力”是根据项目公开功能范围作出的选型判断，仍须在试运行中核实，不等于断言产品无法扩展。

## 用真实 SDK 流程验收

1. 用一条实际需求建立产品、项目、工作项、验收标准，并记录评审和变更历史。
2. 为一次候选发布记录 OpenOrbbecSDKGroup 提交、七个一级子模块和 Viewer 内嵌 SDK 的实际提交。区分打包基线与主 SDK `develop` 分支。
3. 分别登记 Windows、Linux、macOS 的构建及产物，核对文件 SHA-256、构建脚本参数和产物下载位置；检查是否能反查到同一候选。
4. 把 JUnit 报告、硬件测试环境、设备/固件、失败与跳过原因关联到具体产物。验证未执行、阻塞和失败不会被显示成通过。
5. 尝试在缺少必须测试或摘要不匹配时阻止发布，并保留有权人员例外放行的审计记录。如果开源版本身做不到，记录需要由 SDK 专属服务补齐的最小数据和规则。
6. 核对现有 AI-Agent-Platform 用户如何进入并访问禅道。开源版 LDAP 和自定义工作流的缺口必须计入正式方案。官方免密登录 API 使用 `md5(code + key + time)`，签名不含用户名；不能未经安全评审就把签名 URL 交给浏览器作为统一登录方案。

## 通过后如何落到现有底座

开源产品作为需求、任务、测试等通用记录的权威来源。AI-Agent-Platform 的 SDK 入口展示授权范围内的概览与跳转；在需要 AI 分析或 SDK 专属基线、产物、设备测试和发布门槛时，通过受控接口读取同一记录。是否需要单点登录、API 适配或独立专属服务，以试运行证据决定。业务主记录不双写。

本机当前无 `docker` 命令，尚未启动试运行实例；上述可运行性来自官方部署资料，不代表本地已通过部署验收。
