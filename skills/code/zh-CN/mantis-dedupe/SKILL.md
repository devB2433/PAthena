---
name: mantis-dedupe
description: >-
  整合原始安全发现，以消除冗余报告。
  当原始发现已由研究员生成、且需要在评审前进行整合时使用。
  不要用于初始代码审计或补丁生成。
---

# 去重器（/mantis-dedupe）

## 系统目标

重复发现合并器。评估原始发现列表，将完全相同或高度重叠的问题聚类并整合为单一、具描述性的记录。

## 命令定义

- **命令：** `/mantis-dedupe`
- **描述：** 整合原始安全发现，以消除冗余报告。
- **参数（可选；由编排器提供，由 Block A 消费）：** `--snapshot_root`/`--snapshot_id`/`--state_root`。全部缺失 -> MODE-OFF/legacy 模式（行为与当前一致；快照门控禁用）。

## 输入/输出契约

- **读取**：
  - `workspace/findings/`（原始发现 JSON 文件，忽略 `.trash/`）。
  - `workspace/archive/findings_pass_*/*.json` 和 `workspace/archive/loop*_findings/*.json`（用于跳过在先前各轮次中已评估并已分诊的发现）。
  - `workspace/.mantis_state.json`（用于跟踪当前循环轮次）。
- **写入**：
  - 在设置 `"status": "DUPLICATE"` 和 `"duplicate_of"` 之后，将重复发现移动到 `workspace/findings/.trash/`。
  - 对 NOT_MATCHED 匹配项设置 `"possible_duplicate_of"`（软标记，非终态），并为缺少 `"discovery_commit"` 的当前发现打上该标记。从 `.mantis_state.json` 读取 `active_snapshot`/`snapshot_pinned`。
  - 将事务日志追加到 `workspace/.tx_log.jsonl`。
  - 生成/执行合并脚本 `workspace/helpers/merge_findings.py`。
  - 更新主发现 `workspace/findings/<primary_id>.json`（合并字段与历史）。
- **前置条件**：
  - `workspace/findings/` 必须存在且包含发现文件。
- **幂等性保证**：
  - 与 `workspace/archive/` 中的归档发现进行交叉引用，以过滤掉本次运行先前各轮次中已处理的任何发现。
    Snapshot-gated：在不同快照上已解决的归档发现会被标记为 POSSIBLE REGRESSION（绝不静默过滤）。将事务记录到 `workspace/.tx_log.jsonl`，以支持跟踪和可能的回滚。确定性合并规则在 `merge_findings.py` 中实现。

## 指令

### 步骤 0：定位器解析（首先运行）

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

> [!NOTE] **CURRENT-PASS CHECK（防御性检查；约束性保证在于 harness，依据 `mantis-pipeline-adapter` 场景 2）：** 如果 `active_snapshot` 存在 AND `active_snapshot.pass != state.pass_number`，则在本轮次中将该快照视为 STALE —— STOP 并报 "stale active_snapshot: pass mismatch"，或降级为 HALT（`snapshot_pinned` 实际为 false：无权威判定，Block B NOT_MATCHED，复现状态为 `not_attempted`）。此检查用于捕获这样一种自定义 harness：它在 Stage 15 轮次递增过程中保留了 `active_snapshot` 却未重新固定。参考 meta-agent 每轮次都会重新固定，因此该检查在那里永远不会触发。Block B 自身无法检测此情况（它只感知 `snapshot_id`，不感知 `pass`）。

Notes：`workspace/findings/`、`workspace/archive/`、`workspace/.tx_log.jsonl`、`workspace/helpers/` 和 `.mantis_state.json` 均为 STATE-RELATIVE（在 --state_root 下）。你为某条发现所检查的任何代码片段均为 SNAPSHOT-RELATIVE（在 CODE_ROOT 下）。绝不要在 CODE_ROOT 下写入。

审查一组安全发现，并合并指向完全相同安全缺陷或相邻代码路径的重复发现。

按如下方式执行你的任务：

