---
name: mantis-report
description: >-
  生成一份人类可读的安全审查报告包，内容由已确认的发现和利用链汇编而成。
  在审查周期结束时使用，以产出面向利益相关方的文档。
  不要用于直接审计代码或验证补丁。
---

# 报告器（/mantis-report）

## 系统目标

安全报告专家。将复杂的技术性发现日志综合为一份高质量、人类可读的审查报告包，供开发者和利益相关方使用。

## 命令定义

- **命令：** `/mantis-report`
- **描述：** 生成一份人类可读的安全审查报告包，其中包含已确认的发现和利用链。

## 输入/输出契约

- **读取**：
  - `workspace/findings/*.json`（本轮所有活动发现文件）。
  - `workspace/archive/findings_pass_*/*.json`（此前各轮归档的发现）以及旧版 `workspace/archive/loop*_findings/*.json`。报告是覆盖整个行动周期的视图：包含本轮的全部发现，加上此前各轮已停止重试（例如达到重试上限）而结转下来的发现，以免它们从报告中消失。
  - `workspace/.mantis_state.json`（用于跟踪当前循环轮次，并读取 `vcs_info` 和 `active_snapshot` {`root`, `snapshot_id`, `snapshot_pinned`} 以获取溯源信息）。
  - 每条发现的快照溯源字段，均为 OPTIONAL：`discovery_commit`、`repro_snapshot_id`、`patch_base_snapshot`。当其中任一字段缺失或为空时，渲染为“(not recorded)”——这绝不能成为丢弃该发现的理由。
- **写入**：
  - `workspace/report/review_packet_pass_<N>_<snapshot_tag>.md`（带轮次和快照标记的 markdown 报告）。对于未记录快照的旧版轮次，回退到不带后缀的 `review_packet_pass_<N>.md`。
  - 更新 `workspace/report/review_packet-latest.md` 处的副本/符号链接。
- **前置条件**：
  - 在 `workspace/findings/` 或 `workspace/archive/` 中存在已校准且已复现的发现。报告是覆盖整个行动周期的视图：它报告本轮或此前任一轮发现的每一条未解决发现的最新状态，并按每条发现的最新状态去重。一条已确认但未修复、且 plan 已停止结转的发现（例如已达到 2 次尝试的重试上限）NOT会消失——它会以其最近一次归档状态出现在此处。
- **幂等性保证**：
  - 写入带轮次和快照标记的文件。就地覆盖 `review_packet-latest.md`。在SAME快照上重新运行SAME轮次，会更新同一个带标记的文件。在DIFFERENT快照上重新运行相同的轮次编号，会写入一个DISTINCT文件（`<snapshot_tag>`后缀可防止跨快照覆盖）。未记录快照的旧版轮次保留不带后缀的 `review_packet_pass_<N>.md` 名称并就地覆盖，与之前完全一致。

## 指令

**步骤 0 —— 定位器解析。**

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

然后，以下 FINDINGS-ONLY 说明适用于报告器：

- 报告器是一个 FINDINGS-ONLY 阶段（Block A，ROLE 步骤 0）：它SKIPS定位器步骤 2–6，NOT要求或解析 CODE_ROOT，并且NEVER会因为代码根或哨兵未设置而停止。
- 它STILL会读取 `active_snapshot`（`root`、`snapshot_id`、`snapshot_pinned`）以及来自 `workspace/.mantis_state.json` 的 `vcs_info` 以获取溯源信息——用于构建页眉、顶部横幅和输出文件名。
- 报告器触及的每一条路径（`workspace/findings/*.json`、`workspace/.mantis_state.json`、`workspace/report/*`、`workspace/archive/*`）都是 STATE-RELATIVE 的，并且NEVER会加上 CODE_ROOT 前缀。

编写一份专业的 Markdown 报告，详细说明已验证/已复现的漏洞和利用链。

按如下方式执行报告阶段：

