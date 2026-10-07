---
name: risk_calibrator
version: 0.1.0
---

# risk_calibrator

本技能用于安全需求与纯静态代码分析。方法参考 google/mantis 的 mantis-calibrate，已按本项目证据、模块和合规边界改写。

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

## [RISK_CALIBRATOR-01]
分别给出业务影响、攻击可能性、暴露面与证据强度。

## [RISK_CALIBRATOR-02]
影响与可能性各为 1 至 5，由代码计算自定义乘积；不得标为 CVSS。

## [RISK_CALIBRATOR-03]
攻击者起点以最外侧实际不可信边界为准，避免把内部代理权限误当攻击者前提。

## [RISK_CALIBRATOR-04]
可用性影响按业务关键性分析，资源耗尽不自动过滤。

## [RISK_CALIBRATOR-05]
没有动态复现不自动封顶为 LOW；静态证据不足降低 evidence_strength，保留风险解释。

## [RISK_CALIBRATOR-06]
需求合规重要性与可利用风险分开；对没有支持的威胁条件明确保留未知。
