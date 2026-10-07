# Mantis 技能与运行机制核对

本次核对基于本地 `google/mantis` 提交 `17348b24179d8a29e33141927153d48ef3b96546`。已逐项阅读全部 19 个顶层 `SKILL.md`、两个配置与启动技能及五份引用资料，并对照默认工作流、提示词绑定、评测入口、工具、静态环境和输出校验代码。以下结论针对这个提交。

接入方案更新：代码与漏洞部分直接使用 Mantis 的核心机制，具体边界见 [直接接入决策](mantis-static-parity.md)。本文关于上游源码的核对结论继续有效；“本项目处理”和 12 个自定义角色的改写方案属于此前设计，不能据此继续独立重写代码审计流程。需求检查保持独立判定，静态执行差异显式记录。

技能正文描述了审计方法、输入输出和证据规则；ADK 负责运行；工具和领域代码承担权限、校验及状态提交。复用清单必须同时覆盖这三部分。本文是源码核对和设计修订记录，尚未通过 DeepSeek 实跑验证分析效果。

## 1 已读技能及实际方法

| 技能原文 | 读到的关键机制 | 本项目处理 |
| --- | --- | --- |
| [mantis-history](../src/security_auditor/vendor/mantis/skills/mantis-history/SKILL.md) | 版本历史、差异与缓存；区分当前仓库和固定快照；处理浅克隆、历史改写、无 VCS | 提取快照与增量方法；保留配置变化，不照搬配置文件过滤 |
| [mantis-summarize](../src/security_auditor/vendor/mantis/skills/mantis-summarize/SKILL.md) | 自底向上汇总文件和目录，合并历史背景；固定快照模式下有停用约束 | 使用外部证据库中的版本化摘要，不在目标仓库写汇总文件 |
| [mantis-architecture](../src/security_auditor/vendor/mantis/skills/mantis-architecture/SKILL.md) | 代码与研究学习形成关联知识库；验证旧断言、局部重建、归档学习记录 | 改写为代码系统模型与证据关系；摘要不能替代源码证据 |
| [mantis-threat-model](../src/security_auditor/vendor/mantis/skills/mantis-threat-model/SKILL.md) | 从知识库识别攻击者、资产、边界和部署意图；原技能限制只读知识库 | 扩展到需求、设计事实、代码与配置；证据权限明确登记 |
| [mantis-plan](../src/security_auditor/vendor/mantis/skills/mantis-plan/SKILL.md) | 全量与知识库导向模式；调查任务绑定知识库引用；增量扩展、重试和探索任务 | 优先级与必查库存分开；未知依赖扩大范围，预算不足保留未完成任务 |
| [mantis-researcher](../src/security_auditor/vendor/mantis/skills/mantis-researcher/SKILL.md) | 初筛与深挖两波；实际调用链、入口和不变量；词法检索与结构索引合并；独立发现及来源定位 | 保留研究方法；并发由统一预算管理；结构索引只能帮助查找，不能证明不存在调用 |
| [mantis-dedupe](../src/security_auditor/vendor/mantis/skills/mantis-dedupe/SKILL.md) | 当前快照与祖先关系、硬合并与可能重复、发现历史和跨版本谱系 | 同快照、同根因证据才合并；跨快照关联保留回归；相似度只产生候选 |
| [mantis-review](../src/security_auditor/vendor/mantis/skills/mantis-review/SKILL.md) | 独立重读原始代码，逐条评估 13 项分诊规则，追踪攻击者控制与公共缓解措施 | 保留独立复核和逐规则理由；重写忽略安全卫生、纵深控制缺失和资源 DoS 等筛选政策 |
| [mantis-critic](../src/security_auditor/vendor/mantis/skills/mantis-critic/SKILL.md) | 挑战生产可达性、部署前提、构建条件和内存契约；区分快照漂移和真实反证 | 静态证据挑战；缺少运行环境保持未知，不能直接否定设计缺陷或漏洞 |
| [mantis-reproduce](../src/security_auditor/vendor/mantis/skills/mantis-reproduce/SKILL.md) | 隔离副本、公开接口契约、分层复现、实际到达危险点的证据、有限重试 | MVP 不执行；借鉴证据分级，禁止静态结果冒充动态验证 |
| [mantis-chain](../src/security_auditor/vendor/mantis/skills/mantis-chain/SKILL.md) | 前置与后置条件匹配，同快照组合，保留原发现；组成项已复现不代表整条链已复现 | 可借鉴静态攻击路径组合；不宣称整链动态验证，不设独立 MVP 执行阶段 |
| [mantis-patch](../src/security_auditor/vendor/mantis/skills/mantis-patch/SKILL.md) | 隔离修改、漏洞基线、正常输入对照、至少三类有意义的再攻击、回滚和补丁变基 | 自动补丁不进入 MVP；修复建议保留验证不足状态 |
| [mantis-calibrate](../src/security_auditor/vendor/mantis/skills/mantis-calibrate/SKILL.md) | 影响、可能性、暴露面、攻击者起点和可用性；27 项封顶规则；自定义风险分数 | 重写静态评级政策；风险、证据强度、合规重要性分开；不把自定义分数标为 CVSS |
| [mantis-reflect](../src/security_auditor/vendor/mantis/skills/mantis-reflect/SKILL.md) | 根据真实执行日志提取失败假设和调查经验；增量写学习记录 | 保留运行审计；经验必须有来源且不成为当前快照事实，不自动改写规则包 |
| [mantis-report](../src/security_auditor/vendor/mantis/skills/mantis-report/SKILL.md) | 发现谱系、快照和归档汇总；保留历史未关闭发现；按保证级别组织报告 | 确定性汇总；需求缺口、静态发现、未知与未完成项均保留，改写依赖复现的展示政策 |
| [mantis-advise](../src/security_auditor/vendor/mantis/skills/mantis-advise/SKILL.md) | 从实际 SQLite 知识库检索历史威胁和根因，形成变更前建议；工具脚本锚定可信安装路径 | 后续开发建议能力；不直接带入示例代码或假定其对本项目正确 |
| [mantis-structural-index](../src/security_auditor/vendor/mantis/skills/mantis-structural-index/SKILL.md) | 语义索引、类型、AST、标签、正则、词法等能力层次；缓存依赖、覆盖、歧义、分页和降级 | 采用实际实现的能力层次；全覆盖清单与查询分开；未索引不等于未被调用 |
| [mantis-meta-agent](../src/security_auditor/vendor/mantis/skills/mantis-meta-agent/SKILL.md) | 阶段调度、固定快照、可选同步、产物归档和架构反馈循环 | 改为固定工作流；不引入远端同步或无界循环；领域状态和预算由程序管理 |
| [mantis-pipeline-adapter](../src/security_auditor/vendor/mantis/skills/mantis-pipeline-adapter/SKILL.md) | 把技能步骤转成确定性编排、受限工具和最小引用；模型分档；检索与 SAST 种子适配 | 保留分层架构；MVP 用本地 FTS，不引入额外远端嵌入或需要执行目标的阶段 |

