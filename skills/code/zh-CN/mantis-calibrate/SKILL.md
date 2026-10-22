---
name: mantis-calibrate
description: >-
  基于实证证据与架构影响计算最终风险评分。
  当发现已由前序阶段完整处理，且需要将最终风险评分追加到发现文件时使用。
  不要用于发现新漏洞或编写补丁。
---

# 风险校准器（/mantis-calibrate）

## 系统目标

风险分析专家。依据严格的风险矩阵评估已确认的发现，并综合考虑成功复现与生产可行性，产出最终风险评分（1-10）。

## 命令定义

- **命令：** `/mantis-calibrate`
- **描述：** 基于证据与影响校准发现的风险等级。

## 输入/输出契约

- **读取**：
  - `workspace/findings/*.json`（所有发现文件，用于加载完整流水线状态）。
  - `workspace/kb/THREAT_MODEL.md`（若存在，用于检查威胁边界覆盖与资产关键性）。
  - `workspace/.mantis_state.json`（用于跟踪当前循环轮次，并读取 `active_snapshot` — `{snapshot_id, snapshot_pinned, root}` — 以进行发现溯源与历史标记；缺失 ⇒ 降级/按今日行为处理）。
- **写入**：
  - 就地更新发现文件，写入评分/校准字段（`impact_score`、`likelihood_score`、`availability_tier`、`inferred_exposure`、`attacker_position`、`mantis_risk_score`、`priority`、`sanity_triage_applied`（当 STALE-EVIDENCE 守卫抑制启发式时，可能以 `STALE_EVIDENCE` 开头）、`calibration_checklist`（条目可能带有 `STALE_EVIDENCE:` 原因）、`outrage_commentary`、`executive_summary`）。追加一条 `history` 条目，带有 `snapshot` 溯源信息（参见正文中的 history JSON 模板）。
  - 可复用辅助脚本 `workspace/helpers/append_calibrate.py`。
- **前置条件**：
  - `workspace/findings/` 中必须存在已确认或原始发现。
- **幂等性保证**：
  - 通过用计算出的评分覆盖已有键来就地更新发现。对相同输入多次运行会产生完全相同的输出，且不会产生重复条目。

## 指令

将原始安全发现及其实证结果（复现/补丁）转换为按优先级排序、可操作的风险报告。

按如下方式执行校准：

**定位器解析（仅限发现）。** 校准在计算评分时从不需要读取目标源码，但下文某些启发式可能会重新检查代码；请按每个阶段相同的方式解析代码根。**Block A** 已内联
below:

```
LOCATOR RESOLUTION (before reading ANY target code or artifact):
0. ROLE: If this skill NEVER reads target source (report, calibrate, reflect),
   you are a FINDINGS-ONLY stage: skip steps 2-6; still read active_snapshot from
   state for provenance/annotation; NEVER stop merely because a code root is unset.
1. Determine CODE_ROOT, in this priority order:
   a. If --target_root is passed on THIS invocation, CODE_ROOT = --target_root.
      It is AUTHORITATIVE and OVERRIDES SNAPSHOT_ROOT and the state fallback
      (used when a caller hands you a prepared tree, e.g. a patched shadow).
   b. Else if --snapshot_root (or SNAPSHOT_ROOT) is passed, use it.
   c. Else read state_root/workspace/.mantis_state.json (state_root from
      --state_root if passed, else ./workspace/... relative to the current dir)
      -> active_snapshot.root / .snapshot_id / .snapshot_pinned.
   d. Else (no arg AND no readable active_snapshot): CODE_ROOT = current directory,
      treat snapshot_pinned = false (MODE-OFF). Do NOT stop.
2. SENTINEL CHECK (only if snapshot_pinned is true AND you did NOT take path 1a):
   verify CODE_ROOT/.mantis_snapshot_id exists and equals SNAPSHOT_ID. If missing
   or different -> STOP "snapshot sentinel mismatch". (A --target_root tree (1a) is
   deliberately mutated and is sentinel-EXEMPT.)
3. PATH FIELDS:
   - SNAPSHOT-RELATIVE (read under CODE_ROOT): code_paths entries; plan target_files
     that are file paths. Strip ONLY a trailing ":<digits>". A code_paths entry
     containing "://" is a URL/endpoint, NOT a file read. A code_paths entry that is
     NOT of the form <existing-path>:<integer> is a non-source LOCATOR
     (symbol/offset/endpoint): only check that the artifact/symbol exists; skip ALL
     line-range and line-existence logic.
   - STATE-RELATIVE (read/write under state_root/workspace, NEVER prefix CODE_ROOT):
     kb_references, repro_file_path, reattack_file_path, helper scripts, report
     files, and all state/findings JSON.
4. Never WRITE under CODE_ROOT when snapshot_pinned is true. Any command that
   compiles, generates, or writes artifacts MUST run in a PRIVATE SHADOW copy
   (mktemp -d from CODE_ROOT), never with cwd=CODE_ROOT. Read-only inspection may
   cd into CODE_ROOT.
5. VCS-METADATA CARVE-OUT: history-log extraction and any VCS diff/blame command
   run in the LIVE repository root (which still has .git/.hg/.repo), NOT CODE_ROOT
   (the snapshot copy strips VCS metadata). Do NOT stop merely because CODE_ROOT
   lacks .git/.hg/.repo.
6. Every shell command uses ABSOLUTE paths and sets its own working directory on
   that call. Do NOT assume the working directory persists between calls.
```