1. **加载原始发现与归档发现队列：**

   - 列出该目录的内容，并读取 `workspace/findings/` 中的文件。如果该目录为空或不存在，通知用户并退出。
   - *重要：* 在列出或处理发现时，忽略隐藏文件和目录（例如 `.trash/` 子目录）。
   - 定位并加载先前循环轮次中所有已归档的发现 JSON 文件（如果存在），位于 `workspace/archive/findings_pass_*/*.json` 和 `workspace/archive/loop*_findings/*.json` 下。这些文件代表在先前轮次中已完全评估、已分诊并可能已修补的漏洞。
   - *重要：* 请 NOT 读取或针对 `workspace/historical_learnings.jsonl`（VCS 历史记录）进行去重，因为我们希望捕获旧缺陷被重新引入时的回归。

2. **过滤循环重复项（快照门控）。** 首先，为任何缺少 `discovery_commit` 的 CURRENT 发现打上该标记，取值来自 `.mantis_state.json` 中的 `active_snapshot.snapshot_id`（未固定时跳过）。然后，对于每条与某条归档发现匹配（依据 `code_paths`+`title` 相似度）的当前发现，执行：

   **基于签名的候选匹配（阶段 3）—— TIGHTENS，绝不替代：** `signature` 只能将一对发现 PROMOTE 为“成对快照检查的候选”；它 NEVER 能仅凭自身导致硬性 DUPLICATE/移入回收站。一对发现 ONLY 在满足以下条件时，才是下方成对快照匹配检查的候选：
   BOTH:

   - 它在当前的 `code_paths` + `title` 相似度下匹配，比较 `code_paths` 条目时包含行号（WITH 其末尾的 `:line`）；AND
   - （当两条发现都有 `signature` 时）它们的 `signature` 字段相等。`signature` 匹配 WITHOUT `code_paths` + `title` 一致 NOT 重复——至多是一个软标记 `possible_duplicate_of`（保持该发现为 ACTIVE），绝不是移入回收站。理由：`signature` 会剥离行号以及除第一条之外的所有 `code_paths` 条目，因此同一文件中的两个 DISTINCT 缺陷（例如 `parser.c:100` 与 `parser.c:900`）若具有相同的 title+CWE，就会共享同一个 `signature`；仅凭签名就移入回收站会静默删除一条真实发现。如果 EITHER 缺少 `signature`，则沿用当前的 `code_paths` + `title` 相似度匹配，不做改动。

   PAIRWISE SNAPSHOT MATCH CHECK（决定 MATCHED 还是 NOT_MATCHED）——针对该对发现，将 CURRENT 发现的 `discovery_commit` 与 ARCHIVED 发现的 `discovery_commit` 进行比较（NOT 与 `SNAPSHOT_ID` 比较）：

   1. 如果 `snapshot_pinned` 为 false AND 状态中 NO `active_snapshot`（MODE-OFF）-> NOT_MATCHED。停止。（在 HALT 中——`active_snapshot` 存在但 `snapshot_pinned=false`——NOT 在此处短路；继续执行下方的成对比较，该比较将得到 NOT_MATCHED，因为当前发现的 `discovery_commit` 是一个 `live:` id，不会等于归档发现的那个。）
   2. 读取 CURRENT 发现的 `discovery_commit` 和 ARCHIVED 发现的 `discovery_commit`：
      - 如果 EITHER 缺失、为空，或为字面量 `"MIXED"` -> NOT_MATCHED。
      - 如果它们 NOT 彼此逐字节相等 -> NOT_MATCHED。
      - 如果它们 ARE 逐字节相等（两者均存在，且非 MIXED）
        -> MATCHED。不存在通往 MATCHED 的其他路径；绝不做模糊比较。全局的“将该字段置为默认值并继续”向后兼容规则 NOT 适用于 `discovery_commit`：缺失 = NOT_MATCHED。（这里 NO 有单独的
        “dirty”门控：脏工作树的 SNAPSHOT_ID 已经内嵌了工作树
        内容哈希，因此同一轮内的发现 MATCH，而跨轮的裸提交
        发现则不会。）注意：这是 PAIRWISE 检查（当前 vs 已归档），
        NOT 是针对全局 `SNAPSHOT_ID` 的检查——去重会在上文 Step 2 中把当前
        发现的 `discovery_commit` 标记为 `SNAPSHOT_ID`，因此
        针对 `SNAPSHOT_ID` 的检查将始终为 MATCHED，并会把
        重新引入的/回归的缺陷当作 DUPLICATE 丢弃。

   然后，使用幂等性规则（Input/Output Contract → Idempotency
   Guarantee）以避免重复写入，机械地判定：

   - **MATCHED**（两者均存在且相等）：与之前完全一样，将当前发现作为
     循环重复项软删除——设置 `"status": "DUPLICATE"` 和
     `"duplicate_of": "<archived_uuid>"`，若存在则清除 `possible_duplicate_of`，
     确保 `mkdir -p workspace/findings/.trash/`，将其移动到该处，并在 `workspace/.tx_log.jsonl` 中
     记录一条 `loop_filter` 事务。如果
     当前发现缺少 `lineage_id` 而已归档发现具有该字段，
     则在移动当前发现之前，将已归档发现的 `lineage_id` 继承到当前发现上
     （以便在合并过程中保留谱系链）。
   - **NOT_MATCHED**（不同，或任一缺失）：NOT 设置 `DUPLICATE`，且
     NOT 移动到回收站。保持当前发现为 ACTIVE，并设置
     `"possible_duplicate_of": "<archived_uuid>"`（一个软性的、非终态的提示）。
     如果当前发现缺少 `lineage_id` 而已归档发现具有该字段，
     则将已归档发现的 `lineage_id` 继承到当前发现上（以便
     即使这些发现位于不同快照上，也能为报告折叠保留谱系链）。

   > [!IMPORTANT] **STATUS 与 DUPLICATE INVARIANTS：**
   >
   > - 一条发现 MUST NOT 同时携带 `duplicate_of` 和 `possible_duplicate_of`
   >   并指向同一目标 UUID。
   > - `status = "DUPLICATE"` MUST NOT 与 `possible_duplicate_of`
   >   共存并指向同一目标 UUID。
   > - 在 **NOT_MATCHED** 下（处于 MODE-OFF 回退例外之外），
   >   该发现的 `status` MUST 保持活动（例如 `VALID`、
   >   `PROVISIONALLY_VALID`、`NEEDS_RESEARCH`），`duplicate_of` MUST NOT 被设置，
   >   且该发现 MUST NOT 被移动到 `.trash/`。设置
   >   `possible_duplicate_of` 仅是非终态提示。

   - **POSSIBLE REGRESSION：** 如果已归档的匹配项具有 RESOLVED 状态
     （`patch_status` 属于 {`VERIFIED_SECURE`,`MITIGATION_PROPOSED`} OR
     `status`==`FALSE_POSITIVE` OR `production_viability`==`NON_VIABLE`）AND
     该对 NOT MATCHED，保持当前发现为 ACTIVE，添加一条历史备注
     “POSSIBLE REGRESSION vs \<archived_uuid>”，并且 NOT 对其执行过滤。（在
     新代码上重新发现的已回退修复绝不能被丢弃。）
   - **EXACT-UUID 重试例外（不变）：** 如果当前发现具有与已归档发现
     相同的 EXACT SAME UUID，则它是为
     重试而被有意复制回来的——NOT 对其执行过滤（保持原样）。
   - **永久未固定（unpinned）例外（仅 MODE-OFF——无 `active_snapshot`）：**
     如果状态中 NO `active_snapshot`（MODE-OFF = 今日的默认值；即
     没有快照边界的目标，例如一个实时端点），则
     `snapshot_pinned` 在 PASS 层级为假（`active_snapshot.snapshot_pinned`——它 NOT 一个
     逐发现字段），且 Block B 无信息量——回退到今日的
     去重方式：若存在 `signature` 则按其去重，否则 `stable_key` = normalized_title +
     第一个 `code_paths` 条目**包括其末尾的 `:line`**
     （含行号，与 Step 2 相同）。理由：剥离 `:line` 会把同一文件中
     两个 DISTINCT 缺陷（例如 `parser.c:100` 与
     `parser.c:900`）在标题+CWE 相同的情况下合并为一个——从而静默删除一个真实的
     发现。注意：`signature` 本身按设计会剥离 `:line`（它是用于跨轮谱系的粗粒度
     标识，而非去重键）；因此该回退仅在 `stable_key` 的含行号匹配
     ALSO 一致时才优先采用 `signature`，绝不单独依据 `signature`。
     这为永远无法 MATCH 的目标保留了去重。当 `active_snapshot` IS 存在但未固定（HALT
     模式）时，此例外 NOT 触发：保持上述受快照门控的行为
     （NOT_MATCHED → 保持 ACTIVE + `possible_duplicate_of`，绝不 `DUPLICATE`）。
     **POSSIBLE REGRESSION 优先于此回退：** 已解决（resolved）的已归档发现与
     NOT_MATCHED 当前发现配对时，ALWAYS 保持活动（绝不丢弃），无论何种模式。

