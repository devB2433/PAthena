---
name: pci_mapper
version: 0.1.0
---

# pci_mapper

本技能用于安全需求与纯静态代码分析。方法参考 google/mantis 的 mantis-plan，已按本项目证据、模块和合规边界改写。

## [SNAPSHOT]
只分析任务固定的快照，所有证据通过登记 ID 读取；旧快照事实不能直接证明当前情况。

## [EVIDENCE]
肯定结论必须引用原始文档、标准或代码证据；上游摘要仅是调查线索。

## [UNKNOWN]
缺证、解析失败、索引不完整和运行环境未知必须保留；不能判为安全、误报或不适用。

## [BOUNDARY]
识别模块、子系统、入口、数据流、主体与信任边界；检查跨模块关系及公共控制。

## [INVENTORY]
任务 scope 的条款、验收项或调查目标全部产生结果或明确缺口；检索相关性不能删除库存。

## [STATIC]
仅静态分析，不执行程序，不宣称动态复现、补丁验证或通过合规认证。

## [OUTPUT]
输出 StageOutput；不属于角色的记录不得生成；引用既有对象的确切 ID；无结论时保留 gaps。

## [PCI_MAPPER-01]
本任务分析 scope.requirement_id 对应的唯一设计需求，将其匹配到已经入库、固定版本的结构化 PCI DSS 数据。先读取需求引用的设计原文，再用 search_standard_library 检索英文控制术语、同义词与章节。候选不是结论，必须 read_standard_clause 读取原始规范要求、适用说明、测试程序、指导和来源；按需要读取其 context_ids 对应的原始范围或父级上下文。不得将测试程序、指导或例子当作规范要求。

## [PCI_MAPPER-02]
APPLICABLE、NOT_APPLICABLE、UNDETERMINED 三值独立于是否已实现。

## [PCI_MAPPER-03]
不适用必须有明确条件和原始证据；事实缺失不能自动排除。

## [PCI_MAPPER-04]
规范性要求、测试程序和指导分别理解，摘要不替代标准原文。

## [PCI_MAPPER-05]
只提交 applicability 匹配记录。requirement_ids 必须等于 [scope.requirement_id]，clause_id 为固定库的确切 ID。relevance 单独记录 RELEVANT（直接相关）、POTENTIALLY_RELEVANT（条件相关）或 UNRELATED（所读候选没有合理关联）。RELEVANT 和 POTENTIALLY_RELEVANT 都进入后续合规需求生成与代码检查，不因 status=UNDETERMINED 而删除。

## [PCI_MAPPER-06]
组织流程或运行控制仍保留，明确所需外部证据。

## [PCI_MAPPER-07]
本任务仅匹配当前需求，不能复制其他需求的匹配。合理检索多个术语或相关章节，继续检索结果的分页；需要时沿父子章节扩展。每条匹配引用设计原文和标准条款原文，同一条款只提交一次。没有相关匹配时 records 可为空，但 gaps 必须写明检索范围和不足；不能把未检索到匹配描述成全标准不适用或全量覆盖。

## [PCI_MAPPER-08]
技术相关性与正式适用性不同。设计中的认证、加密或日志可证明技术关联，但不证明属于持卡人数据环境；没有提及支付也不能证明不在范围内。缺少范围依据时 status 必须为 UNDETERMINED，missing_facts 写明部署关系与范围事实，applicability_conditions 写明适用前提；不能将未知范围当作 UNRELATED，也不能将技术关联当作 APPLICABLE。rationale 分别阐述需求与条款的关系、尚待确认的前提，不宣称合规认证通过。
