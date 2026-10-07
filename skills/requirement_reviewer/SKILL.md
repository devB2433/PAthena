---
name: requirement_reviewer
version: 0.1.0
---

# requirement_reviewer

本技能用于安全需求与纯静态代码分析。方法参考 google/mantis 的 mantis-review，已按本项目证据、模块和合规边界改写。

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

## [REQUIREMENT_REVIEWER-01]
逐条复核既有需求对象，返回带 subject_id 的 review，不另造需求替代原记录。

## [REQUIREMENT_REVIEWER-02]
独立读取原始文档和标准，不能只相信生成角色的理由。

## [REQUIREMENT_REVIEWER-03]
检查来源、条件、范围、冲突、重复、验收可执行性和条款关系。

## [REQUIREMENT_REVIEWER-04]
检查需求是否覆盖批量操作、旁路接口与跨模块保护。

## [REQUIREMENT_REVIEWER-05]
SUPPORTED 表示需求依据和表达得到支持，不能表示实现已经通过。

## [REQUIREMENT_REVIEWER-06]
缺证用 NEEDS_EVIDENCE；真实错误用 REJECTED 并给出原始反证。

## [REQUIREMENT_REVIEWER-07]
重点检查条件是否被丢失、示例取值是否被强制化、显式/推导来源是否混淆、版本和 TODO 是否被误当作最终设计、验收项能否逐条核对。peer 需求摘要用于发现重复，目标需求仍必须独立读取原文。复核不修改原始需求；审查原子性失败或存在不支持的强制条件时明确说明，不默默生成替代项。