1. **加载发现——本轮全部发现，加上结转的未关闭发现（最新优先折叠；不使用脚本）。** 构建一个以发现身份为键的工作集，每条发现仅以其最新状态出现一次：

   **SAME-BUG PREDICATE**（用于本阶段中ALL当前↔归档去重、折叠和抑制；在此处多报始终是安全的，隐藏真实发现则绝不可接受）：两条发现是SAME BUG，仅当EITHER (i) 它们具有完全相同的 `id`（UUID）；OR (ii) ALL THREE 条件成立——它们共享一个非空的 `lineage_id`，共享一个非空的 `signature`，AND至少有一个 `code_paths` 条目在WITH其末尾 `:line`（含行号）比较时二者完全相同。否则它们就是DISTINCT——BOTH都渲染。NEVER仅凭 `lineage_id` 或 `signature` 就把两条发现当作同一个缺陷
   alone：两者都比缺陷的真实身份更粗糙（由 basename 派生的 lineage 可能把两个同名的不同文件关联起来；`signature` 会剥离行号，因此会在同一文件内的不同缺陷之间发生碰撞），仅凭其中任一者进行合并都可能静默丢弃一条真实发现。

   **设计说明（重新锚定 vs 折叠）：** `mantis-plan` 的重新锚定（Phase 2\）只提供一个行号提示，用于引导 RE-DISCOVER——它NOT会重写结转发现的 `code_paths`。重新发现的发现会以其在当前快照上的NEW行号出现。由于折叠谓词要求 `code_paths` 含行号匹配，重新发现的发现将NOT与其祖先折叠（行号不同）。这属于SAFE多报——两个条目都会渲染，当前条目显示新位置。未来阶段可以在BOTH两条发现携带相同的 `signature` AND `lineage_id` AND 祖先的行号已确认在当前快照上不存在时，将谓词放宽为仅按路径匹配，但当前使用保守的含行号匹配，以防止任何静默丢弃的风险。

   1. 读取所有活动 `workspace/findings/*.json`，并将每一条活动发现加入工作集，以其 `id`（UUID）为键——涵盖所有修复状态，因此本轮的 `VERIFIED_SECURE`/`MITIGATION_PROPOSED` 修复仍会连同其补丁渲染在类别 1/2 中。NEVER合并两条 ACTIVE 发现，即使它们共享 `lineage_id` 或 `signature`——将每条活动 `id` 保留为独立条目。
   2. 按轮次降序扫描 `workspace/archive/findings_pass_<N>/`（以及旧版 `loop<N>_findings/`）。仅在以下条件全部成立时才加入归档风险：仍处于未关闭状态（OPEN，见 4）；并且（AND）工作集中没有（NO）按上述谓词判定为同一个（SAME BUG）缺陷的风险；并且（AND）此前没有加入过该同一（SAME BUG）缺陷的更高轮次归档副本。最先遇到的副本代表最新状态；忽略更低轮次目录中随后遇到的同缺陷副本。仅当（ONLY）真正的同一缺陷取代它时，才能省略归档风险；仅有 `lineage_id` 或 `signature` 相同，不能（NOT）将其省略。
   3. **取代折叠（Phase 3）：** 当某个归档发现 IS 当前发现满足 SAME BUG（谓词 ii）时，当前发现取代它——在当前（最新）状态处只显示一条条目；不要同时渲染归档副本。当该谓词 NOT 满足时（`signature` 不同，例如文件重命名，或 `lineage_id` 缺失），将两者作为独立条目分别渲染（安全的过度报告，绝不隐藏）。
      - **漏报防护：** 如果当前（取代）发现 FAILS 可操作谓词（见下文步骤 4），但归档祖先本会 PASSED 该谓词（例如祖先为 `reproduced` 且 OPEN，而当前 pass 的复现因新快照上的临时构建/环境故障而结果为 `not_attempted`），则 NOT 让该临时降级抑制已确认开放的 bug。相反，应在报告中保留祖先最后确认的状态可见（渲染时注明“Open — repro pending on new snapshot”），使先前已确认的 bug 绝不会从报告中悄然消失。当前发现的较新元数据（例如更新后的 `code_paths` 行号）仍可作为注释附加，但显示的判定/状态必须是祖先最后确认的状态，绝不能是临时降级。
   4. **可操作/质量谓词**（适用于活跃 AND 归档）：仅当发现是漏洞利用链（存在 `constituent_findings`，或标题/历史中含“Exploit Chain”）OR `repro_status` 为 `reproduced` OR（`repro_status` 为 `statically_confirmed` AND 其携带经验性执行证据——外部堆栈跟踪、sanitizer 跟踪（ASan/UBSan/MSan/TSan）或崩溃日志）时，才纳入该发现。不要纳入误报、`NON_VIABLE`、`DUPLICATE`、`failed_to_reproduce`，或缺少经验性跟踪的普通 `statically_confirmed` 发现。
   5. **开放谓词**（仅用于步骤 2 中的归档结转）：若归档发现满足（4）AND `patch_status` 为 NOT `VERIFIED_SECURE`/`MITIGATION_PROPOSED` AND `status` 不为 `FALSE_POSITIVE`/`DUPLICATE` AND `production_viability` 不为 `NON_VIABLE`，则该发现“仍开放”。（活跃发现无论 `patch_status` 如何都会被加载，因此本 pass 的修复仍会出现；已归档的已修复发现不会每个 pass 都重新列出——如需累积的已修复视图，请使用可选的“Resolved this campaign”汇总。）
   6. **规模：** 一次处理一个目录，最新优先；绝不一次性加载整个归档。

   - **严重性过滤：** 将优先级为 `"LOW"` 的发现从主报告正文中排除。你必须将这些较低优先级问题放入报告最末尾一个单独的、专用的“Appendix: Low Priority Findings”章节，使主报告保持聚焦于高风险问题。

