---
name: code_architect
version: 0.1.0
---

# code_architect

本技能用于安全需求与纯静态代码分析。方法参考 google/mantis 的 mantis-architecture，已按本项目证据、模块和合规边界改写。

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

## [CODE_ARCHITECT-01]
根据源码与配置识别模块、子系统、入口、共享保护机制和持久化路径。

## [CODE_ARCHITECT-02]
源码观察标为 OBSERVED，与设计 DECLARED 事实分开。

## [CODE_ARCHITECT-03]
按实际证据建立模块关系；目录名不能证明完整的运行边界。

## [CODE_ARCHITECT-04]
优先函数与调用关系；当前词法索引缺少语义调用图时明确局限。

## [CODE_ARCHITECT-05]
配置与依赖变化影响公共控制；避免只看入口文件。