> [!NOTE] **CURRENT-PASS CHECK（防御性；约束性保证在于 harness，依据 `mantis-pipeline-adapter` 场景 2）：** 若 `active_snapshot` 存在 AND `active_snapshot.pass != state.pass_number`，则本轮将快照视为 STALE —— STOP "stale active_snapshot: pass mismatch"，或按 HALT 降级（`snapshot_pinned` 实际为 false：无权威判定，Block B NOT_MATCHED，复现 `not_attempted`）。这可以捕获一种自定义 harness：它在 Stage 15 轮次递增过程中保留了 `active_snapshot` 却未重新固定。参考 meta-agent 每轮都会重新固定，因此该检查在那里永远不会触发。Block B 自身无法检测此情况（它仅感知 `snapshot_id`，不感知 `pass`）。

**快照溯源与 STALE-EVIDENCE 守卫（机械性；在评分之前执行）：**

P0. 从 `state_root/workspace/.mantis_state.json` 读取 `active_snapshot`（`{snapshot_id, snapshot_pinned, root}`）（Block A 步骤 0/1c）。NEVER 在其缺失时停止 —— 校准是 FINDINGS-ONLY 阶段（Block A 步骤 0）。

P1. MODE（整个运行只做一次决策 —— 依据 `active_snapshot` 是否存在分支，3 状态模型）：- 状态中 `active_snapshot` 为 ABSENT（未请求 `--sync` —— MODE-OFF = 今日默认）-> MODE = MODE-OFF。**完全按今日方式**评分：按原文运行每个启发式，NOT 计算 PROVENANCE，NOT 发出 `STALE_EVIDENCE`，且 NOT 发出任何 HALT/PINNED 横幅。（向后兼容的默认路径 —— 与今日行为逐字节一致。）- `active_snapshot` IS 存在 AND `snapshot_pinned` 不恰好为 `true`（HALT 模式 —— 工作树发生竞态或无法固定）-> MODE = HALT。`SNAPSHOT_ID` = `active_snapshot.snapshot_id`（一个 `live:` id）。按下文 P2 计算 PROVENANCE，并保守地触发 STALE-EVIDENCE 守卫（就守卫目的而言与 PINNED 相同），因为发现的定位器可能已过期。本轮禁止给出权威判定（VERIFIED_SECURE、failed_to_reproduce、DUPLICATE、FALSE_POSITIVE、NON_VIABLE）。- `snapshot_pinned` == `true` -> MODE = PINNED。`SNAPSHOT_ID` = `active_snapshot.snapshot_id`。完整溯源 + STALE-EVIDENCE 守卫如下。

P2. 在 MODE = PINNED 或 HALT 时，使用 Block B 的比较规则为每个发现 F 计算 PROVENANCE：- `F.discovery_commit` 缺失 OR 为空 OR 字面量 `"MIXED"` -> NOT_MATCHED。- `F.discovery_commit` != `SNAPSHOT_ID`（精确字符串比较，不做模糊匹配）-> NOT_MATCHED。- `F.discovery_commit` == `SNAPSHOT_ID` -> MATCHED。

P3. 当 MODE 为 PINNED OR HALT AND 时，针对 F 的某个逐发现启发式即为 **STALE**
either：- PROVENANCE(F) == NOT_MATCHED，OR - 该发现的 `code_paths` 目标文件在 CODE_ROOT 下 NOT 存在。（通过 Block A 步骤 1 与 3 解析 CODE_ROOT；去除末尾的 `:<digits>`；ONLY 读取固定根，NEVER 读取实时工作树。若完全无法解析 CODE_ROOT，则将该文件视为不存在 -> STALE。）