另已读 [mantis-configure](https://github.com/google/mantis/blob/17348b24179d8a29e33141927153d48ef3b96546/reference/skills/mantis-configure/SKILL.md) 与 [mantis-launch](https://github.com/google/mantis/blob/17348b24179d8a29e33141927153d48ef3b96546/reference/skills/mantis-launch/SKILL.md)：配置覆盖、能力预检、锚定可信入口、沙箱选择和启动约束。我们的静态执行政策固定，不让启动技能自动放开权限。

五份引用资料均已读：

- [知识库查询适配](../src/security_auditor/vendor/mantis/skills/mantis-pipeline-adapter/references/mantis-kb-query.md)：BM25、图关系与有界引用，检索不决定审查库存。
- [SAST 种子适配](../src/security_auditor/vendor/mantis/skills/mantis-pipeline-adapter/references/mantis-sast-seed.md)：统一输入、快照、定位、暂定状态和预算。它是适配规范；不能据此宣称默认流程已接入 SAST。限额外的候选必须保留延后记录。
- [结构索引引用入口](../src/security_auditor/vendor/mantis/skills/mantis-pipeline-adapter/references/mantis-structural-index.md)：指向顶层规范，不能据此宣称所有索引后端均已实现。
- [风险校准规则](../src/security_auditor/vendor/mantis/skills/mantis-calibrate/references/calibration_rules.md)：27 项具体条件与封顶规则，必须逐项决定是否适合静态检查及需求缺陷。
- [补丁变基](../src/security_auditor/vendor/mantis/skills/mantis-patch/references/patch_rebasing.md)：快照变化后的重新应用与验证，不能继承旧验证状态。

## 2 技能是否真正进入运行

### 默认 ADK 入口使用阶段提示词

[graph_loader.py](../src/security_auditor/vendor/mantis/core/graph_loader.py) 在构建普通 Agent 时，使用显式 `system_prompt` 文本，或调用 [prompts.py](../src/security_auditor/vendor/mantis/core/prompts.py) 中的 `get_stage_prompt(node_id, skill_name)`。这个函数按节点 ID 或技能名称别名选择 `STAGE_PROMPTS` 中的压缩指令，**不读取 `SKILL.md` 正文**。`system_prompt` 也只是文字，不能通过填写文件路径加载技能。

默认工作流没有逐节点声明 `skill`。结构索引被特殊节点代码执行；风险校准另有批处理构建路径；部分输出契约、复核约束和不可信输入防护由运行层追加。因此不能从技能文件里的某条规则，直接推断默认运行已执行该规则。

具体差异包括：威胁建模技能要求只读取知识库，压缩提示词允许读取代码；挑错技能保留条件不足与快照漂移的区别，压缩提示词却包含环境缺少前提时判不可行的表述；完整校准技能使用自定义分数，压缩提示词提及 CVSS。复用时需要选定自己的规范并消除冲突。

### 评测入口存在不同绑定

[通用阶段评测构建器](https://github.com/google/mantis/blob/17348b24179d8a29e33141927153d48ef3b96546/reference/evals/stage_agents.py) 找到技能目录后调用 `load_skill_from_dir`，但仅拼接 `skill_obj.instructions[:1000]`。正文前 1000 个字符不能代表研究、校准等长技能的完整规则。补丁角色还引用了本地不存在的 `mantis-coder` 目录，现有顶层技能名为 `mantis-patch`。

[专用去重评测入口](https://github.com/google/mantis/blob/17348b24179d8a29e33141927153d48ef3b96546/reference/evals/deduplicator_agent/agent.py) 从该文件向上三层拼出 `reference/mantis-dedupe`；当前实际目录为仓库根下的 `mantis-dedupe`。因此在当前目录结构下落入后备提示词分支。这里只确认路径与分支，不宣称未触发的技能工具分支已通过 ADK 验证。

我们的生产和评测必须共用同一个 SkillLoader 与 AgentFactory；评测记录需要明确最终加载的技能、规则与指令摘要。

### 工具与代码校验是独立层

[研究工具](../src/security_auditor/vendor/mantis/tools/research_tools.py) 会核对可验证的路径、行号与部分符号引用；无法核对与证据错误有不同处理。[静态环境](../src/security_auditor/vendor/mantis/core/environments/static_env.py) 限制文件范围和执行，[沙箱证据工具](../src/security_auditor/vendor/mantis/tools/sandbox_tools.py) 核对实际到达危险点的信号。它们承担技能文本无法独立保证的约束。

[FindingSchema](../src/security_auditor/vendor/mantis/core/schemas.py) 有枚举及部分跨字段校验，但不能据此认为技能正文所有证据条件均被程序验证。[主入口](../src/security_auditor/vendor/mantis/main.py) 还存在按阶段事件更新状态的逻辑。我们将任务开始、候选产出、证据验证、领域提交和任务成功分别记录，不把进入阶段当作完成。

## 3 不能原样继承的政策

分层执行也是需要保留的方法：[meta-agent](../src/security_auditor/vendor/mantis/skills/mantis-meta-agent/SKILL.md) 要求主控把审计分派给专业阶段，[researcher](../src/security_auditor/vendor/mantis/skills/mantis-researcher/SKILL.md) 在平台支持时再分派两波子任务，补丁技能也会调用独立复现任务进行再攻击。阶段顺序、阶段内任务树与跨轮反馈是不同机制。默认 ADK 阶段图主要提供阶段编排，不能据此认定完整技能方案中的嵌套分派均已实现。本项目将阶段内分片、波次、父子任务关系与汇总明确纳入调度器，具体见实现方案 §10.4。

| 源规则或假设 | 本项目规则 |
| --- | --- |
| 忽略部分安全卫生或纵深控制缺失 | 已适用需求缺失必须进入需求检查；是否为可利用漏洞另行判断 |
| 默认过滤资源耗尽类问题 | 按业务可用性、安全需求和攻击前提评估，不能一概排除 |
| 未复现降级或不进入主要报告 | 不执行目标是产品边界；静态支持、证据不足和动态验证分别标记，不能因未运行就删除问题 |
| 未发现结构调用方作为不可达理由 | 先检查索引覆盖、动态调用与词法线索；缺少调用证据不能自动证明不可达 |
| 已知配置或部署条件默认为当前事实 | 必须绑定当前快照证据；设计声明、观察结果与推断分别记录 |
| 快照不同的相似发现硬合并 | 谱系关联与同快照去重分开；保留每次发现、修复和回归 |
| 部分签名定义不同、仅标题或路径接近 | 自己定义确定性身份规则并测试；不能把粗签名当根因相同的充分证明 |
| 学习记录自动变成知识库事实 | 经验先保留来源与版本；对当前材料重新验证后才能支持结论 |
| 架构目录或图已经建立即代表审查完毕 | 建索引、读材料和安全检查分别统计覆盖 |

Mantis 中研究、复核和再攻击的方法值得复用；漏洞奖励导向的取舍不自动成为本项目的需求与合规政策。

## 4 本项目技能层的具体契约

每个 Agent 节点绑定受控角色、`skill_id` 和固定版本。角色定义输入输出与工具；技能包定义分析步骤、必选规则、反证方法和完成条件。工作流决定调用哪个角色和技能，模型不能扩大工具权限或自行改写工作流。

拟议技能包结构如下，实际文件在 P0/P1 建立：

```text
skills/<skill_id>/
  manifest.json          ID、版本、来源、摘要、规则清单、兼容 Schema 和工具
  SKILL.md               目标、步骤、证据标准、必选规则、输出与停止条件
  references/            可按受控资源 ID 读取的解释与例子
  cases/                 正例、反例、未知和增量回归样例
```

`SkillLoader.resolve(role, skill_id, version, profile)` 只读取注册的本地只读包。启动核对摘要、引用与版本；不接收来自设计文档、目标仓库或模型输出的技能路径。目标代码中的同名 `SKILL.md` 属于被审计材料，不能成为运行指令。

`InstructionCompiler` 将完整必选规则块、输出和停止契约、不可信输入政策及可读资源目录编译成指令。不同任务可加载版本化的专项规则模块；选择规则及理由写入任务账本。附加参考资料可按需读取，必选规则不能只放在可选引用里。

如果必选规则超过上下文预算，拆分任务或选用已评测的等价规则模块；不按前 N 个字符截断。必选块缺失、摘要不符或绑定不兼容时，阻断受影响任务并报告原因，其他独立任务继续。记录规则已加载不等于证明模型遵守，必须用有预期答案的样例测行为。

每个任务保存：技能 ID/版本/内容摘要、必选规则 ID、实际指令摘要、加载的参考资源与专项模块、编译器版本、模型配置档、输入快照及输出 Schema 版本。恢复时版本不匹配创建新运行；不把旧检查结果标成使用新规则完成。

跨字段状态、证据引用、覆盖库存、去重、风险数值计算、预算和权限限制由领域代码执行。模型对规则给出证据和适用性解释；仅有“检查过”的自然语言不能满足程序门槛。

## 5 12 个角色如何继承方法

| 拟议技能 ID / 角色 | 来源与新增方法 |
| --- | --- |
| `design_analyst` | 架构技能中的模块、资产、数据流与边界方法；新增文档块、图形、条件、冲突和提取质量规则 |
| `requirement_generator` | 新增可验收需求推导；借鉴证据定位和三值条件处理；不让代码现状删除需求 |
| `pci_mapper` | 新增逐条标准库存、适用条件与映射规则；检索只选择上下文 |
| `requirement_reviewer` | 借鉴独立复核；新增规范性来源、范围、冲突、覆盖和自动修订规则 |
| `code_architect` | architecture、summarize、history、structural-index 的系统理解、来源与失效方法 |
| `threat_modeler` | threat-model 的攻击者、边界、资产和部署意图；加入设计与实现偏离及关联需求 |
| `requirement_checker` | 新增验收项 × 入口检查；借鉴研究与复核的调用链、公共控制和反证方法 |
| `vulnerability_planner` | plan 的威胁优先级、增量扩展和调查任务；所有计划项有完成或未完成记录 |
| `vulnerability_researcher` | researcher 的两波调查、调用链、不变量与代码证据；保持静态边界 |
| `finding_reviewer` | review 的独立读证据与逐规则评估；应用本项目需求缺陷和漏洞的不同判定政策 |
| `finding_critic` | critic 的攻击条件、生产可达性与反证；缺证不等于误报 |
| `risk_calibrator` | calibrate 的影响与暴露面解释；采用本项目静态政策和确定性计算，不沿用动态复现封顶 |

去重、结构索引、报告和日志记录作为领域服务，不因上游有同名技能就新增 Agent。若后续引入反思或攻击链模块，仍必须受预算、快照与证据政策约束。

## 6 验证与后续提取门槛

本次上游轻量检查已通过：技能完整性四项测试、技能内工具脚本路径锚定检查。原 Mantis 文件保持未修改。这些检查只验证文件与引用，不验证模型分析质量。

P0/P1 必须额外验证：

1. 生产与评测使用相同加载器，所有 Agent 节点都有显式技能绑定，缺失技能不能悄悄退回通用提示词。
2. 每个技能必选规则进入最终指令，规则顺序和内容摘要可复查，长技能不会静默截断。
3. 同一缺陷没有动态运行时仍可得到正确静态状态；无运行证据不能产生动态验证或补丁已验证状态。
4. 公共鉴权、动态调用、配置条件和快照变化的反例正确处理；旧证据不会直接否定新发现。
5. 需求缺失、漏洞、适用性未知与外部证据缺口分别保留，不因去重、评级或报告过滤而丢失。
6. 恶意材料不能加载技能、改变模型端点、取得写权限或启动目标程序。

实际提取时为代码和技能方法分别记录来源提交、原路径、改写理由、许可证与回归样例。迁移完成以规则行为与运行边界验收为准，不能仅以目录复制或 Agent 数量判定。
