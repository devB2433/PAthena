---
name: mantis-critic
description: >-
  评估发现项在生产环境中的可利用性，过滤掉仅调试可用的特性和断言陷阱。
  当发现项已完成验证，且你需要确认它们在发布构建（断言已禁用）中仍可触发时使用。
  不要用于编写复现脚本或补丁。
---

# Critic（/mantis-critic）

## 系统目标

生产可利用性专家。对已验证的安全发现项进行过滤，确认它们在标准发布配置和生产配置中是否仍可触发。

## 命令定义

- **命令：**
  `/mantis-critic [--target_root=<path>] [--snapshot_root=<path>] [--snapshot_id=<id>] [--state_root=<path>]`
- **描述：** 评估发现项在生产环境中的可利用性，过滤掉仅调试可用的特性和断言陷阱。
- **参数：**
  - `--target_root`：AUTHORITATIVE 目标代码库根目录的路径。覆盖所有其他定位来源，且免于哨兵检查（Block A 路径 1a）。默认未设置。
  - `--snapshot_root`：本次执行所用的固定、不可变快照副本的路径（又称 `SNAPSHOT_ROOT`）。当 `--target_root` 未设置时，用作 CODE_ROOT（Block A 路径 1b）。
  - `--snapshot_id`：编排器为本次执行计算出的 `SNAPSHOT_ID`。用于 Block A 哨兵检查，以及步骤 3 中针对每个发现项的 Block B 漂移比较。若省略，则回退到状态中的 `active_snapshot.snapshot_id`。
  - `--state_root`：Mantis 状态目录根路径，其中包含 `workspace/`（默认为 `.`）。本文件中每个 `workspace/...` 路径都相对于 `--state_root` 解析；使用默认值 `.` 时，这与当前的 `workspace/` 完全相同。

## 输入/输出契约

- **读取：**
  - `workspace/findings/`（加载所有发现项，无论其状态如何；还会读取每个发现项可选的 `discovery_commit`，用于逐发现项的快照匹配检查）。
  - `workspace/kb/THREAT_MODEL.md`（若存在，用于检查部署意图以及 KB 中记录的 `kb_snapshot_id`）。
  - `workspace/.mantis_state.json`（用于跟踪当前循环执行轮次，并读取 `active_snapshot.{root,snapshot_id,snapshot_pinned}` 以进行定位解析和来源追溯）。
  - 解析后的 CODE_ROOT 下的目标源代码文件（当 `snapshot_pinned` 时为固定快照根目录），位于 `code_paths` 中的路径/行，并带上下文偏移。
- **写入：**
  - 就地更新发现项（设置 `"production_viability"`、`"critic_reasoning"`，并追加历史记录）。
  - 追加到 `workspace/learnings.jsonl`。
- **前置条件：**
  - `workspace/findings/` 中必须存在发现项。
- **幂等性保证：**
  - 就地覆盖可利用性字段。必须检查历史数组中是否已记录当前轮次的 critic 条目，并检查 `workspace/learnings.jsonl`，以确保在同一输入上再次运行时不会写入重复记录。

## 指令

评估已验证的发现项，判断它们在经过编译优化的发布构建中是否构成可操作的安全缺陷。**采取高度怀疑、对抗性的立场。不要信任前序阶段的推理。独立重新验证代码路径，以确凿地证明或否定其生产可利用性。**

### 定位解析（首先执行）

Critic 是 CODE-READING 阶段（它会检查目标源代码），因此它 NOT 仅处理发现项的阶段：执行完整的 Block A。在加载发现项之前，按 Block A 解析 CODE_ROOT 和哨兵。

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

> [!NOTE] **CURRENT-PASS CHECK（防御性检查；约束性保证在于 harness，依据 `mantis-pipeline-adapter` 场景 2）：** 如果 `active_snapshot` 存在 AND `active_snapshot.pass != state.pass_number`，则将本次执行的快照视为 STALE——STOP 并报出“stale active_snapshot: pass mismatch”，或按 HALT 降级（`snapshot_pinned` 实际为 false：无权威判定，Block B NOT_MATCHED，复现 `not_attempted`）。此检查可捕获一种自定义 harness：它在 Stage 15 轮次递增过程中保留了 `active_snapshot` 却未重新固定。参考 meta-agent 每轮都会重新固定，因此该检查在那里永远不会触发。Block B 本身无法检测此情况（它只关注 `snapshot_id`，不感知 `pass`）。

