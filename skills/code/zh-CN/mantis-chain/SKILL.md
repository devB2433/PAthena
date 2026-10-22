---
name: mantis-chain
description: >-
  分析单个安全发现，以识别并构造复杂的利用链。
  在验证阶段之后使用，以判断多个低严重性缺陷能否被组合成影响更高的漏洞。
  不要用于初始代码库审计或编写补丁代码。
---

# 漏洞链构造器（/mantis-chain）

## 系统目标

利用链架构师。分析孤立的、已单独验证的安全发现以及历史知识库原语，以识别并构造复杂的多步利用链。

## 命令定义

- **命令：** `/mantis-chain`
- **描述：** 分析单个安全发现，以识别并构造复杂的利用链。
- **参数（全部可选；由编排器依据 harness 生命周期契约提供——参见 `schema.json` 的“Non-JSON Contracts”）：**
  - `--snapshot_root` / `SNAPSHOT_ROOT` —— 本次执行所固定的、不可变的代码快照根目录。由 Block A 使用。
  - `--snapshot_id` / `SNAPSHOT_ID` —— 本次执行的快照标识字符串。由 Block A（哨兵）和 Block B（匹配检查）使用。
  - `--state_root` —— `workspace/` 的父目录。缺省时默认为当前目录（Block A 步骤 1c/1d）。
  - `--target_root` —— 一个已准备好的目录树，它 OVERRIDES 快照根目录（本阶段很少使用；由 Block A 步骤 1a 处理）。当传入的此类参数为 NONE 时，Block A 回退到 `workspace/.mantis_state.json` 中的 `active_snapshot`；如果该值也不存在，则本阶段以 MODE-OFF（`snapshot_pinned = false`）运行，行为与当前完全一致（参见向后兼容）。

## 输入/输出契约

- **读取**：
  - `workspace/findings/`（已验证的发现 JSON 文件，其中 status 为 `"VALID"`，且 viability 为 `"VIABLE"`、`"CONDITIONAL_VIABLE"` 或 `"SAMPLE_OR_TEST"`）。
  - `workspace/kb/entities/*.md` 和 `workspace/kb/vulnerabilities/*.md`（知识库原语）。
  - `workspace/.mantis_state.json`（用于跟踪当前循环轮次）。
- **写入**：
  - 将全新的利用链发现 JSON 文件写入 `workspace/findings/<new_uuid>.json`。原始发现保持不被修改。
- **前置条件**：
  - `workspace/findings/` 中必须存在已验证或可行的发现。
- **幂等性保证**：
  - 在写入新的利用链发现之前，本技能必须通过比较组成发现序列来检查已有的利用链发现。扫描 BOTH 当前 `workspace/findings/` 目录 AND `workspace/archive/findings_pass_*/` 下的每个已归档轮次 AND 旧式 `workspace/archive/loop*_findings/`（全部为 STATE-RELATIVE——绝不要加前缀 `CODE_ROOT`）。利用链 JSON 是指其 `constituent_findings` 数组存在且非空的任何发现。如果 EITHER 任一位置已存在具有 EXACT 相同有序组成序列的利用链，则跳过创建重复项。
  - **以签名为键的幂等判定（首选），以 code_paths 进一步区分：** 如果所有（ALL）组成风险都具有 `signature` 字段，则比较组成风险的有序 `signature` 序列（不比较 UUID）。只有（ONLY）在满足以下条件时（IF），才能将两条链视为相同（SAME）并跳过新建：有序 `signature` 序列相等，并且（AND）每对相应组成风险至少有一个 `code_paths` 条目完全相同；比较必须包含行号，即连同（WITH）其末尾的 `:line` 一起比较。若签名序列相同，但任意相应组成风险的 `code_paths` 不同，则两条链仍是不同的（DISTINCT），必须新建链。允许多报；`signature` 冲突绝不能压掉真正的新利用链，因为 `signature` 会去掉行号，可能使同一文件中的不同缺陷发生冲突。
  - **UUID 回退（当前行为）：** 如果 ANY 组成发现缺少 `signature` 字段，则回退为像以前一样精确比较有序的组成 UUID 序列。这为旧式/未升级的发现保留了当前行为。
  - 借助稳定的发现签名，此归档扫描现在完全有效：组成发现共享相同签名的两条链会在跨轮次时被正确去重。当签名缺失（旧式发现）时，该扫描仍保持 HARMLESS——它不会错误地抑制一条合法的新链（不会静默丢弃结果），而只是防止在恢复/重跑的轮次内发生精确 UUID 的重复。如果 `workspace/archive/` 目录不存在，则将归档集合视为空并继续。

