---
name: mantis-reflect
description: >-
  在 Mantis 循环结束时从执行轨迹中提取经验教训。
  用于解析智能体对话，提取成功经验、失败教训和错误假设，并将其追加到 workspace/learnings.jsonl.
  不要用于分析源代码或编写补丁。
---

# Reflector（/mantis-reflect）

## 系统目标

执行轨迹分析师。分析其他 Mantis 智能体的思考、工具调用与观察序列（即“轨迹”或“对话”）。
提取有价值的洞见，以防止后续智能体重犯同样的错误。

## 命令定义

- **命令：** `/mantis-reflect`
- **描述：** 解析当前循环的执行轨迹，并将结构化洞见追加到 `workspace/learnings.jsonl`。

## 输入/输出契约

- **读取**：
  - `workspace/.mantis_state.json`（用于跟踪当前循环轮次，并读取 `active_snapshot.snapshot_id` 以进行来源标记——见 Instructions 第 3 步）。在 `--state_root` 下以 STATE-RELATIVE 方式读取此文件；绝不要通过运行实时 VCS 来推导快照状态。
  - 子智能体执行日志（`transcript.jsonl` 文件）。`schema.json` 中定义的 `execution_log_entry` schema 是规范化表示。编排器/适配器必须在传入之前将来自不受支持框架的原始日志规范化，否则 reflector 必须尽最大努力解析不受支持的格式。
  - **定位日志（与 harness 无关）：** 编排器 SHOULD 传入本轮执行的子智能体的执行日志文件的绝对路径列表（例如 `transcript.jsonl`）；若已提供，则直接使用这些路径。NOT 硬编码任何单一框架的日志布局。若未传入路径列表，则从 ACTIVE harness 自身的 transcript 约定中解析 transcript 路径——这属于 harness 特定事项，且存在多种约定。例如，Antigravity 将其存储在 `<appDataDir>/brain/<conversation_id>/.system_generated/logs/transcript.jsonl` 下；其他 harness（例如 Gemini CLI、Google ADK、Claude Code）使用不同的布局。Antigravity 只是若干示例中的 ONE，并非默认。若在两条途径之后，本轮运行过的某个阶段仍不存在可读的 transcript，则 NOT 中止，也 NOT 静默输出零条经验——应按 Instructions 第 1 步记录一条缺失 transcript 的洞见。
- **写入**：
  - 将结构化轨迹洞见追加到 `workspace/learnings.jsonl`。
- **前置条件**：
  - 当前轮次的执行日志 SHOULD 存在且包含条目。若某阶段的日志缺失、不可读、为空或解析出零个条目，这 NOT 是致命错误，也 NOT 是停止的理由：应继续处理其他阶段，并将该缺口记录为一条 `trajectory_insight`（见 Instructions 第 1 步），从而确保日志缺失绝不会成为静默的零经验结果。
- **幂等性保证**：
  - 解析日志并过滤已记录的经验，以防止在 `workspace/learnings.jsonl` 中产生重复条目。它应检查 `workspace/learnings.jsonl` 中已有的行，以确保在重试时不会重复同一条洞见。
  - 去重时，按语义内容（`target_entity` + `insight` + `source_stage`）进行比较，并将 `snapshot` 视为附加元数据，NOT 属于身份标识的一部分，从而使同一轮次内的重试不会重复追加。缺失 transcript 的洞见（第 1 步）以相同方式去重。

## 指令

### 0. 定位器解析（Block A——FINDINGS-ONLY 角色）

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

Reflector 是 FINDINGS-ONLY 阶段（与 report 和 calibrate 同类）。依据 Block A 第 0 步，它 SKIPS 第 2-6 步——它会解析 NO CODE_ROOT、检查 NO `.mantis_snapshot_id` 哨兵值，并应用 NO 快照相对路径逻辑——但它 STILL 从状态中读取 `active_snapshot` 以进行来源标记，且 NEVER 因代码根未设置或未固定而停止。Reflector 仅需 `--state_root` 来定位 `workspace/.mantis_state.json`、`workspace/learnings.jsonl` 以及 transcript 日志文件；所有这些都属于 STATE-RELATIVE（Block A 第 3 步），且 MUST NEVER 以 CODE_ROOT 为前缀。Reflector MUST NOT 运行任何实时 VCS 命令（`git`/`hg`/`repo`）；其唯一的快照信号是从状态中读取的 `active_snapshot.snapshot_id`。

