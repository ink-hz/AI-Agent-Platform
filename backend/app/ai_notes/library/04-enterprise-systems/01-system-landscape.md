---
title: 制造信息系统全景：先理解业务能力，再理解软件名称
slug: system-landscape
description: 从业务能力理解 ERP、MRP、PLM、MES 等系统，建立厂商中立的职责地图。
author: 苍渊
motto: 博观而约取，厚积而薄发。
publishedAt: '2026-11-11'
updatedAt: '2026-11-11'
tags:
- 方法
draft: false
---

> 适用对象：刚接手制造信息化的负责人。本文是厂商中立的学习地图，不表示本企业已经部署、购买或需要购买下列系统。资料核验日：2026-10-27。

## 1. 一张地图要回答什么

制造企业需要把“卖什么、如何设计、需要多少料、何时生产、实际做了什么、是否合格、在哪里、花了多少钱”连接起来。系统名称只是能力的包装：同一套 ERP 可以包含生产、仓储和质量模块；独立 MES 也可能带排程、质量、仓储功能。比较方案时先列业务对象、责任人、状态和决策，再看模块归属，不能按缩写数量判断先进程度。

以下表格是用于访谈的通用整理，实际边界应通过企业流程和合同逐项确认。

| 能力/常见缩写 | 主要问题与数据 | 应参与决策的业务角色 | 接手时的核查重点 |
| --- | --- | --- | --- |
| ERP，企业资源计划 | 订单、采购、库存财务、成本、结算等如何协同 | 财务、销售、采购、计划、仓储 | 哪些业务真正记账，哪些仍在表外 |
| MRP，物料需求计划 | 在给定需求、BOM、库存、在途和参数下，何时补什么料 | 计划、采购、工艺 | 输入是否可信，建议如何审核和执行 |
| PLM/PDM，产品生命周期/产品数据管理 | 产品定义、图纸、BOM、版本、工程变更如何受控 | 研发、工艺、质量 | 工程版与生产版如何转换、何时生效 |
| APS，高级计划与排程 | 在产能、工装、换型、物料等约束下如何安排 | 计划、生产、设备 | 约束是否真实，插单与重排由谁批准 |
| MES，制造执行系统 | 工单、工序、人员、设备、用料和报工如何执行与留痕 | 生产、工艺、质量 | 数据发生在哪里，异常能否闭环 |
| MOM，制造运营管理 | 生产、质量、维护、库存等运营活动如何协调 | 工厂管理及各职能 | 是业务管理范围还是产品宣传名称 |
| WMS，仓库管理系统 | 收、上架、补货、拣配、移库、盘点、发运如何执行 | 仓储、物流 | 库位、批次、容器、任务与账务边界 |
| SRM，供应商关系管理 | 准入、询报价、协同交期、绩效、供应商问题如何管理 | 采购、质量、财务 | 供应商承诺与采购订单是否一致 |
| QMS，质量管理系统 | 标准、检验、不合格、纠正预防、审计如何闭环 | 质量及各流程负责人 | 测量记录、判定、处置、放行是否分权 |
| SCADA，监控与数据采集 | 工艺状态、报警、趋势、操作监视如何呈现 | 自动化、设备、生产 | 信号质量、时间、报警责任与历史保存 |
| IIoT，工业物联网平台 | 多源设备数据如何接入、建模、传送与分析 | OT、IT、设备 | 语义与质量是否统一，断网如何处理 |
| BI/数据平台 | 跨系统经营与运营指标如何解释和追溯 | 管理层、业务分析、数据团队 | 指标分母、更新频率、明细证据是否明确 |