3. **3 层去重阶梯（确定性匹配与语义匹配）：** 在
   解析发现谱系并评估去重候选时，
   去重器使用一个分层的 3 层阶梯，旨在实现亚毫秒级
   快速路径执行，并以语义 RAG 作为回退：

   - **Tier 1（快速路径精确启发式锚点——< 1ms，0 tokens）：**

     1. 精确内容身份签名匹配：
        `hashlib.sha256(canonical_fp | canonical_cwe | target_symbol)`
        （对行偏移、反引号和标题改写不变）。
     2. 精确 `canonical_filepath + normalized CWE + target_symbol` 匹配。
     3. 当目标符号为空时，在精确规范
        文件路径上采用严格的行邻近窗口（$\\le 3$ 行）。

     - *结果*：若匹配，则立即继承祖先 `lineage_id`，无需
       LLM 或嵌入开销。

   - **Tier 2（RCA 规范化）：** 如果 Tier 1 启发式匹配
     未能产生精确锚点，则合成一份标准化的 5 行根因
     分析（RCA）摘要：

     - `Component`：规范化后的规范文件路径与符号。
     - `Vulnerability Class`：规范的 CWE 分类标识符与名称。
     - `Root Cause Mechanism`：底层的编程或逻辑缺陷。
     - `Failure Condition`：特定的输入、状态或边界条件。
     - `Taint Dataflow`：从源到汇的数据流轨迹。

   - **Tier 3（向量嵌入与余弦相似度扫描）：**

     - 使用已配置的多提供方嵌入引擎（默认：`vertex_ai/gemini-embedding-001`），将标准化的 RCA 摘要投影为稠密向量嵌入。
     - 使用有界余弦相似度，在数据库中对历史 `lineage_vectors` 执行最近邻扫描。
     - **CWE 家族与类别结构防护：** 在将查询发现向量与候选谱系记录进行比较时，如果两个发现都具有明确的 CWE 分类（例如 CWE-89 与 CWE-78），则对其进行规范化。如果它们属于不同的、不兼容的 CWE ID，则完全跳过候选向量比较，从而在结构上防止将不同的漏洞类别误合并，无论余弦分数如何。
     - **正阈值（$\\ge 0.90$）：** 语义等价但措辞、扫描器标签或行号偏移不同的发现，会合并到同一个祖先 `lineage_id`（默认：0.90，可通过 `EMBEDDING_SIMILARITY_THRESHOLD` 配置）。
     - **负向阈值（$< 0.70$）：** 不同漏洞类别（例如 SQL 注入与命令注入、存储型与反射型 XSS）应保持低相似度，并采取失败关闭策略，生成新的唯一 `lineage_id`。

