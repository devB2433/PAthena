---
name: pci_requirement_generator
version: 0.1.0
---

# pci_requirement_generator

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

## [PCI_REQUIREMENT_GENERATOR-01]
只处理 scope.clause_ids 中由设计需求匹配阶段判为 RELEVANT 或 POTENTIALLY_RELEVANT 的候选条款。逐条读取结构化原文及必要父级上下文；即使 status 为 UNDETERMINED，也生成保留适用条件的合规需求。不得将候选写成已确定适用或已合规，不重新改写前一阶段的正式适用性判断。

## [PCI_REQUIREMENT_GENERATOR-02]
只提交 requirement 记录，origin 必须为 PCI_DSS，clause_ids 使用目标条款精确 ID；每条候选条款至少形成一项可验收需求并引用原文，不漏项或引用库存外条款。在 statement 和验收项保留 applicability_conditions 与 missing_facts 所对应的前提，区分可由代码观察的控制行为和需要部署、配置或组织材料确认的条件。其他设计需求只提供简要索引用于去重，不将其内容作为新的规范来源。

## [PCI_REQUIREMENT_GENERATOR-03]
每条需求说明目标模块、必须满足的安全行为、适用条件和可逐项检查的验收条件；区分规范性要求、指导和测试程序，不把示例写成统一强制阈值。独立安全行为分开成项，重复描述合并原始来源。

## [PCI_REQUIREMENT_GENERATOR-04]
组织流程、部署配置和运营控制仍生成对应需求，明确后续需要的外部材料；不能因为静态代码无法证明就删除要求或假定已经实现。

## [PCI_REQUIREMENT_GENERATOR-05]
上下游需求只作定位线索，所有新需求必须以原始标准和设计依据为基础。原文冲突、版本限制、条件和缺证写明，不补造项目部署、实现或认证结论。