**本阶段的 SNAPSHOT_ID：** 令 `SNAPSHOT_ID` 为 `--snapshot_id` 的值（若已提供），否则为 `workspace/.mantis_state.json` 中的 `active_snapshot.snapshot_id`。若两者都不存在（既无 `--snapshot_id` AND 状态中也无 `active_snapshot`），OR Block A 已通过路径 1d（无参数，当前目录）解析出 CODE_ROOT，则 SNAPSHOT_ID 为 UNAVAILABLE（MODE-OFF = 当前默认值）：将步骤 3 中的每个 Block B 检查视为 NOT_MATCHED，并将步骤 2 中的 KB 新鲜度门控视为 FAILED。NOT 停止；按下文所述降级。当 `active_snapshot` IS 存在但 `snapshot_pinned` 为 false（HALT 模式）时，SNAPSHOT_ID 是已记录的 `live:` id 且 IS 可用——Block B 仍返回 NOT_MATCHED（snapshot_pinned 为 false），但下文步骤 3c 的“MODE-OFF 例外”NOT 触发：NOT_MATCHED 结果被视为漂移 → CONDITIONAL_VIABLE，绝不会是 NON_VIABLE（calibrate 会将其丢弃）。

按如下方式执行 critic 评估：

1. **加载发现项：** 读取 `workspace/findings/` 目录中的 JSON 文件。必须加载所有发现项，无论其状态如何（包括 `"VALID"`、`"FALSE_POSITIVE"`、`"PROVISIONALLY_VALID"` 和 `"NEEDS_RESEARCH"`），以便它们可被处理或记录到长期记忆。若不存在任何发现项，通知用户。

2. **评估全局仓库意图（受 KB 新鲜度门控）：** 读取 `workspace/kb/THREAT_MODEL.md`（若存在）。检查 **Deployment Intent** 部分。

   **KB 新鲜度门控——在任何整体批量标记之前 REQUIRED（3 状态规则）：**
   确定 KB 所基于的快照。从以下来源中的 ONE 读取（按顺序尝试，首个匹配者胜出）：

   1. `workspace/kb/THREAT_MODEL.md` 的 FIRST 行上的字面 `KB_SNAPSHOT:` 标记（threat-model 将其写为裸标题；architecture 在每个 KB 文件上写入注释包裹的 `<!-- KB_SNAPSHOT: ... -->`）。对于 architecture 文件，在注释内扫描 `KB_SNAPSHOT:` 子串。NOT 查找 `kb_snapshot_id:` 或 `Snapshot:`——这些标记从不会被写入，门控将永远无法匹配。
   2. 否则取 `workspace/.mantis_state.json` 中的 `kb_snapshot_id` 值（architecture 在其状态戳记步骤中写入该值）。
   3. 否则为 `""`（无先前 KB 来源）。下文整体批量标记的门控如下：

   - **MODE-OFF**（状态中没有 `active_snapshot`——没有 `--sync`）：SKIP 整个新鲜度门禁。与今天一样，允许进行一刀切的 `SAMPLE_OR_TEST` 批量标记（与今天的行为逐字节一致）——KB 是针对实时树构建的，没有可供比较的快照。
   - **HALT 或 PINNED**（`active_snapshot` IS 存在）：仅当已记录的 `KB_SNAPSHOT:`（或 `kb_snapshot_id`）存在，且 AND 与上文 Locator Resolution 中解析出的当前 `SNAPSHOT_ID` 逐字节完全相等（不做模糊比较）时，才允许执行整体批量标记 ONLY。若该记录缺失、为空，或与 `SNAPSHOT_ID` 不相等，则你 MUST NOT 执行批量标记：完全跳过此整体操作，并在步骤 3-5 中逐条评估每个发现项。

   仅当新鲜度门禁通过（或在 MODE-OFF 中被跳过）时：如果威胁模型明确声明整个仓库仅是一个教程、示例项目或测试套件（例如 `Intent: SAMPLE_OR_TEST_ONLY`），则无论这些发现位于文件结构中的何处，你 MUST 将所有发现标记为 **`SAMPLE_OR_TEST`**，并跳过其余逐条发现的可行性检查。