4. **过滤当前批次中的重复发现：** 将当前发现相互比对以找出重复项。两个发现 ONLY 在共享同一 `code_paths` 条目**且行号包含在内**（WITH 末尾的 `:line`）AND 具有相同或高度相似的标题时，才属于重复项。如果多个发现指向同一位置上的完全相同的缺陷，则必须将其合并。同一文件中不同行上的发现属于 DISTINCT —— 绝不要合并它们。

5. **Map/Reduce 分块策略（用于规模化）：** 如果发现文件很多（例如 > 20 项），则在检查重叠之前，使用 Map/Reduce 方法按目标文件或组件对其进行分组，以避免上下文窗口限制。

6. **令牌优化的整合与合并：** 为尽量减少 LLM 输出令牌并防止数据丢失，**不要在响应中手动重写或输出合并后的 JSON 文件。** 而应遵循以下模式：

   1. **识别重复项：** 在内部映射哪些发现是某个主发现的重复项。
   2. **可复用的确定性脚本（带版本）：** 编写一个可复用的辅助脚本（例如 `workspace/helpers/merge_findings.py`），其 FIRST LINE 必须恰好为 `# MANTIS_HELPER_VERSION = 2`。在复用现有辅助脚本之前，先 grep 其前几行以查找 `MANTIS_HELPER_VERSION = 2`；如果该标记缺失或为不同的整数（即由较旧流水线版本遗留的辅助脚本），则 REGENERATE 该辅助脚本。仅当标记匹配时才复用它。该脚本必须遵循以下确定性规则：
      - **标题：** 选择最全面、最具描述性的标题。
      - **ID：** 保留所保留主发现的唯一 `"id"`。
      - **严重性：** 选择被合并项中所指定的最高严重性级别。
      - **所需权限：** 继承最严重的权限要求（优先级：`NONE` > `LOW` > `HIGH`）。
      - **攻击者位置：** 继承最关键的定位要求（优先级：`EXTERNAL` > `INTERNAL_NETWORK` > `IN_CLUSTER` > `LOCAL` > `HOST_SYSTEM` > `SUPPLY_CHAIN` > `PHYSICAL_TEMPORARY` > `PHYSICAL_LONG_TERM`）。
      - **用户交互：** 继承最严重的用户交互要求（优先级：`NONE` > `REQUIRED`）。
      - **代码路径：** 收集所有文件路径和行号并去重，合并为单个唯一数组。
      - **描述、缓解措施与影响：** 清晰地拼接。
      - **历史记录：** 拼接并保留被合并发现中的所有 `"history"` 条目。向 `"history"` 数组追加一条符合 schema 的新条目以记录本次合并操作（包含 `"stage": "dedupe"`、`"action": "merge"`、`"details": "Merged duplicate findings: [comma-separated-ids]"`、`"pass_number": <current_pass_number>` 和 `"timestamp": "<current_iso8601_timestamp>"`）。
      - **未知键与来源信息（MANDATORY）：** 脚本 MUST 将主发现中它未显式处理的 EVERY 键（包括 `discovery_commit`、`repro_snapshot_id`、`patch_base_snapshot`、`possible_duplicate_of`、`signature`、`lineage_id`、`cwe`）复制到合并后的对象上 —— 绝不丢弃未知字段。它 MUST REFUSE 合并两个 `discovery_commit` 值不同的发现（它们描述不同的代码版本）；应使其保持分离并记录该拒绝。当合并的发现都共享同一个 `discovery_commit` 时，应原样保留它。
   3. **执行脚本：** 运行你的脚本以更新磁盘上的主发现文件（`workspace/findings/<primary_id>.json`）。

7. **事务性暂存清理：** 不要永久删除冗余文件。确保回收站目录存在（例如 `mkdir -p workspace/findings/.trash/`）。在移动之前，脚本必须更新重复发现文件，设置 `"status": "DUPLICATE"` 和 `"duplicate_of": "<primary_uuid>"`。将被合并的重复 `.json` 文件移动到回收站暂存目录（`workspace/findings/.trash/`）。对于每个被移动的文件，向 `workspace/.tx_log.jsonl` 追加一条事务记录。

   ### 事务日志 Schema 格式（`workspace/.tx_log.jsonl`）

   每一行必须是一个自包含的 JSON 对象，用于记录该事务：

   ```json
   {"timestamp": "2026-07-14T15:13:00Z", "action": "loop_filter | dedupe_merge", "primary_uuid": "[UUID] (or null for loop_filter)", "moved_uuid": "[UUID]"}
   ```

   这样可为下游阶段清理目录，同时保留回滚能力。

完成后，通知用户。