## 指令

### 定位器解析（最先运行——所有阶段）

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

> [!NOTE] **CURRENT-PASS CHECK（防御性检查；约束性保证在于 harness，依据 `mantis-pipeline-adapter` 场景 2）：** 如果 `active_snapshot` 存在 AND `active_snapshot.pass != state.pass_number`，则将本次执行的快照视为 STALE——STOP “stale active_snapshot: pass mismatch”，或按 HALT 降级（`snapshot_pinned` 实际为 false：没有权威判定，Block B NOT_MATCHED，复现 `not_attempted`）。这可以捕获一种自定义 harness：它在 Stage 15 轮次递增过程中保留了 `active_snapshot` 而未重新固定。参考 meta-agent 每轮都会重新固定，因此该检查在那里永远不会触发。Block B 自身无法检测到这一点（它只感知 `snapshot_id`，不感知 `pass`）。

Block A 的阶段特定说明：

- `mantis-chain` NOT 仅处理发现的阶段（它不是 report/calibrate/reflect），因此 Block A 运行步骤 1–6，并从 `--snapshot_root` / `--snapshot_id` / 状态 `active_snapshot` 解析出 `CODE_ROOT`、`SNAPSHOT_ID` 和 `snapshot_pinned`。
- 本阶段 WRITES 仅将新的发现 JSON 写入 `workspace/findings/`（STATE-RELATIVE，Block A 步骤 3）。它从不在 `CODE_ROOT` 下写入，因此 Block A 步骤 4 自然满足。如果你可选地重新检查某个组成发现的 `code_paths` 以对利用链做合理性检查，请在 `CODE_ROOT` 下以 SNAPSHOT-RELATIVE 方式读取它们（Block A 步骤 3），绝不要 cd 到那里并写入。
- 利用链发现的 `code_paths` 是 COPIED 自其组成发现的原样内容（它们已经是 SNAPSHOT-RELATIVE）。不要重新加前缀、重新解析或重新编号。

### 快照匹配检查（用于选择组成发现）

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

在下方步骤 1 中，你将针对每个候选组成发现运行一次 Block B，并使用该发现的 `discovery_commit` 作为 `F.discovery_commit`。

读取当前这一批已验证的发现，并探究是否可以将多个看似低严重性或彼此不相关的漏洞按顺序组合，从而实现更高影响的入侵。

按如下方式执行链式组合阶段：

1. **加载原语与已验证发现：**

   - 读取 `workspace/findings/` 目录中的 JSON 文件。筛选出已通过验证的发现（例如状态为 `"VALID"`，且可行性为 `"VIABLE"`、`"CONDITIONAL_VIABLE"` 或 `"SAMPLE_OR_TEST"`）。
   - **快照资格门（NEW — 依据 `active_snapshot` 是否存在分支，3 状态模型）：**
     - **MODE-OFF**（状态中无 `active_snapshot` — 未请求 `--sync`，即当前默认情形）：完全 SKIP 此门：凡通过上述验证/可行性筛选的发现均为合格构成项，与当前行为完全一致。正常构建链。
     - **HALT**（`active_snapshot` IS 存在但 `snapshot_pinned == false`）：本轮 NOT 构建任何链。在 HALT 中，发现的定位符及前置/后置条件可能已失效（代码树发生竞态或无法固定），因此不得产出任何权威的链结论。记录 "no chain: HALT mode (active_snapshot present, snapshot_pinned=false)" 并干净地停止链式组合阶段（不产出任何链文件）。这与下方的最低资格守卫一致 — 链要求其构成项已针对快照得到证明，而 HALT 无法提供这一点。
     - **PINNED**（`active_snapshot` 存在 AND `snapshot_pinned == true`）：对 EACH 此类发现，使用其 `discovery_commit` 运行 **快照匹配检查（Block B）**。仅保留返回 **MATCHED** 的 ONLY 发现作为合格构成项。返回 **NOT_MATCHED** 的发现 — 包括任何 `discovery_commit` 缺失/为空、为字面量 `"MIXED"`，或与当前 `SNAPSHOT_ID` 不一致的发现 — 是针对 DIFFERENT 代码快照发现的；其 `file:line` 定位符及其前置/后置条件可能不再成立，因此本轮它 MUST NOT 被用作链的一环。
   - **最低资格守卫（NEW）：** 若剩余的合格构成项少于 2 个，本轮 **NOT** 构建任何链 — 链要求至少两个环节已针对 SAME 快照得到证明。记录 "no chain: fewer than 2 snapshot-matched constituents" 并干净地停止链式组合阶段（这不是错误；只是不产出任何链文件）。这与当前在可链发现少于两个时的行为一致。
   - 读取 Markdown 知识库（`workspace/kb/entities/` 和 `workspace/kb/vulnerabilities/`），以识别那些本身可能并非缺陷、但可作为垫脚石的架构原语（例如 "用户控制文件上传路径"、"服务以 root 身份运行"）。

