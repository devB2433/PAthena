---
name: mantis-review
description: >-
  独立审查发现并过滤掉误报。
  当需要对照实际源代码验证已汇总的发现时使用。
  不要用于复现崩溃或修补代码。
---

# Reviewer（/mantis-review）

## 系统目标

独立验证者。对照当前源代码审查已汇总的发现，以核实其有效性并过滤掉噪声和误报。

## 命令定义

- **命令：** `/mantis-review`
- **描述：** 独立审查发现并过滤掉误报。

## 输入/输出契约

- **读取**：
  - `workspace/findings/`（发现 JSON 文件，包括每个发现可选的 `discovery_commit` 来源标记）。
  - `workspace/.mantis_state.json`（当前循环的 `pass_number`；以及，若存在，用于快照来源的 `active_snapshot` = `{root, snapshot_id, snapshot_pinned}`）。
  - 由编排器传入时的调用参数：`--snapshot_root`、`--snapshot_id`、`--state_root`（以及，极少情况下，`--target_root`）。
  - 位于 `code_paths` 路径/行处的目标源代码文件，须在解析后的 `CODE_ROOT`（已固定的快照根目录——见 Instructions 步骤 0 / Block A）下读取；当快照已固定时，绝不读取活动工作树。
- **写入**：
  - 就地更新磁盘上的发现文件（设置 `"status"`、`"reasoning"`、`"repro_hints"`、`"triage_checklist"`，并追加历史记录）。
  - 写入辅助脚本 `workspace/helpers/append_review.py`。
- **前置条件**：
  - `workspace/findings/` 存在且包含发现文件。
  - 目标源代码文件存在。
- **幂等性保证**：
  - 使用辅助脚本 `append_review.py` 就地修改发现文件，该脚本 MUST 以 `# MANTIS_HELPER_VERSION = 2` 作为其第一行；若该标记缺失或为不同的整数，则重新生成该辅助脚本（见 Instructions 步骤 5）。
  - 幂等键为 **(pass_number, snapshot)**。在（重新）写入某个发现之前，扫描其 `history` 数组：
    - **SNAPSHOT MODE OFF**（既未传入 `--snapshot_id`，NOR 状态中也没有可读的 `active_snapshot`）：当且仅当已存在针对当前 `pass_number` 的 `"stage": "reviewer"` 条目时，SKIP 该审查更新（与今日行为逐字节一致）。
    - **SNAPSHOT MODE ON**：当且仅当存在一个 `"stage": "reviewer"` 条目，其 `pass_number` 等于当前 pass 的 AND，且其 `snapshot` 等于 `SNAPSHOT_ID` 时，SKIP 该审查更新。缺少 `snapshot`（旧版）或携带不同 `snapshot` 的 reviewer 条目视为 UNKNOWN → RE-REVIEW（NOT 跳过），因此在旧技能下审查过的发现会针对当前已固定的快照重新建立依据。

## 指令

对照仓库的实际源代码读取并评估去重后的发现。**默认假定每个发现都是误报。你的任务是以对抗性立场证伪该发现。ONLY 依据代码和原始声明本身来评估该主张。明确忽略原始发现者的文字推理与论证，因为它们可能是幻觉。**

按如下方式执行验证：

**步骤 0 — 定位符解析与快照来源门禁（FIRST 执行此步，在读取任何发现的代码之前）：**

0a. **确定从哪里读取代码。** 此处为 **Block A — 定位符解析**：

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

reviewer READS 目标源代码，因此它 NOT 仅处理发现的阶段：执行 Block A 的全部步骤 1-6。`CODE_ROOT` 是你将在其下读取每个 `code_paths` 条目的已固定快照根目录。按 Block A 步骤 3，包含 `://` 的 `code_paths` 条目是 URL（仅做存在性检查），任何不是 `<existing-path>:<integer>` 形式的条目都是非源代码定位符（检查产物/符号是否存在；跳过所有行存在性逻辑）。绝不因为 `CODE_ROOT` 缺少 `.git`/`.hg` 就 STOP（Block A 步骤 5）。

> [!NOTE] **CURRENT-PASS CHECK（防御性检查；绑定保证由 harness 按 `mantis-pipeline-adapter` 场景 2 承担）：** 若 `active_snapshot` 存在 AND `active_snapshot.pass != state.pass_number`，则对本 pass 将该快照视为 STALE——STOP 并报“stale active_snapshot：pass 不匹配”，或降级为 HALT（`snapshot_pinned` 实际为 false：无权威判定，Block B NOT_MATCHED，复现 `not_attempted`）。这可以捕获在 Stage 15 pass 递增过程中保留了 `active_snapshot` 却未重新固定的自定义 harness。参考 meta-agent 每个 pass 都会重新固定，因此该检查在那里永不触发。Block B 本身无法检测此情况（它仅感知 `snapshot_id`，不感知 `pass`）。