2. **提取关键工件：** 对每个已复现的发现，提取并格式化：

   - **头部元数据：** 标题、ID（UUID）、推断暴露面、最终风险评分和定性优先级。
   - **Campaign 溯源：** 为每个开放发现标注其首次被发现的 pass 与当前 pass（例如 `discovered pass 2 · still open as of pass 7`），使结转的发现能够被识别为结转。首次发现 = 该发现 `history` 条目中最小的 `pass_number`（或包含它的编号最小的归档目录的 pass_number）；当前状态 = 你在上述折叠中保留的副本。
   - **重复提示：** 如果该发现具有 `possible_duplicate_of` 字段（当跨 pass 候选为 NOT_MATCHED 时由 `mantis-dedupe` 设置），则输出一条提示说明：
     `Possibly related to finding <UUID> (cross-pass candidate; snapshots differ — not confirmed duplicate).`
     这使该回归指针提示对利益相关者可见。
   - **发现快照：** 为该发现输出 `Discovery Snapshot: <discovery_commit>`。如果 `discovery_commit` 缺失或为空，则输出 `Discovery Snapshot: (legacy — not recorded)`。绝不因该字段缺失而省略或丢弃该发现。
   - **漏洞描述与影响：** 对 bug 的清晰解释及其对系统的具体影响。
   - **复现证据：**
     - PoC 脚本路径（`repro_file_path`）和执行命令（`run_command`）。
     - 一段干净的 stdout/stderr 片段，展示成功触发漏洞利用（`repro_output`）。
     - **证据基线：** 用采集该复现证据时所处的快照为其标注：`Evidence base: <repro_snapshot_id>`。如果 `repro_snapshot_id` 缺失或为空，则写 `Evidence base: (not recorded)`。NOT 假定它等于头部/pass 快照。
   - **风险论证：** 独立验证推理（`reasoning`）、生产可行性推理（`critic_reasoning`）以及愤怒因素分析（`outrage_commentary`）。
   - **修复与补丁：**
     - 推荐的缓解策略。
     - 已验证的补丁 diff（`patch_diff`）以及再攻击状态，以证明修复具有韧性。
     - **适用基线：** 用补丁 diff 所适用的快照为其标注：`Apply against: <patch_base_snapshot>`。如果 `patch_base_snapshot` 缺失或为空，则写 `Apply against: (not recorded)`。如果 `patch_base_snapshot` 存在且与该发现的 `discovery_commit` 不同，则添加一行警示，说明该 diff 是针对不同快照生成的，可能无法干净地应用到发现快照上。
   - **PII 与机密信息脱敏：** 在将任何发现数据（包括描述、PoC 脚本/命令和复现日志）写入报告之前，你**必须**扫描并脱敏任何硬编码的 API 密钥、令牌、凭据、PII（姓名、电子邮件、电话号码）、内部主机名/域名以及过度武器化的载荷参数，将其替换为标准占位符，如 `<REDACTED_SECRET>`、`<REDACTED_PII>`、`<REDACTED_INTERNAL_HOST>` 或 `<REDACTED_PAYLOAD>`，以确保报告可安全地广泛分发。

