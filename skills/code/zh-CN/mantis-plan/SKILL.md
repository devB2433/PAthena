---
name: mantis-plan
description: >-
  基于当前生效的威胁模型与历史经验，制定有针对性的防御性安全审查计划。
  在启动安全审查活动、以梳理代码库边界并生成路线图（workspace/plan.json）时使用。
  不要用于执行代码审查、编写测试脚本或修补代码。
---

# 战略规划器（/mantis-plan）

## 系统目标

安全架构师。分析代码结构、目录元数据与历史记录，以梳理外部边界并制定自适应审查路线图。

## 命令定义

- **命令：** `/mantis-plan`
- **描述：** 基于当前生效的威胁模型与历史经验，制定有针对性的防御性安全审查计划。
- **参数（可选；由编排器提供，由 Block A 消费）：**
  - `--snapshot_root` / `SNAPSHOT_ROOT`：指向已固定的只读代码快照的绝对路径（所有相对于快照的路径均以 CODE_ROOT 为基准）。
  - `--snapshot_id` / `SNAPSHOT_ID`：本轮 pass 的快照标识符（用于哨兵检查 + Block B 比较）。
  - `--state_root`：指向 `workspace/` 状态目录的绝对路径（包含 plan.json、.mantis_state.json、findings/、kb/、archive/）。STATE-RELATIVE —— 绝不添加 CODE_ROOT 前缀。
  - 所有标志均缺失 -> MODE-OFF/legacy 模式（Block A 步骤 1d）：行为与当前完全一致。

## 输入/输出契约

- **读取**：
  - `workspace/.mantis_state.json`（用于跟踪当前循环 pass）。
  - `workspace/kb/THREAT_MODEL.md`（若存在）。
  - `workspace/kb/index.md`（检查其是否存在，以确定 Mode A 还是 Mode B）。
  - Mode A：遍历生产目录与源文件，读取 `mantis-summary.md`（若可用）。
  - Mode B：读取 `workspace/kb/index.md`、`workspace/kb/THREAT_MODEL.md`、`workspace/archive/.repro_attempts.json`（若存在）、VCS 差异或文件时间戳/哈希。
  - `workspace/kb/structural_index/manifest.json`（用于检查结构索引的可用性/状态）。
  - `workspace/helpers/query_structural_index.py`（用于调用有界结构索引查询）。
  - `workspace/.mantis_state.json` 的 NEW 字段：`active_snapshot.{snapshot_id, snapshot_pinned, vcs_type}`、`snapshot_history`（读取，由 meta-agent 写入）。读取 `vcs_type` 是因为 Block E 依据它进行分支。Plan 在 LIVE 仓库根目录运行 Block E，以计算 `changed_files` / `changed_files_status`（COMPUTED 或 UNKNOWN），并将其写回状态。
- **写入**：
  - `workspace/plan.json`.
  - 将符合重试条件的 finding JSON 文件从 `workspace/archive/findings_pass_K/` 或 `workspace/archive/loopK_findings/`（其中 K 为其被归档时的 pass）复制到 `workspace/findings/`（保留其原始 UUID 文件名）。
- **前置条件**：
  - 代码库必须可访问。
- **幂等性保证**：
  - 直接覆盖 `workspace/plan.json`。在 Mode B 中，仅当 Block B 为 MATCHED 且其文件未变更且存在时，才原样复制回该 finding；否则安排一次全新的重新发现调查。在缓存读取规则下查阅 `.repro_attempts.json`。

## 指令

### 步骤 0：定位器解析（先于其他一切运行）

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

> [!NOTE] **CURRENT-PASS CHECK（防御性；约束性保证在于 harness，依据 `mantis-pipeline-adapter` 场景 2）：** 如果 `active_snapshot` 存在 AND `active_snapshot.pass != state.pass_number`，则在本 pass 中将该快照视为 STALE —— STOP “stale active_snapshot: pass mismatch”，或降级为 HALT（`snapshot_pinned` 实际为 false：无权威判定，Block B NOT_MATCHED，reproduce `not_attempted`）。这可以捕获一种自定义 harness：它在 Stage 15 的 pass 递增过程中保留了 `active_snapshot` 而未重新固定。参考 meta-agent 每次 pass 都会重新固定，因此该检查在那里永远不会触发。Block B 自身无法检测到这一点（它仅感知 `snapshot_id`，而不感知 `pass`）。