0b. **确定 SNAPSHOT MODE（一次性，在接触任何发现之前）：** - SNAPSHOT MODE 为 **OFF**，当且仅当本次调用中 `--snapshot_id` 被 NOT 传入 AND `workspace/.mantis_state.json` 没有可读的 `active_snapshot`。这是旧版/非同步运行：NOT 运行下方的快照来源门禁；完全按以往方式用步骤 1-5 审查 EVERY 发现。 - 否则 SNAPSHOT MODE 为 **ON**。此时你已从 Block A 获得 `SNAPSHOT_ID` 和 `snapshot_pinned`。对每个发现应用快照来源门禁（0c）。

0c. **快照来源门禁（仅 SNAPSHOT MODE ON）。** 在步骤 1 加载发现之后，对 EACH 发现 F 应用匹配检查，即对 F 应用 **Block B — 快照匹配检查**：

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

然后，使用幂等性规则（输入/输出契约 → 幂等性保证）以避免重复写入：

- **MATCHED** → F 已基于当前已固定的快照建立依据。对 F 继续执行步骤 2-5（完整的 13 规则审查）。
- **NOT_MATCHED**（包括：`snapshot_pinned` 为 false / HALT / 活动端点 pass；`discovery_commit` 缺失、为空或为字面量 `MIXED`；或 `discovery_commit != SNAPSHOT_ID`）→ F 是针对不同（或缺失/未固定）的快照发现的；其 `code_paths` 行号可能已不再指向同一代码。**DO NOT 对 F 运行 13 规则。DO NOT 将其标记为 FALSE_POSITIVE。** 通过辅助脚本（步骤 5）将 F 最终确定为漂移 `NEEDS_RESEARCH`，写入 EXACTLY：
  - `"status": "NEEDS_RESEARCH"`
  - `"reasoning"`:
    `"Snapshot drift: finding discovered against snapshot <F.discovery_commit or 'unknown'>, which does not match the current pinned snapshot <SNAPSHOT_ID>. Not re-validated this pass; routed to re-research."`
  - 省略 `"repro_hints"`（并且，若 F 携带了来自先前 pass 的过期 `repro_hints`，则保留它们——下游会忽略它们，因为 status 不是 VALID/PROVISIONALLY_VALID）。
  - `"triage_checklist"`：将全部 13 个约束都设为 `UNKNOWN`（逐字粘贴下方对象）。该对象是 REQUIRED 的：追加一条 `reviewer` 历史记录会使 `triage_checklist` 在非链式发现上成为强制项（`schema.json` 第 365-398 行），而对于 `NEEDS_RESEARCH` 发现，每一条记录 MUST 避免 `FAIL`/`passes:false`（`schema.json` 第 418-470 行）。将 `ensure_source_code_coherence` 和 `verify_attacker_control_of_source` 设为 `UNKNOWN`（绝不设为 `FAIL`）就是全部
    point：由 DRIFT 引起的行/路径不匹配 NOT 幻觉证据，因此规则 12/13 必须 NOT 对其触发。
  - 追加 `reviewer` `"history"` 条目（按步骤 5 / 下方历史 JSON 的要求，带有 `"snapshot"`）。然后 STOP 处理 F——对其跳过步骤 2-5。

漂移 `triage_checklist`（逐字粘贴；全部 13 个键，全部为 `UNKNOWN`）：

```json
{
  "ignore_hypothetical_misuse":        { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "ignore_missing_hygiene":            { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "require_strict_reproducibility":    { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "avoid_pedantic_linting":            { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "no_security_flaw_stretching":       { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "evaluate_questionable_file_paths":  { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "ignore_resource_exhaustion_dos":    { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "intrinsic_security_flaws":          { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "verify_mitigations_pragmatically":  { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "refine_code_paths_strictly":        { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "ignore_simd_vector_padding":        { "outcome": "UNKNOWN", "reason": "snapshot drift: not re-validated against the current pinned snapshot" },
  "ensure_source_code_coherence":      { "outcome": "UNKNOWN", "reason": "snapshot drift: line/path mismatch may be caused by drift, not hallucination; not re-validated" },
  "verify_attacker_control_of_source": { "outcome": "UNKNOWN", "reason": "snapshot drift: trust-boundary path not re-traced against the current pinned snapshot" }
}
```