3. **获取目标代码片段（与快照匹配）：** 对于每个 `status` 为 `"VALID"` 或 `"PROVISIONALLY_VALID"` 的发现（对于 `"FALSE_POSITIVE"` 或 `"NEEDS_RESEARCH"` 的发现，跳过本步骤及后续评估步骤）：

   a. **解析目标文件**：依据 Block A 步骤 3，从发现的 `code_paths` 中解析目标文件：`code_paths` 是 SNAPSHOT-RELATIVE，因此在 CODE_ROOT 下读取它们。去掉末尾的 `:<digits>` 以得到行号；`://` 表示一个 URL，而不是文件；任何不是 `<path>:<int>` 的条目都是非源码 LOCATOR——只做存在性检查，不进行行号逻辑处理。

   b. **快照匹配检查（Block B）：** 通过将该发现的 `discovery_commit` 与当前 `SNAPSHOT_ID` 进行比较，计算该发现的 MATCHED / NOT_MATCHED。

   ```
   SNAPSHOT MATCH CHECK for finding F (decides MATCHED vs NOT_MATCHED):
   1. If snapshot_pinned is false -> NOT_MATCHED. Stop.
   2. Read F.discovery_commit:
      - missing OR empty OR the literal "MIXED" -> NOT_MATCHED.
      - not exactly equal to SNAPSHOT_ID          -> NOT_MATCHED.
      - exactly equal to SNAPSHOT_ID              -> MATCHED.
   There is no other route to MATCHED; never fuzzy-compare. The global "default the
   field and proceed" backward-compat rule does NOT apply to discovery_commit:
   absent = NOT_MATCHED. (There is NO separate "dirty" gate: a dirty tree's
   SNAPSHOT_ID already embeds the working-tree content hash, so within-pass findings
   MATCH and cross-pass bare-commit findings do not.)
   ```

   c. **漂移 / 文件缺失 / 行号越界防护（故障安全——NEVER NON_VIABLE）：** 如果 Block B 得出 **NOT_MATCHED**，OR 解析出的目标文件在 CODE_ROOT 下不存在，OR 指定行号超出文件末尾（越界），那么你 MUST NOT 对该发现运行领域特定的可行性分析（步骤 4-5），并且你 MUST NOT 将其标记为 `NON_VIABLE`（文件缺失不是死代码；`NON_VIABLE` 才是 `mantis-calibrate` DROPS 的值）。

   **例外（仅 MODE-OFF——没有 active_snapshot）：** 如果 SNAPSHOT_ID 为 UNAVAILABLE（MODE-OFF：状态中没有 `active_snapshot`，也没有 `--snapshot_id`），AND NOT_MATCHED 的唯一原因是 SNAPSHOT_ID 缺失/不可用（而不是文件缺失或行号越界），则将其视为“无法比较”，并转入正常评估（步骤 3d）。这保留了非 sync 运行下今天的行为。在 HALT 模式下（`active_snapshot` 存在，`snapshot_pinned=false`），此例外 NOT 触发：Block B 为 NOT_MATCHED，该发现属于漂移 → CONDITIONAL_VIABLE（下方的“Otherwise”分支），绝不会是 NON_VIABLE（calibrate 会丢弃该值）。无论是否固定或处于何种模式，文件缺失和行号越界条件仍会强制产生 CONDITIONAL_VIABLE。

   **否则（漂移、文件缺失或行号越界）：** 设置 `production_viability` = **`CONDITIONAL_VIABLE`**，并写入一条 `critic_reasoning` 漂移说明，指明原因，例如：`"Snapshot drift: discovery_commit=<disc> != active SNAPSHOT_ID=<id> (or the target file/line is no longer present at the pinned snapshot); could not re-verify viability, defaulting to CONDITIONAL_VIABLE (conservative)."` 然后通过步骤 6 记录该更新，并继续处理下一个发现。（该发现仍会在步骤 7 中作为 `CONDITIONAL_VIABLE` 记录到长期记忆。）

   d. **MATCHED、文件存在、行号在范围内：** 从 CODE_ROOT 读取目标文件，并在指定行号周围读取至少 **15 行前置上下文**和 **15 行后置上下文**。这一针对性窗口对于分析周边结构和宏定义是必要的。此外，检查 `repro_hints` 和 `history` 中由 `mantis-reproduce` 记录的经验性执行遥测数据（例如 `build_profile`、`sanitizers_used`、`assertions_disabled`、`ingress_blocked`）。使用这些经验性执行遥测数据来佐证发布构建的可行性。继续执行步骤 4-5。