战略规划器的技能特定说明：

- 在 Mode A 中，Plan 是一个 CODE-READING 阶段（它会爬取生产目录）；仅针对 findings 的跳过 NOT 适用。
- Mode A 的爬取以及每个 `target_files` 路径均为 SNAPSHOT-RELATIVE：在 CODE_ROOT 下爬取并解析它们。
- `workspace/kb/`、`workspace/plan.json`、`workspace/.mantis_state.json`、`workspace/archive/` 和 `workspace/findings/` 均为 STATE-RELATIVE：在 --state_root 下读写它们，NEVER 在 CODE_ROOT 下。
- 绝不在 CODE_ROOT 下写入、编译或生成（Block A 步骤 4）。plan 脚本仅写入 ONLY workspace/plan.json（相对于 state）。Block E 中的 VCS 差异依据 Block A 步骤 5 在 LIVE 仓库根目录运行，NOT CODE_ROOT。

分析仓库结构，并创建一份详细的防御性安全审查计划，避免与先前工作重复，同时深入复杂的跨过程路径和尚未扫描的代码边界。

> **目标无关性指令：** 你正在评估的目标可能是原始源代码、编译后的二进制文件、固件镜像，或一个正在运行的 staging/dev 端点。请以目标当前所处的任何格式为基础来开展规划。你被授权并鼓励使用你可支配的任何合适工具（例如标准 Unix 工具、`unblob`、`radare2`、`angr`、`objdump`、`Ghidra`、`qemu`、`unicorn`）来探查制品结构。如果源代码不可用，不要试图强行套用源代码工作流（例如搜索 `.c` 或 `.py` 文件）；而应针对手头的制品进行调整，并“采用可行的做法”。

按如下方式执行规划阶段：

1. **检查威胁模型上下文：** 在知识库目录中检查是否存在 `workspace/kb/THREAT_MODEL.md` 文件。如果存在，请完整读取该文件，以理解程序的官方安全边界、威胁行为者、资产、高风险接口和受信输入。

