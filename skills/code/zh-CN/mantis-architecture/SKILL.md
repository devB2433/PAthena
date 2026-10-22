---
name: mantis-architecture
description: >-
  将原始学习记录与代码库分析综合为相互链接的 Markdown 知识库（KB）。
  在循环开始时使用，以构建或更新 architecture.md、实体和漏洞。
  不要用于生成威胁模型或制定执行计划。
---

# Architect（/mantis-architecture）

## 系统目标

知识库综合器。将来自学习队列（`workspace/learnings.jsonl`）的临时性洞察以及对代码库的结构分析，转化为规范化的、相互链接的 Markdown 知识库（`workspace/kb/`）。

## 命令定义

- **命令：** `/mantis-architecture`
- **描述：** 通过定义系统架构、映射具体实体（组件）并对历史漏洞模式进行分类，构建 KB 的基础。
- **参数（均为可选；由 LOCATOR RESOLUTION / Block A 解析）：**
  `--snapshot_root=<dir>`（或 `SNAPSHOT_ROOT` 环境变量）——用于读取目标源码的固定代码快照；`--snapshot_id=<id>`——该快照的 `SNAPSHOT_ID`；`--state_root=<dir>`——`workspace/` 的父目录，用于所有状态路径和 KB 路径；`--target_root=<dir>`——一个已准备好的目录树，它 OVERRIDES 该快照（很少传给此阶段）。当未传入任何参数时，行为与当前逐字节一致（降级/未固定）：从当前目录读取源码，并将 `snapshot_pinned` 视为 false。

## 输入/输出契约

- **读取**：
  - `workspace/learnings.jsonl`（本轮次的原始洞察）。
  - `workspace/historical_learnings.jsonl`（可选，过往漏洞元数据）。
  - 代码库目录结构和关键源文件。
  - `workspace/kb/` 中已有的 Markdown 文件（用于校验/衰减检查）。
  - `workspace/.mantis_state.json`（用于获取 pass 计数）。
  - `workspace/.mantis_state.json` → `active_snapshot`（`root`、`snapshot_id`、`snapshot_pinned`）以及 `snapshot_history`——用于 KB 新鲜度门禁（步骤 0b）的来源信息。仅从 STATE ONLY 读取快照；NEVER 运行实时 VCS 命令（`git`/`hg`/`repo`）来判断 KB 的时效性。
  - `workspace/.mantis_state.json` → `kb_snapshot_id`（当前 KB 上次构建时所依据的 `SNAPSHOT_ID`；在首次/旧版 KB 中不存在）。
  - `workspace/.mantis_state.json` → `changed_files` 和 `changed_files_status`（由 mantis-plan 的 Block E 写入；由步骤 0b 结果 3 中的限定范围 KB 失效处理消费。从第 2 轮起存在，但可能已过期（在先前轮次中写入）——限定范围路径会将 `changed_files_pass` 与 `state.pass_number` 进行比对，若二者不同则回退到完整重建。）
- **写入**：
  - `workspace/kb/` 下的 Markdown 文件（`architecture.md`、`entities/[component_name].md`、`vulnerabilities/[CWE-ID].md`、`index.md`）。
  - `workspace/kb/dependencies.json`——在架构分析期间提取的导入/依赖边的 JSON 映射（键 = 相对于 CODE_ROOT 的源文件路径；值 = 导入/依赖该键文件的文件数组）。它由 `mantis-plan` 的依赖感知扇出（Phase 2）消费。若代码库没有可解析的导入结构，则写入 `{}`。在限定范围失效处理期间仅重新推导发生变化的条目（见下文结果 3）。
  - 将 `workspace/learnings.jsonl` 归档到 `workspace/archive/learnings/learnings_pass_${N}_${X}.jsonl`。
  - 在每个（重新）写入的 KB 文件（`index.md`、每个 `entities/*.md`、每个 `vulnerabilities/*.md`）的 FIRST 行写入 `<!-- KB_SNAPSHOT: <SNAPSHOT_ID> -->` 标记，并在 `workspace/.mantis_state.json` 中写入 `kb_snapshot_id` = `SNAPSHOT_ID`。
  - 将整个 KB 目录树按轮次复制为不可变副本到 `workspace/archive/kb/kb_pass_${N}_${X}/`（这样后续被回退的修复无法悄悄抹去 KB 在第 N 轮所声称内容的记录）。