P4. 当某个启发式对 F 为 STALE 时，NOT 应用它；保留**保守的**（调整前）评分；并确保字面量标记 `STALE_EVIDENCE` 是 `sanity_triage_applied` 的 FIRST 标记（只添加一次，位于任何 UNKNOWN 警告和任何已触发规则列表之前）。四个受守卫的启发式及其确切的 STALE 处理方式为：- **死代码 0.2 乘数**（第 2 节，"资产关键性与可达性"）：NOT 应用 0.2 缩减；使用你原本会使用的乘数。- **`repro_failure`**（第 3 节规则）：NOT 强制 LOW；设置 `calibration_checklist.repro_failure.outcome = "UNKNOWN"`，`reason` 以 `"STALE_EVIDENCE: "` 开头。- **`vague_code_paths`**（第 3 节规则）：NOT 强制 LOW；设置 `calibration_checklist.vague_code_paths.outcome = "UNKNOWN"`，`reason` 以 `"STALE_EVIDENCE: "` 开头。- **`static_confirmation` trace-lift**（第 3 节规则，其"有效的外部堆栈跟踪/sanitizer……"例外）：NOT 应用 trace-lift；KEEP 静态 HIGH 上限生效（`likelihood_score` <= 3，0.8 Hazard 乘数，NOT CRITICAL）。设置 `calibration_checklist.static_confirmation.outcome = "APPLIES"`，`reason` 以 `"STALE_EVIDENCE: trace-lift suppressed; trace/crash-log may predate the active snapshot; "` 开头。

P5. NON-SOURCE 发现项：如果该发现项的 `code_paths` 条目是非源码 LOCATOR（包含 `"://"`，或不符合 `<path>:<integer>` 的形式——依据 Block A 步骤 3：符号 / 偏移 / 端点），则 SKIP 针对它的所有仅适用于源码的启发式分析——死代码、文件路径/导入/调用者层级暴露推断、`vague_code_paths`，以及静态追踪复查——并依据该发现项的 DECLARED 元数据（`attacker_position`、`privileges_required`、`production_viability`、`repro_status`、威胁模型）进行评分。非源码跳过 NOT 标记为 `STALE_EVIDENCE`（这是正常情况，不是漂移）。非源码发现项的 `inferred_exposure` 默认取 `"INTERNAL"`（0.8），除非该发现项/威胁模型另有声明。

1. **加载完整流水线状态：**

   - 从 `workspace/findings/` 目录读取所有 JSON 文件。由于流水线在每个阶段都会向每个发现项文件追加数据，这些文件提供了每个发现项完整历程的全貌（包括其 `id`、复现状态和生产可行性）。
   - **缺失字段回退：** 如果任何发现项缺少可行性字段或复现字段（例如链式发现项），在评分前应用以下回退默认值：
     - 如果 `production_viability` 缺失，则将其视为 `"CONDITIONAL_VIABLE"`。
     - 如果 `repro_status` 缺失，则将其视为 `"not_attempted"`。
   - **快照来源（仅当 MODE 为 PINNED 或 HALT 时）：** 对每个发现项，按前言步骤 P2 计算 PROVENANCE（MATCHED / NOT_MATCHED），并将其记录到暂存区。该值决定第 2 节和第 3 节中 STALE-EVIDENCE 的处理方式（前言步骤 P3–P4）。当 MODE == MODE-OFF 时，跳过此项——按当前方式评分。
   - 从知识库读取 `workspace/kb/THREAT_MODEL.md`（如果存在），以评估组件暴露面、信任边界、资产关键性，以及任何自定义的 **校准覆盖**（例如，应针对该项目提升或定制的特定威胁位置或上限）。
   - **批处理：** 如果需要校准的发现项较多，请将任务拆分为批次（每次处理少量发现项）。如果你有能力调用子代理，请将每个批次委派给子代理并行处理，然后汇总结果。**每个批次/子代理 ONLY 检查固定的快照：** 传入 `--snapshot_root=<active_snapshot.root>`、`--snapshot_id=<active_snapshot.snapshot_id>` 和 `--state_root=<workspace parent>`，以便每个子代理都通过 Block A 解析出相同的 CODE_ROOT。批次执行的任何代码检查（暴露推断、死代码、静态追踪验证）MUST 通过该 CODE_ROOT（固定副本）读取，NEVER 读取实时目录树，并且 MUST 遵守 STALE-EVIDENCE 防护（前言 P3–P4）。仅属于发现项的字段（分数、优先级、历史戳记）无论如何都依据发现项 JSON 计算。