在步骤 0c 中被最终判定为漂移 `NEEDS_RESEARCH` 的发现即为 DONE——不要在步骤 2-5 中再次处理它们。只有 MATCHED 发现（SNAPSHOT MODE ON）或全部发现（SNAPSHOT MODE OFF）才会流入下方步骤 2-5。

1. **加载聚类发现：** 读取 `workspace/findings/` 目录中的 JSON 文件。如果该目录为空或不存在，则通知用户。

2. **源代码检查：** 对于到达此步骤的每个发现（在 SNAPSHOT MODE ON 下，仅限在步骤 0c 中被标记为 **MATCHED** 的发现；在那里被最终判定为漂移 `NEEDS_RESEARCH` 的发现已完成并被跳过），读取 **`CODE_ROOT` 之下**的文件（即在步骤 0 / Block A 中解析出的固定快照根目录——将每个 `code_paths` 路径都相对于 `CODE_ROOT` 解析，绝不使用实时目录树），以检查 `code_paths` 中列出的确切文件和行号，并确认该发现确实基于实际快照状态。在未先检查源代码之前，不要对路径的有效性做出假设。

3. **严格验证过滤（应用 13 条否定性约束）：** 依据以下严格标准评估每个发现。如果某个发现违反了以下任一规则，则将其标记为 **FALSE_POSITIVE**：

   01. **忽略假设性误用：** 如果函数本身行为安全，不要将依赖调用方 API 假设性地误用函数、编写糟糕的兜底逻辑或发送无效参数的安全缺陷标记出来。
   02. **忽略缺失的卫生措施 / 纵深防御：** 不要将缺失 HTTP 安全响应头（例如 `X-Content-Type-Options`）、仅本地测试函数缺失身份验证，或硬编码的模拟数据库报告为安全缺陷。
   03. **要求严格可复现性：** 只有当代码逻辑边界内存在直接、明确且可触发的缺陷时，才将发现标记为 VALID。如果该发现极其脆弱（例如依赖无法自动化或暴力破解的不稳定时序，或需要不现实的环境条件才能触发），则将其标记为 FALSE_POSITIVE。*关于竞态条件的说明：* 不要 NOT 仅因成功概率低（例如百万分之一 1）就否定竞态条件或时序缺陷，前提是攻击路径可被攻击者自动化并反复尝试，从而最终触发该漏洞利用。
   04. **避免迂腐的代码风格检查：** 如果代码使用了标准安全库（例如 `json.loads`、参数化的 SQL 查询或安全的标准库哈希），但缺少极端的偏执式防护，则将其标记为 FALSE_POSITIVE。
   05. **不得在缓解措施上牵强附会地寻找安全缺陷：** 如果你正在审查的是某个缓解措施或某个函数的安全变体，且该缓解措施成功阻断了原有的安全缺陷类别，则不要 NOT 臆造复杂的协议级绕过或相邻的安全缺陷类别（例如在审查命令注入修复时臆造 SSRF）。如果主要安全缺陷已被成功阻断，则将其标记为 FALSE_POSITIVE。
   06. **评估可疑的文件路径：** 不要 NOT 仅因某个发现的路径包含 `/test`、`/experimental` 或 `/mock` 就立即将其排除。这些路径中的代码有时会被编译进生产目标，或可通过生产端点访问。不要盲目假定其安全；而应采取合理措施追踪其使用情况，以确认它是否实际暴露在生产环境中。
   07. **忽略资源耗尽型 DoS：** 除非该模块的主要既定目的是防御 DoS 攻击，否则不要将缺少递归限制、输入大小边界或循环约束的函数标记出来。
   08. **内在安全缺陷：** 如果某个函数使用了根本性损坏的算法（例如 MD5、SHA1）、硬编码静态密钥，或在其自身逻辑中包含直接的注入路径，则即使它当前在代码库中任何地方都未被调用，也要将其标记为 VALID。
   09. **务实验证缓解措施：** 不要臆造有效缓解措施中的缺陷。如果代码添加了尾部校验斜杠或配置了安全解析标志，则应认可该缓解措施有效。
   10. **严格精炼 `code_paths`：** `code_paths` 字段应只包含有缺陷代码块的确切 `filename:line_number`。从 `code_paths` 中剔除任何辅助文件、测试框架或正确的调用方文件。
   11. **忽略 SIMD/向量填充违规：** 如果某个发现表示优化向量例程（例如 NEON、SSE、AVX、VSX）内部的越界读取或写入，请核实该库是否采用全局内存分配契约（例如尾部安全填充，如 `row_bytes + 16`）。如果在所有执行路径下，该越界访问在数学上保证完全位于此预分配的填充缓冲区内，则将该发现标记为 FALSE_POSITIVE（设计使然）。
   12. **确保源代码一致性（防幻觉）：** 验证 `code_paths` 中列出的每个文件路径都存在于仓库中，并且函数名、变量名或行号确实存在于这些位置。如果引用缺失或不正确，立即将该发现标记为 FALSE_POSITIVE，以防止下游智能体在幻觉出的缺陷上浪费资源。
   13. **验证攻击者对源头的控制（信任边界追踪）：** 在将数据流发现标记为 VALID 之前，识别并引用不可信数据进入被分析代码库的位置（即“入口点”，Ingress Point）的 file:line，该位置是特定污点字段的值流向 sink 的起点，OR 引用该字段由不可信写入方填充的位置。
       - 如果你能访问不可信侧的代码（例如多组件仓库中的 Guest/Client），请引用写入方。
       - 如果你只能访问可信侧的代码，请引用数据流路径上的 Host/Server 入口点（例如从共享内存读取、IPC 处理程序、HTTP 请求参数获取）。
       - 如果源数据被证明仅源自可信侧来源（服务器编写的静态配置、主机平面内部状态），则标记为 FALSE_POSITIVE。
       - 例外：不要将本规则应用于内在安全缺陷（规则 08），此类漏洞存在于库代码中，与活跃调用方无关。

   - **状态判定：**

     - 如果违反上述 13 条规则中的任何一条，则标记为 **FALSE_POSITIVE**。
     - 如果通过所有规则且存在清晰、可触发的缺陷，则标记为 **VALID**。
     - 如果通过规则，但在未经动态验证的情况下无法确定其可行性（例如需要复杂的堆布局或精确的时序），则标记为 **PROVISIONALLY_VALID**。
     - 如果由于高复杂度、未解析的外部 API 或庞大的调用图而导致审查无法得出结论，则标记为 **NEEDS_RESEARCH** —— OR 因为该发现被 Step 0c Snapshot Provenance Gate 判定为 **NOT_MATCHED**（快照漂移）。漂移发现会在 Step 0c 中最终确定，永远不会到达这些 13 条规则；请 NOT 将漂移发现重新分类为 `FALSE_POSITIVE`。
     - **SCHEMA-CRITICAL：** `FALSE_POSITIVE` 是 ONLY 一种状态，在该状态下 `triage_checklist` 条目可以为 `"FAIL"`（或 `"passes": false`）。对于任何 `VALID`、`PROVISIONALLY_VALID` 或 `NEEDS_RESEARCH` 发现，EVERY 清单条目都必须为 `PASS` / `NOT_APPLICABLE` / `UNKNOWN` —— 绝不能为 `FAIL` —— 否则 `schema.json`（第 418-470 行）将拒绝该发现。如果某条规则看起来未通过，但你 NOT 将状态设为 `FALSE_POSITIVE`（例如规则 12/13 的引用看起来有问题仅仅是因为快照漂移），请使用 `UNKNOWN` 并附上 `reason`，而不是 `FAIL`。

   - **清单构建：**

     - 构建 `triage_checklist` 对象，评估全部 13 条否定性约束。对于每条规则，将 `outcome` 设置为：
       - `"PASS"`：如果该发现满足该约束（未违反该规则，即该缺陷仍可能有效）。
       - `"FAIL"`：如果该发现违反该规则。将 ANY 条目设置为 `"FAIL"` REQUIRES 该发现的 `status` 为 `FALSE_POSITIVE`（`schema.json` 第 418-470 行会在 `VALID`/`PROVISIONALLY_VALID`/`NEEDS_RESEARCH` 上拒绝 `FAIL`）。`FAIL` 条目还 REQUIRES 一个 `reason`（`schema.json` 第 788-797 行）。
       - `"UNKNOWN"`：如果规则适用性尚未确定/需要研究（REQUIRES 一个 `reason`）。只要该发现未被标记为 `FALSE_POSITIVE`，就使用此项——而不是 `"FAIL"`——包括漂移 `NEEDS_RESEARCH` 发现（Step 0c）上的所有规则。
       - `"NOT_APPLICABLE"`：如果该规则与此类缺陷完全无关（REQUIRES 一个 `reason`）。
     - 一致性规则：如果 `status` 为 `VALID`，则每个条目都必须为 `PASS` 或 `NOT_APPLICABLE`（不得为 `UNKNOWN`，不得为 `FAIL`），依据 `schema.json` 第 471-507 行。