- **前置条件**：
  - `workspace/learnings.jsonl` 必须存在。
- **幂等性保证**：
  - 事务性：仅在以程序方式验证所有 KB Markdown 更新均已成功写入后，才将 `workspace/learnings.jsonl` 移动到归档。KB 文件就地覆盖。
  - 快照标记属于同一事务：`KB_SNAPSHOT` 标记、按轮次的 `workspace/archive/kb/kb_pass_${N}_${X_kb}/` 副本以及 `kb_snapshot_id` 状态写入均在 learnings 移动之前（或与之同时）完成。任何失败时，保持 `workspace/learnings.jsonl` 原样不变。

## 指令

分析代码库和待处理的学习记录，为后续智能体构建永久的、基于 Markdown 的记忆。

按如下方式执行架构阶段：

0. **LOCATOR RESOLUTION（Block A，内联如下）：**

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

这是一个 CODE-READING 阶段（步骤 2 和 4 读取目标源码），因此 Block A 步骤 1-6 全部适用；它 NOT 仅限 findings。在执行下面任何操作 BEFORE，先从状态中解析 `CODE_ROOT`、`SNAPSHOT_ID` 和 `snapshot_pinned`。按照 Block A 步骤 3，所有 `workspace/kb/...` 路径均为 STATE-RELATIVE：在 `state_root/workspace` 下读写它们，NEVER 在 `CODE_ROOT` 下。在 `CODE_ROOT` 下读取所有目标源码。NOT 运行任何 VCS 命令来判断 KB 的新鲜度（Block A 步骤 5 的例外仅适用于 history/diff/blame，本阶段不使用它们）。

0b. **KB SNAPSHOT FRESHNESS GATE（机械性；仅 STATE + KB 标记，NO 实时 VCS）：**

````
- `CUR` = `SNAPSHOT_ID` (resolved by Block A; the empty string if
  `active_snapshot` was absent). `PINNED` = `snapshot_pinned` (false if absent).
- **MODE-OFF short-circuit (3-state rule):** if `active_snapshot` is ABSENT
  in state (no `--sync` was requested — MODE-OFF = today's default), SKIP the
  freshness gate entirely: do a best-effort build/update against `CODE_ROOT`,
  do NOT prepend any STALE banner, and do
  NOT stamp `kb_snapshot_id`. This is byte-for-byte today's behavior. (Only
  HALT and PINNED run the gate below.)
- `KB_ID` = the `kb_snapshot_id` value in `.mantis_state.json` (primary); else the
  text after `KB_SNAPSHOT:` on the FIRST line of
  `state_root/workspace/kb/index.md` if that file exists (secondary fallback,
  for legacy runs without state); else `""` (no prior KB). State is primary so
  the file-marker parsing pitfall (comment-wrapped first line, no `-->`
  stripping) can never strand `KB_ID` with the comment closer and force BUILD
  FRESH every pass.
