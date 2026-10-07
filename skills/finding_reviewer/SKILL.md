---
name: finding_reviewer
version: 0.1.0
---

# finding_reviewer

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

## [FINDING_REVIEWER-01]
逐一独立重读原始代码、文档与条款，复核既有 subject_id，不复述发现者的论证。

## [FINDING_REVIEWER-02]
检查攻击者输入控制、入口到危险点的可达性、主体权限和真实约束。

## [FINDING_REVIEWER-03]
寻找公共鉴权、过滤、容量契约、部署条件及外部控制等反证。

## [FINDING_REVIEWER-04]
测试路径须检查是否关联实际生产入口；索引无调用方不自动拒绝。

## [FINDING_REVIEWER-05]
不因没有动态复现拒绝静态支持；运行失败与缺陷不存在分开。

## [FINDING_REVIEWER-06]
安全卫生与纵深控制缺失若属于适用要求，作为需求缺陷保留。

## [FINDING_REVIEWER-07]
资源耗尽依业务可用性及攻击成本评估，不全面过滤。

## [FINDING_REVIEWER-08]
弱加密和硬编码秘密须核对具体用途与暴露，不凭关键词定性。

## [FINDING_REVIEWER-09]
拒绝假设性误用、没有证据的额外攻击步骤和纯风格问题。

## [FINDING_REVIEWER-10]
检查缓解措施是否真实有效，不能要求不存在的理想化缓解。

## [FINDING_REVIEWER-11]
引用位置必须核对，保留入口、危险点和需求来源，不只保留单一代码行。

## [FINDING_REVIEWER-12]
条件未知或快照不同输出 NEEDS_EVIDENCE，只有确切反证才 REJECTED。

## [FINDING_REVIEWER-13]
输出 checked_rules 为实际检查的规则 ID 和理由，不能声称执行了未检查规则。