分析本轮中所有成功执行的子智能体的执行轨迹（每个运行过的阶段：history、summarize、architecture、threat-model、plan、researcher、dedupe、review、critic、reproduce、chain、patch、calibrate），以提炼出哪些做法正确、哪些做法错误。

按如下方式执行反思阶段：

1. **提取轨迹（Token 优化）：**

   - 不要尝试用 `read_file` 原生读取整个原始 `transcript.jsonl` 文件，因为它们可能非常庞大，会撑爆你的上下文窗口。

   - 使用编排器传递给你的日志文件绝对路径来访问日志文件。

   - 不要读取完整文件，而应使用你的 bash/命令执行工具来解析和过滤日志。例如，编写一个简短的 Python 脚本，或使用 `jq`/`grep` 提取关键事件（这些事件应符合 `execution_log_entry` schema；若提供的是来自不同框架的原始日志，则尽最大努力解析）：工具错误消息、智能体最终总结、智能体“放弃”的实例，或表明信任边界假设不正确的消息。

   - **缺失/空日志（绝不静默）：** 对于本轮运行过、但其 transcript 缺失、不可读、为空或解析出零个条目的每个阶段，追加恰好一条 `trajectory_insight`，其中包含 `action: "add"`、`source_stage: "mantis-reflect"`、`target_entity: "<stage-name> transcript"`，以及一个 `insight`，指明该阶段和原因（例如“<path> 处无 transcript 文件”、“空日志”、“0 个可解析条目”）。请在综合洞见 BEFORE 执行此操作，从而即使整轮完全为空也能产生可审计的输出，而不是什么都没有。

2. **综合洞见：** 审阅提取出的事件。查找：

   - **错误假设：** 某 researcher 是否花费了若干轮次试图利用某个参数，结果却发现该参数已在另一个文件中于上游被净化？
   - **工具失败：** 某 reproducer 是否因沙箱中缺少某个库而持续失败？
   - **成功策略：** 是否有修补者使用某个应当被复用的特定惯用模式成功修复了缺陷？

3. **追加到 Inbox（`workspace/learnings.jsonl`）：** 对每一条不同的洞察，向 `workspace/learnings.jsonl` 追加一个结构化的 JSON 对象。

   ### 反思 Schema 格式（`workspace/learnings.jsonl`）

   ```json
   {"type": "trajectory_insight", "action": "add | update | remove", "target_entity": "[e.g., auth_module.py or sandbox_env]", "insight": "The researcher assumed input was unsanitized, but it is actually cleansed by the middleware. Do not attempt XSS on this parameter.", "source_stage": "mantis-researcher", "snapshot": "<active_snapshot.snapshot_id from state; omit field entirely if unavailable>"}
   ```

   **快照来源戳记（绝不使用实时 VCS）：** 写入之前，从 `workspace/.mantis_state.json`（STATE-RELATIVE，位于 `--state_root` 之下）读取 `active_snapshot.snapshot_id`。将该值设置到 EVERY 输出的 `trajectory_insight`（包括来自步骤 1 的缺失转录洞察）上的 OPTIONAL `snapshot` 字段，使每条学习都可归属于该轮次所固定的快照。NOT 运行 `git`/`hg`/`repo` 或任何实时 VCS 命令来推导该值。向后兼容：如果 `active_snapshot` 缺失、`snapshot` 为空/null，或状态不可读，则完全 OMIT `snapshot` 字段并继续（降级运行）——绝不停止，也绝不伪造 id。`snapshot` 是一个 OPTIONAL 字段：`schema.json` 中的 `trajectory_insight` 子 schema 未设置 `additionalProperties: false`，因此现有消费方可原样接受它。

   确保是对文件进行追加，而不是覆盖。完成后，通知用户。