- Choose EXACTLY ONE outcome by string checks, top to bottom, first match wins:
  1. `PINNED` is false (HALT mode — `active_snapshot` present but unpinned)
     ->  **STALE / HALT.** Do a best-effort build/update against `CODE_ROOT`,
     but PREPEND the STALE banner (below) as the first lines of `index.md`.
     Do NOT claim currency: leave the
     banner in place. Set `kb_snapshot_id` = `CUR` (a `live:` id).
  2. Else `KB_ID` == `CUR` (both non-empty)  ->  **CURRENT.** Do the incremental
     update + decay-check (step 4) as today. Re-stamp `KB_SNAPSHOT: CUR` on
     every (re)written file. REMOVE any STALE banner previously prepended to
     `index.md`.
  3. Else (`PINNED` true AND (`KB_ID` is empty OR `KB_ID` != `CUR`)) ->
     **BUILD FRESH (full or scoped re-architecture).** The pinned code advanced
     since the KB was built (a sync / pass-boundary change), OR the KB is
     unstamped / legacy. Choose full or scoped:
     - **Scoped invalidation (Phase 2 incremental efficiency):** If
       `changed_files_status` is known (not UNKNOWN) AND the KB already has a
       `KB_SNAPSHOT` stamp (KB_ID was non-empty, just different) AND
       `changed_files_pass` equals the current `state.pass_number` (the diff
       is from THIS pass, not a stale prior pass — absent or different →
       treat as UNKNOWN → full rebuild below), attempt a SCOPED rebuild: only
       invalidate KB entries whose source files are in `changed_files`, plus
       their parent-rollup dependents (KB entities that import/reference the
       changed files). Re-derive ONLY those entries from `CODE_ROOT`; carry
       forward all other KB entries unchanged (they were built against the
       same code, just a different snapshot ID). Re-stamp `KB_SNAPSHOT: CUR`
       on every (re)written file.
        - **Parent-rollup (2-hop, matching plan's fan-out):** When
          invalidating a KB entry for changed file F, also invalidate any KB
          entry that REFERENCES F directly (1-hop) AND any entry that
          references a 1-hop dependent of F (2-hop). This matches
          `mantis-plan`'s dependency-aware fan-out (which expands up to 2
          hops), ensuring that a grandchild entity (H imports G, G imports
          changed F) is not carried forward stale and later fed as a
          `kb_reference` while its dependency has changed.
       - **Guardrail:** If ANY uncertainty arises (can't determine which KB
         entries map to which source files, the KB structure is ambiguous, or
         changed_files is empty but KB_ID != CUR), fall back to full rebuild
         below. Never carry forward a stale entry for a changed file.
     - **Full rebuild (Phase-1 fallback):** If KB_ID is empty (no prior KB),
       OR `changed_files_status` is UNKNOWN, OR the scoped invalidation guardrail
       fired, REBUILD every KB file from scratch against `CODE_ROOT`. Do NOT
       carry forward any prior assertion you have not re-derived from
       `CODE_ROOT` this pass. Stamp `KB_SNAPSHOT: CUR`. REMOVE any STALE banner.
- STALE banner (paste verbatim, substituting `<KB_ID>` and `<CUR>`; keep the
  leading `>` on every line so it renders as a visible blockquote):

  ```
  > **STALE KB WARNING——未经重新验证不得信任。**
  > snapshot_pinned=false，或该 KB 是针对不同快照构建的。
  > KB_SNAPSHOT=<KB_ID> 与 active_snapshot.snapshot_id=<CUR> 不匹配。
  > 下文每一项 SECURE/FIXED/NON_VIABLE/SAMPLE_OR_TEST 声明相对于当前代码均为 UNVERIFIED。
  > 信任前须重新验证；NOT 基于此 KB 过滤、跳过或降低工作优先级。
  ```
````

1. **读取收件箱（`workspace/learnings.jsonl` 和 `workspace/historical_learnings.jsonl`）：**

   - 解析 `workspace/learnings.jsonl`（以及 `workspace/historical_learnings.jsonl`，若存在）的内容。提取所有轨迹洞察、已发现的漏洞、可行的崩溃路径以及已验证的补丁。

2. **分析源代码边界：**

   - 检查 **`CODE_ROOT` 下**的目录结构与关键源文件（即由 Block A 解析出的固定快照；按 Block A 第 6 步使用绝对路径，并且 NOT 运行 VCS 命令）。基于仓库内容动态识别系统的核心组件、接口与信任边界。这广泛适用于各个领域：无论是软件系统（例如识别解析器、控制器或网络守护进程）、硬件/RTL 设计（例如识别 IP 模块、JTAG 接口或内存控制器）、基础设施即代码（例如识别云权限、VPC 边界或部署描述符），还是数据/ML 流水线（例如识别数据入口点、模型序列化机制或训练边界）。

