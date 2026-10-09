---
name: requirement_checker
version: 0.1.0
---

# requirement_checker

本技能用于安全需求与纯静态代码分析。方法参考 google/mantis 的 mantis-researcher，已按本项目证据、模块和合规边界改写。

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

## [REQUIREMENT_CHECKER-01]
本任务只检查 scope 中的唯一 requirement_id。所有 assessment 必须使用该确切 ID，按 acceptance_criteria 逐项返回且每项仅一次，保持验收条件原文。不得检查或创建其他需求，也不能用另一条需求的同名验收项替代。

## [REQUIREMENT_CHECKER-02]
设计覆盖与代码实现状态分开；文档说启用不能证明代码已实现。

## [REQUIREMENT_CHECKER-03]
从入口沿服务、公共中间件、数据层和配置寻找保护及反证。

## [REQUIREMENT_CHECKER-04]
缺少局部检查不证明控制缺失，先找共享控制或外部责任。

## [REQUIREMENT_CHECKER-05]
STATIC_SUPPORTED 需要覆盖目标入口的原始静态证据；词法检索无结果不能证明缺失。

## [REQUIREMENT_CHECKER-06]
确认代码控制违反生成 IMPLEMENTATION_GAP；明确设计冲突生成 DESIGN_GAP；代码调用链、共享控制或检查覆盖不足使用 UNKNOWN 并说明已查范围和缺口。已确认存在、但依赖实际部署、运营记录或发布文档的安全要求使用 NOT_CODE_VERIFIABLE（无法通过代码验证），说明为何源码不足以证明。不能因为无法通过代码验证就生成缺陷。本任务生成的 finding 必须令 requirement_ids 等于 [scope.requirement_id]，保持需求与缺陷的关联。

## [REQUIREMENT_CHECKER-08]
上游已自动排除与当前代码范围无关的合规控制。status=UNDETERMINED 或带 applicability_conditions 的需求不能变成“已正式适用”或“已合规”。适用范围是需求的前提，不是产品代码的验收项。根据代码逐项检查相关控制的实际行为；代码机制与实际部署要求分开判断：代码检查有依据则给出对应静态状态，只有实际部署或运营部分使用 NOT_CODE_VERIFIABLE。范围未知不替代代码检查，也不因仓库没有部署材料就判为代码漏洞。不要再输出 EXTERNAL_EVIDENCE_REQUIRED。

## [REQUIREMENT_CHECKER-07]
source_inventory 表示本次快照实际提供的材料数量。存在代码时，不得仅凭设计文本或一次检索无结果就声称“快照只有文档”。先通过 get_mantis_model 获取模块线索，再使用 find_symbol、代码文件路径或函数名检索原始实现；search_evidence 同时匹配源码内容与位置，指定 source_type="code" 可排除文档。沿目标入口、配置校验、共享控制与错误处理读取代码后才能判断。优先批量读取相关块，并继续读取工具返回的 remaining_ids；检索结果与来源清单只提供位置，未读取的材料不得引用。充分搜索仍缺少相关实现时，明确已查范围并保留 UNKNOWN。
