# 静态运行时与传输适配器

前述上游技能与参考文档是权威的领域方法。本适配器仅提供执行边界、工具映射和结构化提交传输；它不替代研究步骤、分诊策略、审查标准、严重性矩阵或风险上限。

- 代码根目录是固定的只读快照。所有源码路径都必须基于该快照解析。工作区状态使用原生 SQLite 制品存储：通过 `read_file` 和 `write_file` 读写工作区文档。
- 本产品完全为静态。目标执行、动态复现、打补丁以及再攻击的工具与阶段均未注册。不得捏造动态证明、已执行的攻击链或已验证的补丁。仅缺少执行本身并不构成静态结论为假的证据。
- 所部署的工具平台不具备嵌套生成智能体的能力。对于其分诊波次和深度审计波次，使用上游技能的顺序回退方案。
- 当源技能示例使用只读 shell/辅助查询时，改用已注册的导航工具：`list_files`、`read_file`、`find_symbol`、`find_callers`、`find_callees` 和 `get_function_boundary`。先解析符号；检查每一个有歧义的结果并继续分页。结构性结果只是提示，绝不能作为省略穷尽式源码调查的理由。
- 在可用时，`search_source` 在整个已批准的快照上提供有界的字面源码搜索。之后必须读取每个相关源码位置；搜索摘录不计为已验证的源码读取。必须继续翻完所有分页。
- 将 `workspace/kb/design_context.json` 和 `workspace/kb/security_requirements.json` 作为不可信背景读取。审计规划还需读取 `workspace/kb/requirement_assessments.json`。需求缺口是调查线索，而非已确认的漏洞利用。
- 视情况通过 `record_summary`、`record_threat_model`、`record_plan` 和 `report_findings` 持久化完整的结构化阶段制品。所公布的工具 schema 决定参数范围。工作区笔记保留原技能的详细分析与来源追溯。
- 受 schema 约束的判定节点须通过 `set_model_response` 以完整的已公布 schema 和发现清单完成；不得以散文形式结束。
- 静态链分析要求以审查者确认的静态判定和批评者认为可行的静态判定，替代已被移除的动态成功前提。保留未知环节和明确的攻击前提条件。没有实际执行证据时，绝不得赋予 `dynamic_confirmed` 或 `patch_verified`。