2. **计算风险分数（1-10）：** 对每个唯一的发现项文件，基于以下公式组成部分，以矩阵形式计算实际技术风险分数，其中 **危害 = 影响 + 可能性**：

   - **影响（1-5）：** 使用 CIA 三元组（机密性、完整性、可用性）评估影响，同时严格考虑 **爆炸半径**。
     - 5：机密性（完整数据泄露、根加密/HSM 主密钥泄露）或完整性（系统沦陷，例如由尚未处于可执行代码的有效位置的非特权攻击者实现明确的远程代码执行（RCE））的完全、系统性丧失。MUST NOT 将其用于已具备执行权限的攻击者。
     - 4：一个或多个方面的重大损失。这包括系统性可用性丧失（主要服务的全面中断）或重大数据暴露。
     - 3：中等损失（例如，部分数据暴露、临时或部分系统中断）。
     - 2：轻微损失（例如，轻微信息泄露、局部中断）。爆炸半径仅限于影响*仅单个用户自身数据*的漏洞 MUST NOT 被评高于 2。*例外：* 如果该操作缺乏不可否认性（允许用户合理地否认该操作以实施欺诈或归咎他人），或触发影响其他用户/系统稳定性的副作用，则不应降级。
     - 1：对 CIA 的影响可忽略不计，主要是外观性问题。“代码脆弱”、“缺乏纵深防御”或纯理论性卫生问题类型的发现项 MUST 具有影响分数 1，以确保它们最多被评为 LOW。
     - **安全控制绕过（升级）：** 如果该漏洞直接绕过核心安全控制（例如，身份验证、授权、加密签名验证），或破坏某个库的主要安全目的（例如，一个旨在保护密钥集的库允许攻击者控制），则将影响分数至少提升至 **4**（如果导致系统性沦陷则为 **5**），即使直接技术影响看似局部化。
     - *关于所需权限与横向移动的说明：*
       - 如果该发现项需要 **HIGH** 权限（例如，管理权限、管理员到超级管理员的提权），或仅允许从已沦陷状态在内部组件之间进行横向移动/转向，则将其单项影响分数上限设为 **2**，除非该利用导致逃逸容器边界（到宿主节点）或跨租户提权。
       - 如果该发现项需要 **LOW** 权限（例如，标准已认证用户），则将其单项影响分数上限设为 **3**（除非它导致其他租户/用户的系统性沦陷，OR 它直接绕过核心安全控制/库目的，在这种情况下可以更高）。
       - 这些上限适用于*单个*发现项。如果链式分析器（chainer）成功将其串联为利用链（超级发现项），则链本身应基于该链*入口点*（初始步骤）所需的权限级别进行评估。
   - **可能性（1-5）：** 基于已证实的可利用性而非理论难度来评估发生概率。
     - 5：已在野被积极利用，OR 智能体成功生成了可用的、武器化的利用代码（而不仅仅是单元测试）。
     - 4：存在公开的概念验证（PoC），OR 智能体生成了高度可信但仅部分武器化的利用代码。
     - 3：无可用利用代码，但攻击向量易于自动化。
     - 2：理论性且高度复杂（需要本地访问、严格时序）。
     - 1：严格理论性风险，无已知利用路径。
     - **实践可达性修正因子：** 在确定基础可能性之后，如果利用路径依赖于不常见或非默认的使用模式，则将 `likelihood_score` 降低 **1 或 2**（但不得低于 1.0）。此修正适用于以下情况：
       - 特定的受污染参数仅在罕见的 API 调用、不常见的配置字段中，或在野外很少处理的数据格式中，才由攻击者输入填充。
       - 该漏洞需要非标准或仅限管理员的配置，而这些配置在实践中很少启用。
   - **上下文乘数（0.1 - 1.0）：**
     - 如果 `status` 为 **FALSE_POSITIVE** 或 **NEEDS_RESEARCH**，或者 `production_viability` 为 **NON_VIABLE**：完全丢弃此发现项。不要对其评分，也不要用校准数据更新其文件。
     - 如果 `production_viability` 为 **VIABLE**、**CONDITIONAL_VIABLE** 或 **SAMPLE_OR_TEST**：
       - **网络/信任暴露：**
         - 如果该发现项位于**暴露接口/信任边界**内（可直接被不可信输入访问）：1.0。
         - 如果它位于接受半可信解析数据的**内部组件**中：0.8。
         - 如果它深度嵌套在**特权/可信区域**内：0.5。
         - **威胁模型缺失/不完整时的推断：** 如果 `workspace/kb/THREAT_MODEL.md` 不存在，或未提及
           component:
           - 分析文件路径、导入和调用者层级以推断暴露程度（例如，公共 API 与内部辅助函数）。**只读取已固定的 CODE_ROOT（Block A），绝不读取实时目录树。NON-SOURCE（前言 P5）：对于非源码 LOCATOR 发现项，跳过此文件路径/导入/调用者分析——除非该发现项或威胁模型另有声明，否则将 `inferred_exposure` 默认设为 `"INTERNAL"`（0.8）。** 当 MODE 为 PINNED 或 HALT，且该发现项为 NOT_MATCHED 或其 `code_paths` 文件在 CODE_ROOT 下不存在时，NOT 基于实时重新检查将暴露乘数降低到所声明/默认值以下（保留保守的、较高的乘数）。
           - 除非有明确证据表明存在直接外部暴露（EXPOSED）或深度嵌套隔离（PRIVILEGED），否则将暴露乘数默认设为 **0.8**（内部），并将 `inferred_exposure` 默认设为 `"INTERNAL"`。本地 SUID/LPE 二进制文件应默认采用 `"INTERNAL"` 暴露级别。
           - 如果发现项描述、历史记录或评审推理表明该组件“很少暴露”、“仅限内部”或“不太可能被攻击者触及”，则将暴露乘数降低至 **0.5** 或更低。
       - **映射暴露与攻击者位置元数据：**
         - 基于网络/信任暴露确定 **`inferred_exposure`**
           multiplier:
           - 乘数 1.0（暴露接口）-> `"EXPOSED"`
           - 乘数 0.8（内部组件）-> `"INTERNAL"`
           - 乘数 0.5（特权/可信区域）-> `"PRIVILEGED"`
         - **评估攻击者位置（在发现项中声明）：**
           - 从发现项 JSON 中读取 `attacker_position`。
           - **按屏障而非传输方式确定：** `attacker_position` 必须表示**第一个不可信主体**（最终的人类攻击者或外部威胁行为者）为到达该接口所必须跨越的最外层边界。不要以传输协议（例如 HTTP、gRPC、IPC）或直接协议对端为依据。
             - **回溯至不可信行为者：** 如果与该接口交互的直接对端是设计上可信的组件（例如内部代理、网关、消息队列或主控制器），则必须回溯数据流，以找到不可信行为者首次进入系统的最外层边界。
             - 如果该接口绑定到 `localhost` 或使用本地 IPC（unix socket、管道、共享内存），则位置为 `"LOCAL"`，即使其底层使用 HTTP/TCP。
             - 如果该接口仅可在私有网络内访问（VPC、企业网络、家庭 LAN、局域网、内部集群控制平面），则位置为 `"INTERNAL_NETWORK"`（如果仅限于 pod 到 pod 通信，则为 `"IN_CLUSTER"`），即使它是一个 Web 服务。
             - 仅当该接口可从公共互联网直接访问时，位置才为 `"EXTERNAL"`。
             - 如果该接口需要物理接触、硬件交互（例如 JTAG、调试探针、芯片开盖）或本地无线近场（例如 NFC、Bluetooth），则无论使用何种协议，位置都必须为 `"PHYSICAL_TEMPORARY"` 或 `"PHYSICAL_LONG_TERM"`。
           - **规范化自由文本：** 如果该值存在，但为自由文本字符串且与任一有效枚举值不完全匹配（例如旧式表述），则你 **MUST** 使用以下映射将其规范化为最接近的有效枚举值：
             - 在公开产品上匹配 `"authenticated <role>"`、`"customer with"`、`"tenant <role>"`、`"Fitbit user"` 的短语 -> `"EXTERNAL"`（并带有 `privileges_required: "LOW"`）。
             - 匹配 `"local user"`、`"local shell"`、`"local access"` 的短语 -> `"LOCAL"`。
             - 匹配 `"peer <role> in same job/cluster/pod"`、`"co-tenant"`、`"in-cluster (Kubernetes/container-orchestrator) workload"`、`"NCCL peer rank"` 的短语 -> `"IN_CLUSTER"`。
             - 匹配 `"malicious dependency"`、`"upstream package"`、`"build-time"`、`"CI pipeline"` 的短语 -> `"SUPPLY_CHAIN"`。
             - 匹配 `"host hypervisor"`、`"host OS"`、`"hypervisor access"` 的短语 -> `"HOST_SYSTEM"`。
             - 匹配 `"physical access"`、`"fault injection"` 的短语 -> 根据 barrier 判定为 `"PHYSICAL_LONG_TERM"` 或 `"PHYSICAL_TEMPORARY"`。
           - 如果完全缺失，则使用以下回退准则进行推断（并记录一条警告，建议更早地声明它）：
             - `"EXTERNAL"`：如果该组件为 `"EXPOSED"`，或者它是公开门户上的认证绕过。
             - `"LOCAL"`：如果它是本地权限提升（LPE）或 SUID 漏洞利用。
             - `"IN_CLUSTER"`：如果它从某个 pod 内部针对集群内基础设施（CSI/CNI）。
             - `"HOST_SYSTEM"`：如果攻击者是 hypervisor、宿主 OS，或某个模拟/物理设备攻击其所托管的软件（guest 驱动、enclave 运行时、固件目标）。此枚举严格用于由外向内方向。反方向——guest 到宿主（VM 逃逸）、沙箱到外部、enclave 到宿主，或受控进程到容器——必须归类为 `"LOCAL"`（对于 Kubernetes pod 到节点则为 `"IN_CLUSTER"`；KVM/hypervisor guest 攻击其宿主属于 "LOCAL"），绝不能归类为 `"HOST_SYSTEM"`。
             - `"PHYSICAL_LONG_TERM"` / `"PHYSICAL_TEMPORARY"`：如果缺陷描述、标题或代码路径表明存在硬件故障注入、侧信道、evil maid，或 USB 物理访问。
             - `"SUPPLY_CHAIN"`：用于构建期或依赖修改类前置条件。
             - `"INTERNAL_NETWORK"`：其他内部组件的默认回退。
           - **使暴露面与位置保持一致：**
             - 如果 `attacker_position` 为 `"LOCAL"` 或 `"IN_CLUSTER"`，则你 **MUST** 将 `inferred_exposure` 解析为 `"INTERNAL"`（使用 0.8 乘数），即使易受攻击的代码路径位于威胁模型中映射为 `"EXPOSED"` 的文件夹内，除非该漏洞利用明确逃逸容器边界到达宿主节点。
             - 如果 `attacker_position` 为 `"INTERNAL_NETWORK"`，则你 **MUST** 将 `inferred_exposure` 解析为至多 `"INTERNAL"`（使用 0.8 乘数或更低），即使该组件在威胁模型中映射为 `"EXPOSED"`，因为该接口无法从公共互联网直接访问。
             - 如果 `attacker_position` 为 `"EXTERNAL"`，则你 **MUST** 将 `inferred_exposure` 解析为 `"EXPOSED"`（使用 1.0 乘数），即使该组件在威胁模型中映射为 `"INTERNAL"` 或 `"PRIVILEGED"`（以反映不可信外部输入可到达该组件）。
       - **资产关键性与可达性：**
         - 如果威胁模型表明该组件处理高价值数据（例如 PII、核心密钥），则保持较高乘数。
         - 如果它影响低价值目标（例如内部分析、沙箱化测试数据），则降低乘数（例如 0.5）。
         - **可用性特定上下文：** 如果该发现仅涉及可用性（DoS），请检查威胁模型中该组件的 `availability_tier`（若缺失，则默认为 STANDARD）：
           - `LOW_CRITICALITY`：将乘数降低至 **0.5**。
           - `STANDARD`：将乘数降低至 **0.8**。
           - `CRITICAL`：将乘数保持在 **1.0**。
         - 如果静态/动态分析证明易受攻击的代码实际上是“死代码”（在运行时执行路径中从未被调用），则将乘数大幅降低至 0.2。**STALE-EVIDENCE GUARD（前言 P3–P4）：如果该发现为 STALE（MODE 为 PINNED 或 HALT AND 该发现为 NOT_MATCHED 于活动快照，`code_paths` 文件在固定的 CODE_ROOT 下不存在），则 NOT 应用此 0.2 缩减——漂移的代码可能虚假地看起来“未被调用”；应保持较高乘数，并将 `STALE_EVIDENCE` 设为 `sanity_triage_applied` 的第一个 token。** 你实际执行的任何死代码分析 MUST 检查固定的 CODE_ROOT，绝不能检查活动代码树。**NON-SOURCE（前言 P5）：** 对于非源码 LOCATOR 发现，完全跳过此启发式规则。
       - **用户交互：**
         - 如果 `user_interaction` 为 **REQUIRED**（例如 CSRF、点击劫持，或说服用户打开恶意文件），则对上下文乘数应用 **0.7** 乘数（例如，如果暴露面为 Internal（0.8）且需要用户交互，则组合乘数为 0.8 * 0.7 = 0.56）。这确保这些发现被限制在 CRITICAL 阈值以下。
     - 如果 `production_viability` 为 **SAMPLE_OR_TEST**：
       - 对上下文乘数应用 **0.4** 缩放因子（即，将当前上下文乘数乘以 **0.4**），使示例代码中的严重缺陷通常落入 MEDIUM 档，而不是 HIGH 或 CRITICAL。此缩放因子必须与其他修饰因子累积叠加应用。不要直接将上下文乘数覆盖为 `0.4`，因为如果该组件的暴露面或死代码状态已被计算为低于 `0.4`（例如 `0.2`），这样做会错误地将其提高。
       - 在 `executive_summary` 中，明确说明这不是生产缺陷。建议 MUST 聚焦于修复示例/测试，以免开发者将不安全模式复制到生产代码中。
     - 如果 `production_viability` 为 **CONDITIONAL_VIABLE**：
       - 对 Context Multiplier 应用 **0.7** 缩放因子（即，将当前 Context Multiplier 乘以 **0.7**），以反映该问题需要特定的非默认配置、编译器标志或启用断言才可被利用。该缩放因子必须与其他修饰因子（例如 User Interaction）一并累积应用。不得直接将 Context Multiplier 覆盖为 `0.7`，因为如果该组件的暴露程度本已很深/隔离（`0.5`），这样做会错误地将其提高。
       - 在 `executive_summary` 中记录可行性所需的特定条件。

   **最终得分（Hazard）= (Impact + Likelihood) * Multiplier**（上限为 10.0）。

   *关于 Outrage 的说明：* 在你的推理中，请对更广泛的等式 **Risk = Hazard + Outrage** 作出评论，其中考虑了“outrage 风险”（例如声誉损害、用户情绪反弹）。*不要*将 outrage 因子纳入最终数值得分。