4. **评估领域特定的可行性约束：**

   - **对于内存安全缺陷：** 定位受影响缓冲区的分配来源。判断其是否带有安全余量或尾部填充进行分配。如果越界访问被限制在物理填充范围内，则将其标记为 **NON_VIABLE**。
   - **对于逻辑与授权缺陷：** 验证有缺陷的逻辑或被绕过的端点是否在标准生产部署中确实可访问。如果该缺陷依赖于仅调试用的后门、模拟身份验证提供程序或仅测试用的路由，则将其标记为 **NON_VIABLE**。

5. **确定可行性状态：** 为发现分配以下可行性状态之一，以确保我们正确排定优先级：

   - **`NON_VIABLE`**：该缺陷在生产中不可达或被编译排除。这包括：
     - **禁用的断言（内存缺陷）：** 依赖标准 `assert()`、`debug_abort()` 或仅开发用的 panic 来触发崩溃/DoS 状态的缺陷，而 `NDEBUG` 会剥离它们，代码会安全返回。
     - **仅调试功能：** 使用调试标志进行条件编译（例如 `#ifdef DEBUG`）。
     - **被环境控制阻止：** 被标准的、不可配置的生产环境控制所阻止（例如 OS 级权限、内核级沙箱、只读文件系统），且无法绕过。
   - **`SAMPLE_OR_TEST`**：该问题存在于示例代码、测试套件、模糊测试框架或验证框架中。
   - **`CONDITIONAL_VIABLE`**：该缺陷仅在特定的、非默认配置、可选编译器标志或自定义加固选项下可利用，而这些在不同生产环境中可能有所不同。
   - **`VIABLE`**：该缺陷在标准发布/生产构建中完全可触发。

6. **Token 优化的文件更新：** 为尽量减少 LLM 输出 token，**不要在输出中重新输出或手动重写整个 JSON 对象。** 而应使用就地编辑工具（例如用你偏好的语言编写的简短脚本，或 `jq`）以编程方式将新字段追加到现有的 `workspace/findings/<id>.json` 文件中。

   你必须将以下内容追加到现有对象中：

   - 一个 `"production_viability"` 字段（`"VIABLE"`、`"NON_VIABLE"`、`"SAMPLE_OR_TEST"` 或 `"CONDITIONAL_VIABLE"`）。
   - 一个 `"critic_reasoning"` 字段，用于说明你的评估。
   - 向 `"history"` 数组追加一个条目：

   ```json
   {
     "stage": "critic",
     "action": "evaluated",
     "details": "Determined production viability as [VIABLE/NON_VIABLE/SAMPLE_OR_TEST/CONDITIONAL_VIABLE] because [reason]",
     "pass_number": <current_pass_number>,
     "snapshot": "<SNAPSHOT_ID>",
     "timestamp": "<current_iso8601_timestamp>"
   }
   ```

   将 `"snapshot"` 设置为在 Locator Resolution 中解析得到的 `SNAPSHOT_ID`。如果 `SNAPSHOT_ID` 为 UNAVAILABLE（MODE-OFF），则将其设置为 `""`（或省略该键）。不得伪造 id。

7. **追加到长期记忆：** 对于你加载的每个 finding（包括 `NON_VIABLE`、`SAMPLE_OR_TEST`、`CONDITIONAL_VIABLE`、`FALSE_POSITIVE` 和 `NEEDS_RESEARCH`），向名为 `workspace/learnings.jsonl` 的工作区数据库文件追加一行结构化 JSON（使用追加模式）。这可确保验证结果在多次运行之间被记住，帮助 strategist 避免重新扫描它们。

   - **记忆条目格式：**
     `{"title": "[security_flaw_title]", "code_paths": ["[path1:line1]"], "status": "[VIABLE / CONDITIONAL_VIABLE / NON_VIABLE / SAMPLE_OR_TEST / FALSE_POSITIVE / NEEDS_RESEARCH]", "snapshot": "[current SNAPSHOT_ID, or omit in MODE-OFF]"}`

完成后，通知用户。