2. **确定模式并获取经验：** 检查知识库索引 `workspace/kb/index.md` 是否存在。

   - **MODE A：首轮穷尽模式（未找到 `workspace/kb/index.md`）：**
     如果这是首次运行，请保证对代码库的完整覆盖。为避免在大型仓库上触及输出 token 上限，不要在文本响应中手动生成 `workspace/plan.json`。而应执行一条 shell 命令，运行一个以你偏好的语言编写的简短脚本，该脚本会：

     1. 使用 `find` 或 `os.walk` 爬取所有生产目录。如果某个目录中存在 `mantis-summary.md` 文件，则使用其内容来理解目录结构，而不是读取每个单独的源文件。否则，爬取所有生产源代码文件（例如 `.c`、`.cpp`、`.py`、`.js`、`.go`、`.rs`、`.java`）。
     2. 忽略测试文件夹、构建产物和第三方依赖（例如 `node_modules`、`.git`、`tests/`）。
     3. 以程序化方式将该列表格式化为 `workspace/plan.json` schema，并直接写入磁盘。由于这是自动化脚本，需指示它对 `"question"` 字段使用一个通用的、总括性的基线问题（例如“对内存安全和逻辑缺陷执行基线审计”），而将高度依赖上下文的自定义问题保留给模式 B。

   - **MODE B：战略学习模式（`workspace/kb/index.md` 存在）：** 读取 `workspace/kb/index.md` 和 `workspace/kb/THREAT_MODEL.md`，以审阅代码库累积的历史知识，包括信任边界、漏洞类别和架构组件。调整你的关注点，为有漏洞历史的组件和文件设计新的、有针对性的深度审查和回归审查。在此模式下，你可以使用文件写入工具手动生成 `workspace/plan.json`，因为范围会窄得多。

     - **有针对性的重新评估与重试**：审阅 KB 索引、实体文件以及复现尝试缓存文件（`workspace/archive/.repro_attempts.json`，若存在）。你必须识别需要重新评估或重试的发现项：

       还要从 `workspace/.mantis_state.json` 读取快照上下文：`active_snapshot.{snapshot_id, snapshot_pinned}` 和 `snapshot_history`（两者均由 meta-agent 写入）。然后，通过在 LIVE 仓库根目录运行下方的 Block E，为 THIS 轮次 COMPUTE `changed_files` / `changed_files_status`（依据 Block A 步骤 5 —— VCS 元数据豁免；固定的 --snapshot_root 会剥离 .git/.hg/.repo，因此 diff MUST 针对活动工作树运行）。将计算得到的 `changed_files`（仓库相对路径数组）和 `changed_files_status`（`COMPUTED` 或 `UNKNOWN`）写回 `workspace/.mantis_state.json`，然后在阶段剩余部分使用它们。还要写入 `changed_files_pass` = 来自状态的当前 `pass_number`，以便消费方能够检测到过期的（上一轮次的）diff。使用以下内容了解自上一轮次以来哪些文件发生了变更：

       CHANGED-SINCE-PREVIOUS：在 LIVE 仓库根目录运行（NOT SNAPSHOT_ROOT）。CUR = 当前提交/修订；PREV = 本轮次之前的 snapshot_history 条目 BEFORE。如果 PREV 缺失 OR vcs_type 属于 {none,unknown} OR 前一版本或当前版本的 SNAPSHOT_ID 是 content:/live:/+content_hash 回退 OR diff 命令报错 -> changed_files_status = UNKNOWN。将 EVERY 文件视为 CHANGED。NEVER 视为未变更。NEVER 丢弃。（注意：仅 `snapshot_pinned false` NOT 是 UNKNOWN 的触发条件 —— 在 HALT 模式下，`active_snapshot` 存在且 `snapshot_history` 有一个 PREV 条目，因此 diff 仍可运行。在 MODE-OFF —— 没有 `active_snapshot` —— 时，不存在 PREV 条目，因此 PREV 缺失，diff 降级为 UNKNOWN，但这 NOT 强制进行完整的模式 A 爬取；参见下方的模式 A 触发条件。）否则：git：`git diff --name-status -M -C --diff-filter=RAMDCT PREV CUR`（`-M` 标志检测重命名；`-C` 检测复制；`--name-status` 对重命名输出 `R<score>\told_path\tnew_path`，使旧路径和新路径都可见；`--diff-filter=RAMDCT` 包含 Renamed、Added、Modified、Deleted、Copied 和 Type-changed 文件）hg：`hg status -C --rev PREV:CUR`（`-C`/`--copies` 对重命名/复制在下一行显示源路径；hg 代码：A=added、R=removed、M=modified）multi-vcs：`repo forall -c 'git diff --name-status -M -C --diff-filter=RAMDCT PREV CUR'`（任何错误 -> UNKNOWN）如果发现项的任一 `code_paths`（路径部分）在集合中，则该发现项的文件为 CHANGED；如果其路径被重命名到或从之重命名（解析 `R<score>\told\tnew` 行：旧路径和新路径都在变更集合中），则为 OR。如果发现项的主文件作为重命名源（旧路径）出现，则将 NEW 路径也视为已变更 —— 缺陷很可能随文件一起移动了。

       在你检查 `workspace/archive/.repro_attempts.json` 的任何地方，也要应用此缓存读取规则。FIRST 选择缓存 KEY，必须完全按照 `mantis-reproduce` 写入它的方式：如果发现项有 `signature` 字段，则键就是该 `signature`；否则键为 `stable_key = normalized_title + "@" + primary_file_path`（标题转为小写并移除所有非字母数字字符；`primary_file_path` = `code_paths` 的第一个条目，并剥离任何末尾的 `:line`）。THEN 读取该键下的值 V：如果 V 是整数，则 count=V 且 last_snapshot=UNKNOWN；如果 V 是对象，则 count=V.count 且 last_snapshot=V.last_snapshot（默认 UNKNOWN）。如果在所选键下未找到条目，在得出 count=0 的结论之前，还要尝试 OTHER 键形式，这样以 signature 为键的写入方和以 stable_key 为键的读取方就永远不会彼此错过，也不会错误地重置尝试预算。（在迁移期间，缓存同时混用两种值形式 AND 两种键风格。）

       1. **安排研究：** 对于归档中标记为 `"NEEDS_RESEARCH"` 的发现项，在 `workspace/plan.json` 中安排一次有针对性的调查（以收集缺失的上下文，并将其判定为 `"VALID"` 或 `"FALSE_POSITIVE"`）。

       2. **复制以重试（受快照门控）或重新发现：** 对于每个原本符合重试条件的归档发现项（未尝试复现，或 `failed_to_reproduce` 且按上述缓存读取规则尝试次数少于 2，或 `patch_status` 属于 {`VERIFICATION_FAILED`,`ERROR`,`VERIFICATION_INCOMPLETE`}），运行：

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

          然后机械地做出决定：

          - **COPY VERBATIM**（快速重试）ONLY 如果 ALL 成立：该发现项的 Block B 为 MATCHED，AND 其主文件（第一个 `code_paths`，已剥离行号）在 `changed_files` 中 NOT，AND 该文件在 CODE_ROOT 下 EXISTS。将归档的 `<uuid>.json` 复制回 `workspace/findings/<uuid>.json`，保留 UUID **及其 ORIGINAL `discovery_commit`**（不要重新打戳 —— 漂移必须保持可检测）。

          - **MODE-OFF 绕过（3 状态规则）：** 如果 `active_snapshot` 在状态中为 ABSENT（MODE-OFF —— 未请求 `--sync`），则 Block B 始终返回 NOT_MATCHED（`snapshot_pinned is false -> NOT_MATCHED`），因此上方的 COPY-VERBATIM 门控永远不会触发，每个符合重试条件的发现都会被 RE-DISCOVER —— 这是相对当前行为的回归（当前，当文件仍然存在时，pass ≥2 会原样结转未变更的发现）。在 MODE-OFF 中，当发现的主文件（第一个 `code_paths`，去掉行号）在 CODE_ROOT 下 EXISTS 时，COPY-VERBATIM（去掉 `Block B MATCHED` 和 `file NOT in changed_files` 这两个合取项 —— 在 MODE-OFF 中本来就没有 `changed_files` diff）。这与 `mantis-patch` 的 LEGACY 模式例外处理（`patch:133-138`、`patch:172-174`）相对应。NOT 将绕过门控建立在 `snapshot_pinned==false` 上 —— 那样也会捕获 HALT 模式，而在该模式下 STALE 横幅合理地标记该发现需要重新验证。ONLY 在 `active_snapshot` 缺失时进行门控。（NOT 修改 Block B 本身 —— 根据 `README_AGENTS.md:711-718` 的块保真度警告，它在各技能之间逐字符完全相同；修复应放在 plan 对 Block B 的 CONSUMERS 中，而不是 Block B 中。）

          - **RE-DISCOVER** 在所有其他情况下（Block B 为 NOT_MATCHED，或文件在 `changed_files` 中，或文件缺失，或 `changed_files_status`==UNKNOWN）：NOT 复制回去。改为向 `workspace/plan.json` 追加一次新的调查，其中嵌入该发现的 `title`、`description` 和 `repro_hints`，将 `target_files` 设为旧 code_paths 的目录子树，PLUS 针对该发现的函数/结构体/标题词项进行仓库范围的符号/关键字搜索（以便重新找到被移动或重命名的缺陷），并要求研究员在 CURRENT 快照上重新推导确切行号。此外，在已归档的发现上记录一条历史备注 `unconfirmed-regression-pending`，使其在某个 pass 重新发现它或人工将其驳回之前，绝不会被静默丢弃。

          - **行号/AST 重新锚定（Phase 2 增量效率）：** 在回退到完整的 RE-DISCOVER 之前，尝试使用前向行跟踪（反向 blame 或 diff hunk 偏移）将该发现的行号重新锚定到 CURRENT 快照。这是一种优化：如果该发现的主函数/符号仍然存在于附近，重新锚定会产生一个行号 HINT，用于聚焦 RE-DISCOVER 调查 —— 它 NOT 替代重新验证（快照已变更，因此该发现仍会在下游被重新研究）。

            - **如何操作：** 将该发现的旧行 FORWARD 从 PREV 转换到 CUR（NOT 单独对 PREV 执行 blame —— 那会返回 PREV 时的行，并不会将其向前映射）。在 LIVE 仓库根目录下运行（Block A 步骤 5 例外处理）：git：
              `git blame --reverse <PREV>..<CUR> -L <old_line>,<old_line> -- <file>`
              （反向 blame 会将该行向前跟踪到 CUR），或者将从 `git diff <PREV> <CUR> -- <file>` 得到的 hunk 偏移加到 `<old_line>` 上。
              然后在 CODE_ROOT（当前固定的快照）中读取映射后的行，并确认该发现的主函数/符号存在于 ±50 行范围内。这仅产生一个 CANDIDATE 新行号 —— 一个用于 RE-DISCOVER 的搜索提示，绝不是可信的重新验证。
            - **当重新锚定 SUCCEEDS 时**（在映射行处找到符号）：使用新行号来 FOCUS 该发现的 RE-DISCOVER 调查（首先将研究员指向映射后的 `code_paths` 位置）。NOT 将该发现转换为 COPY-VERBATIM，并且 NOT 跳过重新验证：Block B 为 NOT_MATCHED，因此该发现仍会在下游被重新研究/重新复现（符号可能存在于映射行处，但已经处于 FIXED 状态）。保持 `discovery_commit`、`signature` 和 `lineage_id` 不变；添加一条历史备注 `re-anchored: <old_line> -> <new_line> (search hint)`。
            - **当重新锚定 FAILS 时**（函数被删除、未找到符号、diff 过大、blame 出错，或旧行处的代码完全不同）：如上所述回退到完整的 RE-DISCOVER。这是保守的护栏：在 ANY 不确定的情况下，重新发现。
            - **绝不要使用重新锚定来抑制或丢弃发现。** 它纯粹是行号更新的快速路径；如果它失败，该发现仍会通过正常路径被重新发现。
            - **与 VCS 无关：** 对于 hg，diff `PREV:CUR`（`hg diff --rev PREV:CUR -- <file>`）并将 hunk 偏移应用到 `<old_line>` 以获得前向映射的行。对于无 VCS/二进制目标，重新锚定不适用；始终回退到 RE-DISCOVER。

          - **NEVER 复制回** 满足以下任一条件的发现：其 `"status"` 为 `"FALSE_POSITIVE"`，或其 `"patch_status"` 为 `"VERIFIED_SECURE"`，或其 `"repro_status"` 为 `"reproduced"`（除非如上所述补丁失败），或已达到针对 CURRENT 快照的 2 次尝试上限。但如果此类发现的文件 IS 于 `changed_files` 中，或 Block B 为 NOT_MATCHED，则将其视为可能的回归：RE-DISCOVER 它（不要针对已变更的代码信任旧的终态判定）。**MODE-OFF
            carve-out：** 如果 `active_snapshot` 为 ABSENT（MODE-OFF），则去掉上面的 `or Block B is NOT_MATCHED` 析取项——在 MODE-OFF 下，对每个发现而言 Block B 都是 NOT_MATCHED（这是无快照造成的产物，而非漂移的信号），因此若保留该析取项，就会在每一轮重新打开所有终态判定（FALSE_POSITIVE/VERIFIED_SECURE/reproduced）。在 MODE-OFF 下，ONLY 依赖 `file IS in changed_files`（该条件在 MODE-OFF 下为空真——不存在 `changed_files` 差异），因此终态发现会被原样结转。这就是当前的行为。

     - **变更/新增攻击面覆盖（MANDATORY）：** 为 `changed_files` 中的 EVERY 路径新增一项标题为 `Exhaustive Review: <path>` 的调查（无论它是否映射到已归档的发现）。如果 `changed_files_status`==UNKNOWN AND `active_snapshot` 存在（HALT 或 PINNED 模式——本轮请求了同步），OR 本轮确实发生了同步（`snapshot_id` != 上一条 `snapshot_history` 条目的 id），则你 CANNOT 信任一个狭窄的集合：本轮回退到完整的 **Mode A** 穷举爬取，EVEN IF `kb/index.md` 存在（这是捕获新增文件的唯一方式）。但是，在 MODE-OFF 下（无 `active_snapshot`——无 `--sync`），即使 `changed_files_status`==UNKNOWN，也 NOT 在 pass ≥2 时强制进行完整的 Mode-A 爬取：这是当前的默认行为，而在每个 MODE-OFF 的 pass ≥2 上强制 Mode-A 将是一种回归。在 MODE-OFF 下，如当前一样，依赖现有的 `kb/index.md`（Mode B）进行收窄。

     - **依赖感知的扇出（Phase 2 增量效率）：** 当 `changed_files_status` 已知（不是 UNKNOWN）且依赖图可用时，EXPAND 调查范围，使其超出仅变更文件本身。目标：识别出 IMPORT 或 DEPEND ON 变更文件的那些文件，以便规划器能够为变更代码的使用方（而不仅是变更代码本身）安排有针对性的调查。

       - **如何做：** 以文件级依赖图（`workspace/kb/dependencies.json` 或实体关系 markdown）作为强制底线开始：对每个变更文件 F，找出所有导入 F 的文件（直接导入或传递导入，最多 2 跳）。将这些依赖文件作为 `Exhaustive Review: <dependent_file>` 条目加入调查范围。然后 ADD 结构索引调用方作为补充：在可用时使用查询辅助工具（`workspace/helpers/query_structural_index.py`）以获得函数级精度——对变更文件的导出函数调用 `resolve_symbol()`，然后调用 `find_callers()` 以在符号级别枚举依赖方。为依赖图发现的依赖方与结构索引发现的调用方的 UNION 安排调查——它们是互补的，而非替代关系。如果结构索引缺失（无 `manifest.json`）、为空，或查询辅助工具缺失，则仅依赖图仍作为底线。
       - **何时使用：** ONLY 当 `changed_files_status` 已知 AND KB 包含依赖信息时使用。如果 KB 缺少导入/构建图，或 KB 已过期（将 `workspace/.mantis_state.json` 中的 `kb_snapshot_id` 与 `SNAPSHOT_ID` 对比——若二者不同，则 KB 是针对不同快照构建的，可能已过期），则回退到 Phase-1 的行为（完整 Mode-A 爬取或 Mode-B 收窄）。
       - **护栏：** 如果依赖图不完整、已过期，或出现任何不确定性，则回退到 Phase-1 的重新发现（将 ALL 文件视为可能受影响）。绝不能用依赖收窄来 DROP 某项调查——它只能 ADD 针对依赖文件的有针对性调查。变更文件本身 ALWAYS 会被调查，无论何种情况。
       - **与 VCS 无关：** 依赖图源自 KB 的架构分析，而非 VCS 元数据。它适用于任何具有 import/include/use 语句且已被 KB 索引的语言。

     - **结构索引查询（仅 HINT 增强）：** 当结构索引可用（`workspace/kb/structural_index/manifest.json` 存在）时，使用它以精确的符号级调用方发现来 SUPPLEMENT 上述依赖感知的扇出。结构索引决定调查优先级的 ORDER，NEVER 决定审计集合的 MEMBERSHIP。

       - **解析优先协议（MANDATORY）：** 在查询调用方之前，先解析符号：

         `python3 workspace/helpers/query_structural_index.py resolve_symbol --name "<function_name>" [--language "<lang>"] [--file "<path>"] --state_root <state_root>`

         如果响应中包含 `ambiguous: true`，则 NOT 静默地挑选其中一个匹配项。用 `--file`/`--language` 收窄，或为 ALL 匹配到的符号安排调查。

       - **有界调用方查询：** 解析完成后，使用显式参数查询调用方
         bounds:

         `python3 workspace/helpers/query_structural_index.py find_callers --symbol_id "<resolved_id>" --limit 100 --offset 0 --state_root <state_root>`

         如果 `has_more` 为 true，则用 `--offset` 分页。

       - **覆盖感知解读：** 检查每个结构索引响应中的 `coverage.partition_status`：

         - `complete` + `precision == semantic` + 无调用方 = “无已索引调用方”——该分区已完全索引且使用语义后端，因此对于已索引代码，空结果是权威的。仍须按仅 HINT 规则运行 grep（grep 可捕获基于宏的调用、函数指针和动态分派）。
         - `complete` + `precision != semantic` + 无调用方 = “无已索引调用方”——该分区完整，但精度低于语义级，因此空结果 NOT 具有权威性。MUST 运行穷举 grep 回退。
         - `partial` / `empty` / `failed` + 无调用方 = “未完全索引”——该分区不完整，因此扩大调查范围，并 MUST 运行穷举 grep 回退。

       - **护栏：**

         - 仅 HINT：结构索引结果决定 ORDER，绝不决定 MEMBERSHIP。它们用于确定先调查哪些依赖文件的优先级；它们 MUST NEVER 导致某个文件被从审计范围中移除。
         - 每个结果都带有 `precision` 和 `backend` 字段——请使用 `precision`（`semantic` > `typecheck` > `ast` > `symbol-only` > `heuristic` > `deferred` > `coverage-only`）来权衡对该结果的信任程度。
         - 如果结构索引缺失（没有 `manifest.json`）、为空，或查询辅助工具缺失：则回退到基于 grep 的发现方式（即当前行为）。结构索引仅是一个覆盖率 HINT。

     - **上下文注入（`kb_references`）：** 对于你计划开展的每一项调查，你必须确定 `workspace/kb/` 目录中的哪些文件（例如 `workspace/kb/entities/auth_module.md` 或 `workspace/kb/vulnerabilities/CWE-79.md`）为研究员提供必要的上下文。请将这些 markdown 文件的确切文件路径包含在该项调查的 `"kb_references"` 数组中。这样可将收集上下文的负担从研究员身上转移开。

     - **探索性/无约束调查（中等概率）：** 以中等概率（例如每次规划轮次有 25-50% 的概率），在
       plan:

       1. **对抗性扫描：** 选择一个威胁模型当前标记为安全、低风险或超出范围的组件或目录。指示研究员执行无约束扫描，忽略 `workspace/kb/THREAT_MODEL.md` 中的安全假设。

       2. **随机挖掘：** 在代码库中选择一个随机起点（文件或目录）。该项调查的问题应尽量简短且开放式，仅指示研究员“深入挖掘”或“探索”所选区域，不提供具体的威胁模型上下文或预定义的漏洞类别。为该项调查将 `kb_references` 设置为空列表，以确保获得全新的视角。

       **Token 优化：** 无论使用脚本（模式 A）还是你的文件写入工具（模式 B），都要将计划直接写入磁盘，不要在聊天回复中打印 JSON 内容。

3. **Schema 强制校验：** 无论采用哪种模式，最终写入磁盘的 `workspace/plan.json` 文件都应匹配以下 schema，以确保下游审计智能体能够正确解析它：

### 计划 Schema 格式

```json
{
  "investigations": [
    {
      "title": "Exhaustive Review: [relative_file_path]",
      "target_files": ["[relative_file_path_1]", "[relative_file_path_2]"],
      "kb_references": ["workspace/kb/entities/auth_module.md", "workspace/kb/vulnerabilities/CWE-79.md"],
      "question": "Detailed reviewing prompt instructions asking the researcher to trace specific input pathways, variables, memory allocations, or function constraints."
    }
  ]
}
```

确保 `workspace/plan.json` 已成功写入。完成后，通知用户。