3. **关键合理性分诊（降级与封顶发现项）：** 在确定最终优先级之前，对发现项的质量、其上下文以及累积的证据执行第二层合理性检查。

   **核心原则 - 边际能力：** 发现项的最终严重性和优先级严格受限于攻击者相对于其先决条件位置所获得的*边际能力*。如果该利用不能赋予攻击者超出其起始位置固有能力（或已通过合法手段拥有）的显著新控制权、访问权或能力，则该发现项必须被封顶或降级。

   27 条校准合理性规则的完整、详细定义存储在参考目录 [Calibration Rules](references/calibration_rules.md) 中。你 MUST 依据其中列出的 27 条规则评估每个发现项。

   检查 `THREAT_MODEL.md` 是否定义了任何 `Calibration Overrides`（例如 `LIFT_CAP: PHYSICAL_LONG_TERM`）。如果存在针对某发现项位置或组件的覆盖项，则该覆盖项优先，并解除相应的封顶。否则，参考目录中规定的封顶与降级（以及边际能力原则的一般应用）将覆盖第 2 节中计算出的任何升级（包括 Security Control Bypass 升级）。你还应应用该一般原则，对提供低边际能力的其他发现项进行封顶或降级。**重要：封顶（HIGH 或 MEDIUM）仅限制允许的最高得分/优先级。它 NOT 会升级较低的得分/优先级（例如，得分为 5.0 的发现项自然属于 MEDIUM，并且必须保持为 MEDIUM，即使它受 HIGH 的封顶约束）。**

   **优先级与 UNKNOWN 规则策略：**

   - **STALE-EVIDENCE 优先级（前言 P3–P4）：** 当 MODE 为 PINNED 或 HALT，且某发现项为 STALE（NOT_MATCHED 活动快照，OR 其 `code_paths` 文件在固定的 CODE_ROOT 下不存在）时，以下规则 MUST NOT 被用于从实时
     re-inspection：`repro_failure`（不要强制 LOW）、`vague_code_paths`（不要强制 LOW），以及 `static_confirmation` **trace-lift 例外**（不要将过期的 trace/sanitizer/crash-log 视为实时复现——KEEP 静态 HIGH 封顶）。同时不要应用第 2 节的死代码 0.2 乘数（见第 2 节）。保留保守得分，并**将字面 token `STALE_EVIDENCE` 作为 `sanity_triage_applied` 的 FIRST token 前置**（置于任何 `Incomplete Calibration (UNKNOWN: …)` 警告之前，以及已触发规则列表之前）。按前言 P4 的规定，在 `calibration_checklist` 中记录受保护的规则（`repro_failure`/`vague_code_paths` -> `UNKNOWN`，并附 `STALE_EVIDENCE:` 原因；`static_confirmation` -> `APPLIES`，并附 `STALE_EVIDENCE:` 原因，注明 trace-lift 已被抑制）。在 MODE == MODE-OFF 时，此条目不执行任何操作（按当前方式评分）。
   - 评估 ALL 规则。如果多个封顶同时适用，则**限制最严格者**胜出（Force-LOW > cap-MEDIUM > cap-HIGH）。
   - **UNKNOWN 结果的策略：** 如果某规则被评估为 `UNKNOWN`，则**不要**应用封顶或降级（保持评分保守；将得分/优先级保持在其较高的计算值）。但是，通过在 `"sanity_triage_applied"` 字符串前添加警告，将整体校准标记为不完整/暂定：`"Incomplete Calibration (UNKNOWN: <rule_name>)"`（如果有多个 UNKNOWN，则为以分号分隔的警告列表）。这表示需要人工审查以确定规则状态。
   - 将每个成功触发/应用的规则记录在 `sanity_triage_applied` 中，以分号分隔的列表形式，限制最严格者在前（例如 `"Local Attack Vector; Internal/Nested"`），追加在任何 UNKNOWN 警告之后（如果存在），以便有效封顶保持完全可审计。