3. **构建或更新知识库（KB）：**

   - 使用标准 Markdown 在 `workspace/kb/` 目录中创建或更新文件。严格遵循以下路径：

     - `workspace/kb/architecture.md`：高层数据流、区域定义、系统设计，以及整体可用性/正常运行时间要求（若有文档记录，或可从 systemd、kubernetes 或负载均衡器等配置中推断）。
     - `workspace/kb/entities/[component_name].md`：各组件的具体定义（例如 `auth_module.md`）。必须包含指向相关漏洞类别的链接，并记录已知约束（例如“此模块对输入 X 进行净化”）。记录组件的关键性与可用性要求（如适用，归类为 CRITICAL、STANDARD 或 LOW_CRITICALITY）。在此处纳入轨迹洞察。
     - `workspace/kb/vulnerabilities/[CWE-ID_or_BugClass].md`：对历史上与该代码库相关的缺陷类别（例如 `CWE-79.md` 或 `Memory-Corruption.md`）的描述，包括说明*不应*做什么的示例。
     - `workspace/kb/index.md`：根目录索引，包含指向上述所有已创建文件的链接及 1 行摘要。这是 Planner 将要读取的地图。

   - **重要格式规则：** 使用相对链接交叉引用实体与漏洞（例如 `[Auth Module](entities/auth_module.md)`）。确保所有 Markdown 文件简洁，并聚焦于可操作的安全上下文。

   - **快照标记（在运行新鲜度门禁时，即 HALT 或 PINNED 时，对每个（重新）写入的 KB 文件标记 REQUIRED；在 MODE-OFF 时绝不标记）：** 使 `index.md` 的 FIRST 行、每个 `entities/*.md` 以及每个 `vulnerabilities/*.md` 都恰好为 `<!-- KB_SNAPSHOT: <SNAPSHOT_ID> -->`（用第 0b 步中的 `CUR` 替换；它是 HTML 注释，因此不会渲染）。下一轮的新鲜度门禁（第 0b 步）正是以此检测漂移。**MODE-OFF 门禁（3 状态规则）：** 如果 `active_snapshot` 为 ABSENT（MODE-OFF——未请求 `--sync`），则 NOT 标记每文件 `KB_SNAPSHOT` 标记：`CUR` 为空字符串（`arch:145`：“若 `active_snapshot` 缺失则为空字符串”），因此强制要求的标记将是 `<!-- KB_SNAPSHOT:  -->`（空值）——这是快照时期产生的产物，在 Phase 1 中并不存在。每文件标记 ONLY 由第 0b 步的新鲜度门禁消费，而该门禁在 MODE-OFF 时会被完全跳过（`arch:147-152`：“完全 SKIP 新鲜度门禁……这与今天的行为逐字节一致。只有 HALT 和 PINNED 会运行下述门禁。”）。此 MODE-OFF 门禁与下述新鲜度门禁逻辑及第 0b 步的结果条款一致，后者仅在 HALT/PINNED 结果中提及每文件 `KB_SNAPSHOT: CUR` 标记（CURRENT `:164`、作用域为 BUILD FRESH `:178`、完整 BUILD FRESH `:195`）。

   - **每文件 `KB_SNAPSHOT` 标记是 KB 断言的唯一溯源机制。** NOT 写入逐断言 `(AS_OF:<snapshot>)` 标签——`AS_OF` 重新验证读取器从未构建，且陈旧性防护已由新鲜度门禁（第 0b 步）比较 `kb_snapshot_id` 与 `SNAPSHOT_ID` 提供，再加上逐发现的 `discovery_commit`（由 `mantis-critic` Block B 强制执行）。后来被回退的修复会被这些机制捕获，而不是由逐断言标签捕获。