SAP 对 MES 的公开说明强调生产过程的跟踪、记录以及企业系统与现场之间的联系；Oracle 对 PLM 的说明覆盖产品信息、文档、变更等生命周期数据。它们支持理解能力范围，但不证明所有厂商具有同样功能，也不构成项目收益承诺。[S03：SAP MES 介绍](https://www.sap.com/resources/what-is-mes)、[S04：Oracle PLM 介绍](https://www.oracle.com/scm/product-lifecycle-management/what-is-plm/)

## 2. ISA-95：描述活动和边界的共同语言

可把生产物理过程理解为 Level 0；感知和执行为 Level 1；监视、控制为 Level 2；制造运营活动为 Level 3；经营计划与物流为 Level 4。使用这一视角是为了讨论时间尺度、活动和信息交换，不是给软件盖一个永久楼层章。ISA 的公开材料说明其接口模型与制造运营活动范围；官方技术文章还明确指出，层级用于定义逻辑集成边界。[S01：ISA-95 官方系列介绍](https://www.isa.org/standards-and-publications/isa-standards/isa-95-standard)、[S02：ISA 对逻辑边界的说明](https://www.isa.org/intech/2020/september-october/the-isa-95-enterprise-control-system-integration-s)

因此，“MES 必须只在本地”“ERP 绝不能读取设备数据”“必须逐层经过某个系统”“一个层级对应一台服务器”均不能由这个模型直接推出。云、边缘、混合部署要另行考虑时延、连通性、可用性、知识产权和维护能力；网络安全分区也需要独立设计。本文未读取付费标准全文，不把上述学习解释表述为标准规范条款。

## 3. 负责人必须识别的三组重叠

**MRP 与 APS：**前者侧重物料需求及补充建议，后者通常更深入处理排程约束，但实际产品可以同时提供两者。物料算得齐不代表设备、人、模具同时可用；排程看起来平滑也不代表库存和交期数据真实。Microsoft 的计划文档展示了有效 BOM、现有与在途供应参与计划的具体实现，不能直接套用为所有 MRP 的算法。[S06：Microsoft 生产计划文档](https://learn.microsoft.com/en-us/dynamics365/supply-chain/master-planning/planning-optimization/production-planning)

**ERP、WMS 与 MES：**ERP 常承载企业订单和库存财务，WMS 细化仓内任务，MES 记录现场执行。线边库、领退料、完工入库是最容易重复记账的地方。Oracle 文档提供了“外部 WMS 执行后回传收发确认”的产品实例，说明库存管理与仓库执行可以分开，实际项目仍须约定唯一过账入口。[S20：Oracle 外部 WMS 集成说明](https://docs.oracle.com/en/cloud/saas/supply-chain-and-manufacturing/25c/famml/third-party-logistics-and-warehouse-management-systems.html)

**QMS 与其他质量模块：**质量责任贯穿供应商、过程与客户，不能因上线 QMS 而移交给 IT。ERP 也可能提供检验和冻结，MES 可以采集过程参数，实验室系统管理检测过程；应明确谁形成原始记录、谁判定、谁处置、谁放行。[S21：Microsoft 质量与不合格管理实例](https://learn.microsoft.com/en-us/dynamics365/supply-chain/inventory/quality-management-processes)

## 4. 用能力清单验收，而不是用菜单验收

为每项能力建立一行：业务负责人、输入、输出、权威记录、允许修改者、异常处理、审计证据、指标。选一个真实订单，沿流程找出人工复制、重复录入、无人处理的等待和无法解释的差异。得到的可能是参数治理、培训或接口修复需求，也可能才是软件缺口。能力清单应同时保留“已购未用、已用但绕行、无系统但有效、确实缺失”四种情况，避免把购买清单当作信息化现状。

## 延伸阅读

- 下一篇：[订单流程与系统边界](/ai-notes/enterprise-systems/process-and-system-boundaries)。
- 深入集成：[接口与数字主线](/ai-notes/enterprise-systems/integration-and-digital-thread)。
- 来源的证据范围与访问限制见 [systems.json](https://github.com/ink-hz/AI-Manufacturing-Platform/blob/53958c7cede6707c41b99cba7c636402f3c23fe3/knowledge/sources/systems.json)；厂商资料仅作能力示例。