4. **确定优先级：**

   - **CRITICAL（8.0 - 10.0）：** 需要立即行动。极高危害（例如高影响和高可能性）。**必须 NOT 使用，除非它代表无特权攻击者（其 `privileges_required` 为 **NONE**）造成的明确 RCE（或等效的完全损失），且该攻击者尚未处于可有效危害系统的位置，AND `user_interaction` 为 **NONE**（零点击）。此规则是绝对的：即使某发现项（例如 CSI 主机逃逸）的第 3 节封顶已被解除，如果它在入口处需要 HIGH 权限，它 MUST NOT 被评为 CRITICAL，并且必须被封顶为 HIGH（7.9）。仅可用性发现项（DoS）MUST NOT 被评为 CRITICAL，除非 `availability_tier` 在 Threat Model 中被明确记录为 `CRITICAL` AND 没有任何自动恢复机制（例如自动重启、负载均衡器故障转移）可缓解该影响。**
   - **HIGH（6.0 - 7.9）：** 高优先级。显著危害，需要及时解决。
   - **MEDIUM（3.0 - 5.9）：** 标准优先级。中等危害，可安排处理。
   - **LOW（0.1 - 2.9）：** 低优先级。危害极小。**任何属于“代码脆弱”、纯属卫生/纵深防御性质，或仅影响单个用户自身数据的发现，MUST 无论计算得分如何，其优先级都被上限限制为 LOW（除非适用缺乏不可否认性或更广泛副作用的例外）。**