2. **跨发现分析（链式组合矩阵）：**

   - 分析每个已验证发现的前置条件和后置条件。
   - 追问：*发现 A 的输出或副作用能否满足触发发现 B 所需的严格前置条件？*
   - 需要留意的示例链：
     - **路径遍历 + 宽松权限 = RCE：** 一个低严重性的路径遍历（发现 A）允许写入 `/tmp`，而另一处独立的错误配置（发现 B）允许 cron 作业执行 `/tmp` 中的脚本。
     - **XSS + CSRF = 账户接管：** 可利用一个存储型 XSS（发现 A）来窃取反 CSRF 令牌，从而执行改变状态的操作（发现 B）。
     - **信息泄露（内存泄露）+ 缓冲区溢出 = ASLR 绕过：** 信息泄露（发现 A）揭示了基址指针，满足了利用栈缓冲区溢出（发现 B）的前置条件。

3. **构建 "超级发现"：**

   - 若发现可行的利用链，**NOT** 修改或删除原始的孤立发现。它们仍需被单独修补。
   - **幂等性检查（在铸造 UUID BEFORE 运行）：** 应用上述幂等性保证 — 扫描当前的 `workspace/findings/` AND `workspace/archive/findings_pass_*/` AND 旧有 `workspace/archive/loop*_findings/`，查找其构成项序列等于本链有序构成项序列的链。若 ALL 构成项均具有 `signature`，则比较有序 `signature` 序列 AND 要求采用幂等性保证中所述的逐构成项、含行号的 `code_paths` 决胜规则（仅签名序列匹配 ALONE 是 NOT 足以跳过的）；若 ANY 构成项缺少 `signature`，则回退到有序构成项 UUID 序列（当前行为）。仅当某匹配满足决胜规则时，才 SKIP 此链（不创建文件）并继续处理下一个候选。
   - 而是生成一个**全新的 UUID**，并在 `workspace/findings/<new_uuid>.json` 中创建一个新的发现 JSON 文件。
   - **构成项发现**：必须在结构化属性 `"constituent_findings"` 中记录构成项发现 UUID 的数组（例如 `["UUID_A", "UUID_B"]`）。这清晰地记录了利用链的各环节。
   - **确定发现快照（`discovery_commit`）**：根据链发现的 ELIGIBLE 构成项（即在步骤 1 中保留的那些）设置其 `discovery_commit`：
     - **MODE-OFF 短路（3 状态规则）：** 如果 `active_snapshot` 在状态中为 ABSENT（MODE-OFF —— 未请求 `--sync`），则对链发现完全 OMIT `discovery_commit`。在 MODE-OFF 中，researcher 对每个组成项都 OMITS `discovery_commit`（`researcher:317,340-341`：“如果 active_snapshot 缺失或 snapshot_pinned 为 false，则完全 OMIT 此键”），因此每个组成项都有一个缺失/空的 `discovery_commit`。于是下面的 SAME/MIXED 逻辑会在每条链上写入字面量 `"MIXED"` —— 这是快照时期产生的产物，在 Phase 1 中并不存在，并且违反了 MODE-OFF 的“与今日行为逐字节一致”保证（`schema.json:4`）。链自身的 schema 注释（`chain:307`）写道“在 legacy/unpinned 运行中缺失”，这确认了预期的 MODE-OFF 行为是省略，而不是 `"MIXED"`。（功能影响较低 —— `schema.json:232` 将“缺失”和 `"MIXED"` 都视为 NOT_MATCHED —— 但模式纯净性很重要。）NOT 写入 `"MIXED"` 或 MODE-OFF 中的任何值。
     - 如果 ALL 个符合条件的组成项都具有 SAME 的非空 `discovery_commit` 值，则将链的 `discovery_commit` 设置为该确切字符串。
     - 如果各组成项的 `discovery_commit` 值彼此 DIFFER，OR 任一符合条件的组成项具有缺失/空的 `discovery_commit`（仅在 PINNED/HALT 模式下可达 —— 在 MODE-OFF 中，上述短路逻辑已 OMIT 该字段），则将链的 `discovery_commit` 设置为字面字符串 `"MIXED"`。NOT 发明、哈希或模糊归一化此值 —— 要么复制确切字符串，要么写入字面量 `"MIXED"`。理由：在 pinned 模式下，每个符合条件的组成项都是 MATCHED，因此它们都共享当前的 `SNAPSHOT_ID`，链继承该值（随后链上的 Block B 会在本次处理中 MATCHES 它）。`"MIXED"` 哨兵值使 Block B 对链返回 NOT_MATCHED（Block B 步骤 2 将字面量 `"MIXED"` 视为 NOT_MATCHED）—— 对于无法针对单一快照证明其各环节的链而言，这是安全分支。
   - **计算 `signature`（链的确定性内容标识哈希）：**
     - 从每个 ELIGIBLE 组成项发现中收集 `signature` 字段（与步骤 1 中用于 `discovery_commit` 的集合相同）。
     - 如果 EVERY 个符合条件的组成项都具有非空 `signature`：按字典序（升序）对组成项签名排序，然后计算 `signature` = `sha256("chain|" + "|".join(sorted_constituent_signatures))` 的前 16 个十六进制字符。`"chain|"` 前缀可防止与非链发现的 `signature` 发生任何碰撞（后者由 title+cwe+target 哈希而来，属于不同的输入域）。排序使链签名在组成项重新排序时保持不变。
     - 如果 ANY 个符合条件的组成项缺少 `signature` 字段（legacy / 未升级的发现）：将链的 `signature` 设置为 EMPTY STRING，并将其从 JSON 中 OMIT（下游消费者随后按 `schema.json:256` 回退到基于 UUID 的折叠/去重）。这是安全分支 —— 它绝不会导致错误的折叠，只会导致过度报告。
     - 在创建链时 ONCE 计算一次签名，NEVER 重新计算、编辑或发明它（与 `discovery_commit` 以及常规发现的 `researcher:241-242` 规则相同）。
   - **计算 `lineage_id`（跨轮次链谱系）：**
     - 扫描 `workspace/archive/findings_pass_*/` AND `workspace/archive/loop*_findings/` 中的任何已归档链发现 JSON（任何具有非空 `constituent_findings` 数组的发现），其 `signature` 字段 EXACTLY 等于此链计算出的 `signature`。
     - 如果找到匹配：`lineage_id` = 已归档祖先的 `lineage_id`（继承谱系链，以便报告可跨轮次折叠）。如果 MULTIPLE 个已归档链共享同一 `signature`，则从 MOST RECENT（轮次编号最高）的祖先继承。所有具有相同链 `signature` 的祖先 SHOULD 共享同一 `lineage_id`；如果它们不共享，则从最近的一个继承并记录一条警告。
     - 如果无匹配（包括此链的 `signature` 为 EMPTY STRING 的情况）：`lineage_id` = 一个新的 UUIDv4。
     - 这些是 STATE-RELATIVE 路径（Block A 步骤 3）—— 在 `--state_root/workspace/archive/` 下读取，NEVER 在 CODE_ROOT 下读取。（扫描目标与算法形态与常规发现的 `researcher:246-272` 相同 —— 链只是跳过了基于 basename 归一化的重命名回退，因为链的身份是其组成项的集合，而不是单个文件路径。）
   - **确定入口点权限**：链的 `privileges_required` 字段必须表示发起链的*第一步*（入口点）所需的权限级别。例如，如果链以未认证利用（NONE）开始，进而获得管理员访问权限，随后用于触发 RCE，则链的 `privileges_required` 必须设置为 `NONE`。
   - **确定攻击者位置**：链的 `attacker_position` 字段必须从链的入口点 / 第一个组成项发现继承攻击者位置。
   - **确定用户交互要求**：如果入口点或链中任一步骤需要用户交互，则链的 `user_interaction` 字段必须设置为 `REQUIRED`。只有当整条链都是零点击时，才应将其设置为 `NONE`。
   - **确定状态**：将 `status` 设置为 `"VALID"`。
   - **确定生产可行性**：从组成项发现继承。如果任一组成项为 `"SAMPLE_OR_TEST"`，则设置为 `"SAMPLE_OR_TEST"`。否则，如果任一组成项为 `"CONDITIONAL_VIABLE"`，则设置为 `"CONDITIONAL_VIABLE"`。否则，设置为 `"VIABLE"`。
   - **确定复现状态**：从组成项发现继承：
     - 如果任一组成项的 `repro_status` 为 `"failed_to_reproduce"` 或 `"not_attempted"`，则设置为 `"not_attempted"`。
     - 否则，如果所有组成项的 `repro_status` 均为 `"reproduced"` 或 `"statically_confirmed"`，则设置为 `"statically_confirmed"`。
     - 利用链**绝不**得继承 `"reproduced"`（因为组成项的复现并不能证明端到端链有效）。

   ### 链发现 Schema 格式（按文件）

   ```json
   {
     "id": "A unique identifier generated for this chain finding. Must match filename.",
     "title": "Exploit Chain: [Impact] via [Finding A] and [Finding B]",
     "description": "Step-by-step documentation of the exploit chain. Start with Step 1 (Triggering Finding A) and explain how its outcome feeds into Step N (Triggering Finding Z).",
     "impact": "The combined, escalated impact of the chain (e.g., Remote Code Execution, Full Database Exfiltration). This should be higher than the individual findings.",
     "severity": "CRITICAL / HIGH",
     "privileges_required": "NONE / LOW / HIGH",
     "user_interaction": "NONE / REQUIRED",
     "code_paths": [
       "relative/file/path_A.c:line_number",
       "relative/file/path_B.c:line_number"
     ],
     "attacker_position": "EXTERNAL / LOCAL / etc. (inherited from entry point)",
     "mitigation": "Recommended strategy to break the chain. Usually involves fixing at least one, if not all, of the underlying links.",
     "status": "VALID",
     "production_viability": "VIABLE / SAMPLE_OR_TEST / CONDITIONAL_VIABLE",
     "repro_status": "statically_confirmed / not_attempted",
     "constituent_findings": ["UUID_A", "UUID_B"],
     "signature": "First 16 hex chars of sha256(\"chain|\" + \"|\".join(sorted(constituent_signatures))) if EVERY constituent has a non-empty `signature`; else the EMPTY STRING (absent-equivalent -> downstream falls back to UUID-only behavior). Computed ONCE at chain creation and NEVER recomputed (same rule as discovery_commit). See the signature/lineage computation steps above.",
     "lineage_id": "Inherited from the most-recent archived chain finding whose `signature` equals this chain's `signature` (scan workspace/archive/findings_pass_*/ AND workspace/archive/loop*_findings/); else a fresh UUIDv4. See the signature/lineage computation steps above.",
     "discovery_commit": "The SNAPSHOT_ID shared by all constituents, or the literal \"MIXED\" if they differ / are missing (only computed in PINNED/HALT mode). OMITTED (absent) on legacy/unpinned runs (MODE-OFF) — see the MODE-OFF short-circuit in the discovery_commit computation step above.",
     "history": [
       {
         "stage": "chainer",
         "action": "created",
         "details": "Constructed by chaining findings [UUID_A] and [UUID_B].",
         "pass_number": <current_pass_number>,
         "timestamp": "<current_iso8601_timestamp>"
       }
     ]
   }
   ```

4. **链式去重标记：**

   - 为确保 `/mantis-dedupe` 将这些链与原始发现区别对待，须确保 "Chain" 一词在 `"title"` 和 `"history"` 字段中显著出现，如 schema 所示。

确保任何新构建的链文件都写入 `workspace/findings/` 目录。完成后，通知用户。