4. **验证并衰减知识（防止漂移）：**

   - 当代码被打补丁或重构时，知识会变得陈旧。在最终确定 KB 更新之前，对照 **`CODE_ROOT` 下**的源代码（即固定快照——NOT 实时 VCS 查询，也 NOT 实时工作树）抽查现有 `workspace/kb/entities/` 中的断言。在 **BUILD FRESH** 结果（第 0b 步）中，完全 NOT 抽查：丢弃先前的断言，并在本轮从 `CODE_ROOT` 重新推导每个实体。在 **STALE / HALT** 结果中，仅尽力抽查，并且无论结果如何都保留 STALE 横幅。
   - 如果某个实体文件声称某变量未净化（基于旧的学习记录），但当前代码现已包含净化函数（因为补丁已落地），则**删除或更正该过时的学习记录**，在 KB 中。
   - 如果某项学习记录被当前轨迹洞察反复证明是错误的，则主动更正它，以防止“错误的学习记录”持续存在并使未来的智能体失明。
   - 当你更正或重新确认某项源自发现的断言时，请在该 KB 文件上重新标记 `KB_SNAPSHOT` 标记。

5. **事务性清空收件箱与归档：**

   - 为防止无限循环和 token 膨胀，你必须清空队列并归档学习记录。
   - **验证并最终确定：** 以程序化方式验证所有 Markdown KB 更新均已成功写入磁盘，且交叉引用有效。在提交之前，还要验证每个（重新）写入的 KB 文件 BEGINS 其 `<!-- KB_SNAPSHOT: <SNAPSHOT_ID> -->` 标记，并且每个源自发现的判定都基于当前 `SNAPSHOT_ID`。
   - **通过移动来提交：** 仅在验证综合成功后，才将 `workspace/learnings.jsonl` 移动到归档目录：
     - 确保目标目录存在（例如 `mkdir -p workspace/archive/learnings/`）。
     - 通过从 `workspace/.mantis_state.json` 读取 `"pass_number"` 来确定循环轮次编号 `N`。如果该值缺失或无效，则扫描 `workspace/archive/` 中匹配 `findings_pass_N` 或 `loopN_findings` 的文件夹，并将 `N` 解析为 `max_found + 1`；若不存在任何归档，则默认为 1。
     - 通过统计 `workspace/archive/learnings/` 中匹配 `learnings_pass_${N}_*.jsonl` 的现有文件数量并加上 1，来确定子索引 `X`。
     - 移动该文件：`mv workspace/learnings.jsonl workspace/archive/learnings/learnings_pass_${N}_${X}.jsonl`。
   - **对 KB 做快照（按轮次归档）：** 在成功完成综合之后，将整个 KB 目录树复制到按轮次的归档中：
     - 确保该目录存在（`mkdir -p workspace/archive/kb/`）。
     - 计算子索引 `X_kb` =（`workspace/archive/kb/` 中现有 `kb_pass_${N}_*` 目录的数量）+ 1。
     - 复制（NOT 移动——活动的 `workspace/kb/` 必须保留以供下一轮次使用）：`cp -a workspace/kb/. workspace/archive/kb/kb_pass_${N}_${X_kb}/`。
   - **写入状态标记：** 将 `kb_snapshot_id` = `SNAPSHOT_ID`（`CUR`，来自步骤 0b）写入 `workspace/.mantis_state.json`。如果 `active_snapshot` 缺失（MODE-OFF），则 NOT 写入 `kb_snapshot_id`，并且 NOT 前置任何 STALE 横幅（新鲜度门控已被跳过）。在 HALT 模式下，写入 `kb_snapshot_id` = `CUR`，并将 STALE 横幅保留在 `index.md` 中。
   - 如果综合失败或被中断，则将 `workspace/learnings.jsonl` 原样保留在其原始位置，以确保不丢失任何数据。
   - 如果进入时 `workspace/learnings.jsonl` 为 ABSENT（例如 Stage-15 调用，因为 Stage-2 调用已在本轮次将其归档），则 ONLY 跳过 learnings 移动；STILL 运行新鲜度门控（步骤 0b）、写入 `KB_SNAPSHOT` 标记、写入 `kb_snapshot_id`，并复制按轮次的 KB 归档。KB 来源信息必须在每次调用时记录。

完成后，通知用户。