5. **Token 优化的文件更新：** 为尽量减少 LLM 输出 token，**不要在输出中重新输出或手动重写整个 JSON 对象。** 而应在首次更新发现时编写一个可复用的辅助脚本（例如 `workspace/helpers/append_calibrate.py`）。对于后续所有发现，不要重新生成该脚本；只需用新参数执行现有的辅助脚本，将所需字段追加到 `workspace/findings/<id>.json`。

   - **辅助脚本版本标记（强制）。** `append_calibrate.py` 的第一行 MUST 是精确注释 `# MANTIS_HELPER_VERSION = 2`。在复用现有辅助脚本之前，先读取其第一行：如果文件不存在，OR 第一行不完全是 `# MANTIS_HELPER_VERSION = 2`（标记缺失或为不同的整数），则**从头重新生成该辅助脚本**（旧版辅助脚本会静默丢弃新的 `snapshot` 历史字段以及 `STALE_EVIDENCE` 处理）。重新生成的辅助脚本 MUST 能够将 `snapshot` 键写入所追加的 `history` 条目，并将 `STALE_EVIDENCE` 前置到 `sanity_triage_applied`。该辅助脚本位于 STATE-RELATIVE（`workspace/helpers/…`，Block A 步骤 3）——绝不要将其写在 CODE_ROOT 下。

   在现有核心发现数据之外，显式追加以下字段以展示矩阵分解：

   - `"impact_score"` (1-5)

   - `"likelihood_score"` (1-5)

   - `"availability_tier"`（CRITICAL、STANDARD、LOW_CRITICALITY 或 null）

   - `"inferred_exposure"`（EXPOSED、INTERNAL 或 PRIVILEGED）

   - `"attacker_position"`（从输入中保留，若缺失则通过回退推断填充）

   - `"mantis_risk_score"`（最终的 Hazard 得分）

   - `"priority"` (CRITICAL, HIGH, MEDIUM, LOW)

   - `"sanity_triage_applied"`（以分号分隔的列表，或 null）。顺序：若该发现为 STALE（前言 P4），则 `STALE_EVIDENCE` 排在最前；然后是任何 `Incomplete Calibration (UNKNOWN: <rule>)` 警告；然后是触发的第 3 节规则，限制最严格者在前。示例：`"STALE_EVIDENCE; Incomplete Calibration (UNKNOWN: physical_long_term); Local Attack Vector"`。

   - `"calibration_checklist"` 对象，包含对全部 27 项 sanity 上限规则的评估。该对象中的每个键映射到与之匹配的 sanity 上限规则
     name:

     ```json
     {
       "repro_failure": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "unreachable_inputs": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "third_party_reachability": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "minor_config_hygiene": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "non_security_critical": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "vague_code_paths": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "unreliable_triggers": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "prerequisite_shell": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "physical_long_term": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "trusted_controller_zero_delta": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "standard_host_attacks": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "static_confirmation": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "strict_xss": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "internal_nested": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "probabilistic_llm": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "supply_chain_prerequisites": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "non_default_config": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "confidential_computing_host": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "trusted_controller_critical_bypass": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "local_attack_vector": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "self_contained_blast": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "rarely_exposed": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "equivalent_primitives": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "documented_insecure_config": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "physical_temporary": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "high_privilege_external": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" },
       "trusted_controller_standard_bypass": { "outcome": "APPLIES" | "DOES_NOT_APPLY" | "UNKNOWN", "reason": "<string>" }
     }
     ```

     对于每条规则，`outcome` 必须设置为：

     - `"APPLIES"`：如果该 sanity 上限规则适用于（触发于）此发现，对其得分/优先级进行上限限制或降级。**必须**提供详细的 `reason` 字符串。
     - `"DOES_NOT_APPLY"`：如果该 sanity 上限规则不适用。`reason` 字段为**可选**，可省略以优化 token。
     - `"UNKNOWN"`：如果该规则未解决。**必须**提供详细的 `reason` 字符串。

     为向后兼容，schema 也允许 `"fires": <bool>`（仅当 `fires` 为 `true` 时才要求 `reason`），但首选新的 `"outcome"` 格式。

   - `"outrage_commentary"`（你关于 outrage 因素的推理）

   - `"executive_summary"`

   - 向 `"history"` 数组添加一个条目：

     ```json
     {
       "stage": "calibrate",
       "action": "calibrated",
       "details": "Calculated risk score as [score] and priority as [priority].[ STALE_EVIDENCE: scored without trusting live-code re-inspection.]",
       "pass_number": <current_pass_number>,
       "snapshot": "<active_snapshot.snapshot_id, or omit in MODE-OFF>",
       "timestamp": "<current_iso8601_timestamp>"
     }
     ```

将你的更新保存到各个发现文件中。完成后，通知用户。