3. **生成评审包（Review Packet）：**

   - **报告页眉与免责声明：** 位于报告最顶部（在 Executive Summary 之前）：

     **快照来源横幅——在报告最顶部、按以下顺序，将下列 BEFORE 个条目 1 各自作为独立的引用块输出：**

     a. **非权威 / HALT 横幅（3 状态规则）。** 从 `workspace/.mantis_state.json` 读取 `active_snapshot`。- 如果 `active_snapshot` **不存在**（MODE-OFF——未请求 `--sync`，即当前默认情况）：NOT 输出此横幅。该次运行与当前行为逐字节一致；报告中的 `VERIFIED_SECURE` 发现及其他判定与当前一样有效。在此处输出 NON-AUTHORITATIVE 横幅将与同一次运行产生的判定相矛盾。- 如果 `active_snapshot` IS 存在，但 `snapshot_pinned` 为 `false`（HALT 模式）：将其作为 FIRST 行输出到
     report：
     `> **WARNING — NON-AUTHORITATIVE RESULTS:** The target could not be         pinned to an immutable snapshot for this pass (HALT mode: the tree         raced or was too big / live / copy-failed). Findings may not         correspond to a stable, reproducible tree, and the ABSENCE of         findings in this report does NOT indicate the target is secure.         Treat all results as provisional.`
     当 `active_snapshot` 不存在（MODE-OFF）OR 当 `active_snapshot.snapshot_pinned` 为 `true`（PINNED）时，省略此横幅。

     b. **工作区脏状态警告。** 如果 `vcs_info.dirty` 为 `true`，则输出：
     `> **WARNING — DIRTY WORKING TREE:** The target had uncommitted local       modifications when it was scanned. Results reflect that exact working       tree (captured by content hash), NOT a clean committed revision. The       recorded commit alone will not reproduce this state.`

     c. **混合快照横幅。** 令 HEADER_SID = `active_snapshot.snapshot_id`。如果 HEADER_SID 存在且非空 AND 任一 INCLUDED 发现的 `discovery_commit` 缺失、为空或与 HEADER_SID 字符串不相等，则输出：
     `> **WARNING — MIXED SNAPSHOTS:** This report combines findings       discovered on different code snapshots (e.g. findings retried from       earlier passes). The pass snapshot is <HEADER_SID>. Consult each       finding's "Discovery Snapshot" before acting; line numbers and code       context may differ between snapshots.`
     仅以 EXACT 字符串方式比较快照 ID——不得进行模糊匹配或前缀匹配。

     1. 在 `workspace/.mantis_state.json` 中显示从 `"vcs_info"` 读取的目标代码库版本信息：

        - 如果 `"vcs_type"` 为 `"git"`，显示：
          `Target Version: Git branch [branch] at commit [commit_hash] [(dirty) if dirty is true]`。
        - 如果 `"vcs_type"` 为 `"hg"`，显示：
          `Target Version: Mercurial branch [branch] at revision [commit_hash] [(dirty) if dirty is true]`。
        - 如果 `"vcs_type"` 为 `"multi-vcs"`，显示：
          `Target Version: Multi-VCS (repo) manifest [revision] [(dirty) if dirty is true]`。
        - 如果 `"vcs_type"` 为 `"none"`，显示：
          `Target Version: None (No version control detected)`。
        - 如果 `"vcs_type"` 为 `"unknown"`，或 `vcs_info` 缺失，显示：
          `Target Version: Unknown (VCS detection failed/error)`。

        在 `Target Version:` 行之后，于第二行追加本次轮次的快照标识：

        - 如果 `active_snapshot.snapshot_id` 存在且非空，写入：
          `Snapshot ID: [snapshot_id]  (pinned: [snapshot_pinned])`。
        - 如果 `active_snapshot` 不存在或 `snapshot_id` 为空，写入：
          `Snapshot ID: (legacy — snapshot not recorded)`。这仅是用于展示的来源信息；它不会拦截或丢弃任何发现。

     2. 包含一条醒目的免责声明：*“本报告由 Mantis AI 自动生成。所有发现和补丁均由 AI 生成，必须在部署或披露之前由安全专家或领域专家手动验证。”*

   - **按补丁状态分组（互斥性）：** 通过分组发现来组织 Executive Summary 表格和报告正文。**利用链 MUST 被排除在这些主分组之外，ONLY 在其专门的“Exploit Chains (Not End-to-End Reproduced)”章节中报告。** 对于标准（非链式）发现，根据其修复状态将其分为三个不同类别（严格互斥）：

     1. **类别 1：补丁已独立验证**：`patch_status` 为 `"VERIFIED_SECURE"` 的发现。
     2. **类别 2：已提出补丁 / 已识别缓解措施**：`patch_status` 属于 `["MITIGATION_PROPOSED", "VERIFICATION_INCOMPLETE"]` OR（`patch_diff` 存在 AND `patch_status` 未设置/为空）的发现。
     3. **类别 3：未修补 / 验证失败**：`patch_status` 属于 `["VERIFICATION_FAILED", "ERROR"]` OR（`patch_diff` 不存在 AND `patch_status` 未设置/为空）的发现。

   - **专门的利用链章节：** 创建一个专门用于利用链的章节，标题为 `"Exploit Chains (Not End-to-End Reproduced)"`。在此记录每个链式发现，列出其标题、定性优先级、风险评分，并详细说明其组成发现（各自的 ID 和单独状态）。不得将利用链与类别 1、2 或 3 中的标准发现混在一起。

   - **按轮次编号的输出：** 不要在每次执行时覆盖同一个 `review_packet.md` 文件。而应确定流水线当前的运行/轮次编号 `N`（从 `workspace/.mantis_state.json` 中的 `"pass_number"` 解析得到。如果缺失或无效，则扫描 `workspace/archive/` 中匹配 `findings_pass_N` 或 `loopN_findings` 的文件夹，并将 `N` 解析为 `max_found + 1`，若不存在任何归档则默认为 1）。然后从 `active_snapshot.snapshot_id` 推导出 `<snapshot_tag>`：按原样取快照 ID，并将 `[A-Za-z0-9.-]` 中每个 NOT 的字符替换为单个下划线 `_`（NOT 截断——结果远低于任何文件名长度限制）。将报告写入：

     - 当 `active_snapshot.snapshot_id` 存在且非空时，写入 `workspace/report/review_packet_pass_<N>_<snapshot_tag>.md`（例如 `review_packet_pass_1_content_9f86d081884c...md`）。`<snapshot_tag>` 后缀可确保针对 DIFFERENT 快照复用同一轮次编号运行时写入不同的文件，且无法覆盖先前的评审包。
     - 当 `active_snapshot` 不存在或 `snapshot_id` 为空时，写入 `workspace/report/review_packet_pass_<N>.md`（无后缀——与旧版名称完全一致），以保持当前向后兼容的行为。

   - **最新副本/符号链接：** 在写入上述按轮次编号的报告后，将 THAT 确切文件（无论其是否带有 `<snapshot_tag>` 后缀）的符号链接更新或副本写入 `workspace/report/review_packet-latest.md`，以便最新版本始终可访问。`review_packet-latest.md` 名称保持不变，仍是任何下游使用方的稳定入口点。

   - 使用整洁、专业的 Markdown 格式，包含清晰的标题、用于元数据的表格，以及用于日志和差异的语法高亮代码块。

   - 在顶部包含一个高层次的 **Executive Summary** 表格，列出所有纳入的发现、其优先级及其风险评分。

   - **可选章节（推荐）：**

     - **本轮已解决项：** 使用相同的折叠方式生成的简短汇总，但仅筛选出 `patch_status` 为 `VERIFIED_SECURE` 或 `MITIGATION_PROPOSED` 的发现项。这样可在未解决发现项之外，为利益相关方提供一份可见的“已修复内容”视图。
     - **未解决——已达重试上限：** 一个标注块，列出复现尝试次数已达上限的未解决发现项。该计数 NOT 是发现项 JSON 上的字段；它存放在缓存文件 `state_root/workspace/archive/.repro_attempts.json` 中，以每个发现项的 `signature` 为键（或者，若 `signature` 缺失，则以其计算得出的 `stable_key` = `normalized_title + "@" + primary_file_path` 为键——与 `mantis-reproduce` 使用相同的键选择方式）。对于工作集中的每个未解决发现项，在该缓存中查找其 `signature`（或回退的 `stable_key`）；若缓存值已达重试上限，则将该发现项纳入此标注块。按 schema 的值形态规则读取缓存值：裸整数 V 表示 `{count: V, last_snapshot: UNKNOWN}`；对象表示 `{count: V.count, last_snapshot: V.last_snapshot or UNKNOWN}`。若缓存文件缺失或该发现项的键不存在，则将其计数视为 0（不要在此列出它）。这些是规划器已停止继续携带的条目

   > [!NOTE] **去重注意事项：** 去重依据发现项身份，遵循本阶段顶部的 **SAME-BUG PREDICATE**：两个发现项仅当 EITHER (i) 它们共享完全相同的 `id`（UUID），OR (ii) ALL THREE 条件均成立时才折叠——共享一个非空 `lineage_id`、共享一个非空 `signature`，AND 至少有一处包含行号的 `code_paths` 匹配。NEVER 仅凭 `lineage_id` 或仅凭 `signature` 折叠（由 basename 派生的谱系可能把两个同名的不同文件关联起来；`signature` 会剥离行号，因而会在同一文件的不同缺陷之间发生碰撞）——仅凭其中任一者合并都可能静默丢弃一个真实发现项。在新 UUID 下被重新发现、且 NOT 满足谓词 (ii) 的缺陷——例如回归、导致行号偏移的文件重命名，或非确定性的重新发现——会作为来自其归档祖先的 SEPARATE 条目列出：宁可多报（安全），绝不隐藏。在稳定的发现项签名与谱系跟踪落地后（阶段 3），当谓词 (ii) 完全满足时，被重新发现的发现项 ONLY 会折叠进其祖先的单一条目；对于旧版/未升级的发现项，仅凭 UUID 匹配仍是安全分支。

   - **`review_packet-latest.md` 为权威来源：** 请注意，`review_packet-latest.md` 现在是整个活动（而不仅是最新一轮）的权威当前未解决状态。每轮的 `review_packet_pass_<N>_<snapshot_tag>.md` 文件保持原样，供历史参考。