4. **构建复现脚本提示：** 对于每个标记为 **VALID** 或 **PROVISIONALLY_VALID** 的发现，提供高信号量的 `"repro_hints"`，说明复现智能体如何触发该缺陷、需要哪些输入或载荷参数，以及预期通过何种崩溃条件、sanitizer 跟踪（ASan/UBSan/MSan/TSan）或功能验证结果（例如意外的 HTTP 200 OK）来确认该安全缺陷。

5. **令牌优化的文件更新：** 为尽量减少 LLM 输出令牌，**不要在输出中重新输出或手动重写整个 JSON 对象。** 相反，在首次更新发现时编写一个可复用的辅助脚本（例如 `workspace/helpers/append_review.py`）。对于所有后续发现，不要重新生成该脚本；只需使用新参数执行现有的辅助脚本，以追加所需字段。

   **辅助脚本版本防护（机械性）：** 辅助脚本的 FIRST 行 MUST 是注释 `# MANTIS_HELPER_VERSION = 2`。在复用现有的 `workspace/helpers/append_review.py` 之前，检查该首行（一行 grep）：如果该标记为 ABSENT 或为 DIFFERENT 整数，则 REGENERATE 该辅助脚本（旧的持久化辅助脚本会静默省略 `snapshot` 溯源字段并错误处理漂移路径）。重新生成的辅助脚本 MUST：

   - 接受 `status`、`reasoning`、可选的 `repro_hints`、`triage_checklist`（JSON）、`pass_number`、`timestamp` 和 `snapshot` 的参数（将其写入键 `"snapshot"` 下，绝不要写入 `"snapshot_id"`）；
   - 写入审查者 `history` 条目 INCLUDING `"snapshot"`（即当前的 `SNAPSHOT_ID`；仅在 SNAPSHOT MODE OFF 中省略或写入 `""`，此时不涉及快照）；
   - 强制执行（pass_number, snapshot）幂等性检查（幂等性保证）：如果已存在匹配的审查者条目，则不执行任何操作。

   你必须将以下内容追加到现有对象中：

   - 一个 `"status"` 字段（取值为 `"VALID"`、`"FALSE_POSITIVE"`、`"PROVISIONALLY_VALID"` 或 `"NEEDS_RESEARCH"` 之一）。

   - 一个 `"reasoning"` 字段。

   - 一个 `"repro_hints"` 字段（对于 `"NEEDS_RESEARCH"` 或 `"FALSE_POSITIVE"` 为可选）。

   - 一个 `"triage_checklist"` 对象，包含对全部 13 条否定约束的评估结果（该对象中的每个键都映射到上文第 3 节中同名约束）：

     ```json
     {
       "ignore_hypothetical_misuse": { "outcome": "PASS" },
       "ignore_missing_hygiene": { "outcome": "PASS" },
       "require_strict_reproducibility": { "outcome": "FAIL", "reason": "Requires unstable 1-in-a-million race condition that cannot be automated." },
       "avoid_pedantic_linting": { "outcome": "PASS" },
       "no_security_flaw_stretching": { "outcome": "PASS" },
       "evaluate_questionable_file_paths": { "outcome": "PASS" },
       "ignore_resource_exhaustion_dos": { "outcome": "PASS" },
       "intrinsic_security_flaws": { "outcome": "PASS" },
       "verify_mitigations_pragmatically": { "outcome": "PASS" },
       "refine_code_paths_strictly": { "outcome": "PASS" },
       "ignore_simd_vector_padding": { "outcome": "PASS" },
       "ensure_source_code_coherence": { "outcome": "PASS" },
       "verify_attacker_control_of_source": { "outcome": "PASS" }
     }
     ```

     为保持向后兼容，schema 也允许以 `"passes": <bool>` 作为 `"outcome"` 的替代写法，但优先使用 `"outcome"`。如果结果为 `FAIL`、`UNKNOWN` 或 `NOT_APPLICABLE`（或者 `passes` 为 `false`），则必须提供 `reason` 以说明评估依据；对于 `PASS` / `true` 则应省略它，以节省 token。

   - 向 `"history"` 数组添加一个条目（现在会携带本次审查所针对的快照，用于 `(pass_number, snapshot)` 幂等性）：

   ```json
   {
     "stage": "reviewer",
     "action": "reviewed",
     "details": "Determined status as [VALID/FALSE_POSITIVE/PROVISIONALLY_VALID/NEEDS_RESEARCH] because [reason]",
     "pass_number": <current_pass_number>,
     "snapshot": "<SNAPSHOT_ID>",
     "timestamp": "<current_iso8601_timestamp>"
   }
   ```

   在 SNAPSHOT MODE OFF 下（不涉及快照），设置 `"snapshot": ""`（或省略该字段）。`schema.json` 的 `history_entry` 没有 `additionalProperties:false`，因此该额外字段可原样通过校验。

完成后，通知用户。
