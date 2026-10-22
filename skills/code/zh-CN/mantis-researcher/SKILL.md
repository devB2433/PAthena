---
name: mantis-researcher
description: >-
  依据 workspace/plan.json. 中的策略审计生产源代码文件。
  当已有审查计划、且需要对目标文件执行静态分析与深度审查时使用。
  不要用于制定计划、去重或编写补丁。
---

# Mantis Researcher (/mantis-researcher)

## 系统目标

韧性代码审计员。对源文件执行快速分诊与深度审查，以识别边界检查、前置条件、缺失的净化处理以及接口违规。

## 命令定义

- **命令：** `/mantis-researcher`
- **描述：** 依据 `workspace/plan.json` 中的策略审计生产源代码文件。
- **参数（可选；由编排器提供，由 Block A 消费）：**
  - `--snapshot_root` / `SNAPSHOT_ROOT`：本轮次所固定的只读代码快照的绝对路径。这就是所有快照相对路径字段所解析依据的 CODE_ROOT（Block A 步骤 1b）。
  - `--snapshot_id` / `SNAPSHOT_ID`：轮次快照标识符。用于哨兵检查（Block A 步骤 2），并原样写入每条发现的 `discovery_commit`。
  - `--state_root`：`workspace/` 状态目录的绝对路径（`plan.json`、`.mantis_state.json`、`findings/`、`kb/`）。状态路径是 STATE-RELATIVE，并且 NEVER 以 CODE_ROOT 为前缀（Block A 步骤 3）。
  - 若提供了 `--target_root`（权威覆盖，Block A 步骤 1a），同样予以遵循。
  - **所有标志均缺失 -> DEGRADED/legacy 模式：** CODE_ROOT 回退到当前目录，`snapshot_pinned` 被视为 false，行为与当前完全一致（不写入 `discovery_commit`）。

## 输入/输出契约

- **读取**：
  - `workspace/plan.json`（若缺失/为空，则回退为代码库全面扫描）。
  - `workspace/.mantis_state.json`（用于跟踪当前循环轮次）。
  - `"kb_references"` 中引用的 Markdown 文件（例如 `workspace/kb/entities/*.md`）。
  - 目标源代码文件。
  - `workspace/kb/structural_index/manifest.json`（用于检查结构化索引的可用性/状态）。
  - `workspace/helpers/query_structural_index.py`（用于调用有界结构化索引查询）。
- **写入**：
  - 将原始发现文件写入 `workspace/findings/<uuid>.json`（若 `workspace/findings/` 缺失则创建）。
- **前置条件**：
  - 目标文件必须可访问。
- **幂等性保证**：
  - 将新发现作为独立文件写入，使用唯一 UUID。依赖 `mantis-dedupe` 在后续步骤中对重复发现进行聚类与合并。

## 指令

### 步骤 0：定位器解析（快照感知的路径处理）

在下方编号的研究步骤 BEFORE 运行此步骤。它确定唯一的 CODE_ROOT，本阶段中所有 `target_files` / `code_paths` 引用均依据它解析，从而使所有子代理审计同一个固定快照。

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

面向研究员的技能专属说明：

- 研究员是 CODE-READING 阶段，因此 Block A 步骤 0 的“仅发现”跳过 NOT 适用于此处——你 MUST 解析 CODE_ROOT 并遵循哨兵。
- `workspace/plan.json` 的 `target_files` 与发现的 `code_paths` 是
  SNAPSHOT-RELATIVE：在 CODE_ROOT 下解析它们（Block A 步骤 3）。
- `kb_references`、`workspace/plan.json`、`workspace/.mantis_state.json` 以及 `workspace/findings/` 下的所有内容均为 STATE-RELATIVE：在 --state_root 下读写它们，NEVER 在 CODE_ROOT 下。
- 固定时，绝不在 CODE_ROOT 下写入、编译或生成任何内容（Block A 步骤 4）。

对目标代码库执行彻底的内存安全、逻辑正确性与健壮性审查。

按如下方式执行研究阶段：

1. **加载审查计划与上下文：** 从 `workspace/.mantis_state.json` 读取当前轮次编号，并解析当前 ISO 8601 时间戳。读取 `workspace/plan.json` 文件以获取目标调查项。若 `workspace/plan.json` 缺失或为空，则对目录执行通用列举，并审查任何主要源文件。若调查项包含 `"kb_references"` 数组，则在开始审计 `"target_files"` 之前，显式读取这些 Markdown 文件（例如 `workspace/kb/entities/auth.md`）以获取累积的历史上下文。同时从 `workspace/.mantis_state.json` 读取 `active_snapshot`（`root`、`snapshot_id`、`snapshot_pinned`）。将 `active_snapshot.snapshot_id` 保存在内存中：它就是你要写入每条发现 `discovery_commit` 的值（参见 Findings Schema Format）。若 `active_snapshot` 缺失或 `snapshot_pinned` 为 false，则你处于 DEGRADED/legacy 模式——NOT 停止（Block A 步骤 1d）；你只需省略 `discovery_commit`。

2. **子代理委派（基于波次的蜂群并行化）：** 若 CLI 或代理平台支持派生子代理（例如使用专用子代理工具或多代理编排器指令）：

   - 若支持子代理，不要顺序执行调查。将 `workspace/plan.json` 中的调查拆分为并行波次，以最大化吞吐量与上下文效率。

   - **波次 1：轻量快速分诊（并发峰值）：** 派生并发的轻量子代理（例如最多 10-20 个并行），扫描 `workspace/plan.json` 中列出的所有文件。每个子代理应只输出一份快速的
     classification: `{"potentially_flawed": true/false, "reason": "..."}`.

   - **波次 2：深度安全缺陷热点审计与并行轨迹搜索：** 收集波次 1 中标记的所有文件。派生一波并发的深度审计子代理（例如最多 4-8 个并行），专门聚焦于这些已识别的热点。对于特别复杂的文件，可针对*同一*文件派生多个子代理，使用不同的提示约束或一组多样化的低成本 LLM 来探索并行的攻击向量。依赖后续的去重阶段合并任何重叠的发现。

   - **Token 优化（分布式写入）：** 指示波次 2 的子代理生成唯一 UUID，并将其发现直接写入磁盘上各自的 `workspace/findings/<id>.json` 文件。不要要求它们在返回给你的消息中回传完整的 JSON 载荷，因为聚合这些内容会撑爆你的上下文窗口。要求它们只返回其创建的 UUID 列表。

   - **快照隔离（Wave 固定）— MANDATORY：** 将 SAME 的 `--snapshot_root`（CODE_ROOT 已在 Step 0 中解析）以及相同的 `--snapshot_id` 值传递给 EVERY Wave-1 和 Wave-2 子代理，并指示它们各自遵守 Block A（在该 CODE_ROOT 下解析 `target_files`/`code_paths`，遵守 sentinel）。任何 Wave-2 子代理写入 finding 时 MUST 用该 snapshot_id 打上 `discovery_commit` 标记，完全按照 Findings Schema Format 中的规定。子代理 MUST NOT 运行 `git pull`/`fetch`/ `checkout`/`reset`、`hg pull`/`update`、`repo sync`，或任何会更改工作树或切换修订版本的命令——快照在整个 pass 期间不可变。无法看到快照的子代理必须报告该情况，而不得重新同步。

   - 如果当前环境不支持子代理或并发，则回退为顺序执行各项 sweep 和 deep-dive。

   - **结构索引（仅 HINT 增强）：** 当结构索引可用时（`workspace/kb/structural_index/manifest.json` 存在），使用它来 SUPPLEMENT 上述基于 wave 的 swarm。结构索引决定 ORDER，绝不决定 MEMBERSHIP。它 MUST NEVER 取代 Step-3 的穷尽式 call-site sweep。

     - **解析优先协议（任何结构查询之前 MANDATORY）：**

       1. 首先解析符号：
          `python3 workspace/helpers/query_structural_index.py resolve_symbol --name "<function_name>" [--language "<lang>"] [--file "<path>"] --state_root <state_root>`
       2. 如果响应中包含 `ambiguous: true`，则调查 ALL 个匹配的符号——绝不静默地挑选其中一个。如有可能，用 `--file`/`--language` 缩小范围，或为每个匹配的符号安排调查。
       3. 使用已解析的 `symbol_id` 进行有界查询：
          `python3 workspace/helpers/query_structural_index.py find_callers --symbol_id "<id>" --limit 100 --offset 0 --state_root <state_root>`

     - **覆盖感知解读：** 检查每个结构索引响应中的 `coverage.partition_status`：

       - `complete` + `precision == semantic` + 空结果 = “无已索引的调用方”（对已索引代码具有权威性——仍须按 HINT-only 规则运行 grep）。
       - `complete` + `precision != semantic` + 空结果 = “无已索引的调用方”——NOT 具有权威性。MUST 运行穷尽式 grep。
       - `partial` / `empty` / `failed` + 空结果 = “未完全索引”——MUST 运行穷尽式 grep。
       - 使用每个结果上的 `precision` 和 `backend` 字段来权衡可信度（`semantic` > `typecheck` > `ast` > `symbol-only` > `heuristic` > `deferred` > `coverage-only`）。

     - **Wave 1（快速分诊）：** 使用 `find_callers()` 来 SUPPLEMENT grep，作为排序 HINT——ORDER，绝不 MEMBERSHIP。结构索引结果用于确定哪些文件应被标记为 `potentially_flawed` 的优先级；它们 MUST NEVER 取代 Step-3 的穷尽式 call-site sweep。审计 grep 结果与结构索引结果的并集。

     - **Wave 2（深度审计）：** 使用 `get_function_boundary(file, line)` 从所在的外层函数开始，然后按需扩展到调用方/被调用方/文件，以获取深度分析上下文。

     - **优雅降级：** 如果结构索引缺失（没有 `manifest.json`）、为空，或查询辅助工具缺失，则回退到基于 grep 的发现方式（即当前行为）。结构索引仅是一种覆盖 HINT。

3. **穷尽式接口与调用点审查：** 如果目标源文件定义了公开或 API 函数（例如数值解析器、解码器、编码器或转换器），且这些函数记录了明确的大小约束或安全要求（例如期望调用方分配特定大小的缓冲区）：

   - 对函数名运行全仓库 grep，以构建候选调用点的穷尽集合——这是强制性的底线。然后使用结构索引查询辅助工具（先 `resolve_symbol` 再 `find_callers`）来 RANK 并确定优先审计哪些调用点（该索引可区分真实调用与注释/字符串/变量名）。审计两个结果集的并集——结构索引可能遗漏基于宏的调用、函数指针和动态分派，因此 grep 仍是底线。
   - 搜索代码库，在整个仓库范围内查找并审查这些函数的所有调用点，以确保安全契约在全局范围内得到遵守。
   - 阅读调用方文件，并验证每个调用点是否严格遵守输入约束、正确管理边界并检查大小。
   - 将任何不一致标记为契约对齐缺陷或缺失检查。

4. **无约束 / 探索性调查：** 如果 `workspace/plan.json` 中的调查计划包含明确要求进行无约束 sweep、对抗性审计或随机探索的指令或问题：

   - 忽略 `workspace/kb/THREAT_MODEL.md` 中已有的安全假设和已记录的信任边界。
   - 将所有输入和边界视为不可信且可能格式错误。
   - 以完全的自由度和自主性从零开始分析实现。
   - 如果这是一项指令极少的随机探索/挖掘任务，则专注于梳理目标文件的行为、识别关键入口点，并在不受特定威胁模型约束的情况下寻找意外的副作用或边界情况。

5. **编译并写入 Findings：** 不要使用单个单体文件，而应在 `workspace/findings/` 目录不存在时创建它。对于每个潜在 finding，生成唯一的 UUID，并将一个有效的 JSON 对象写入名为 `workspace/findings/<id>.json` 的独立文件中。这样可使 findings 保持隔离，并防止后续分析期间出现 token 限制问题。文件中不得在 JSON 之前或之后包含任何文本。

   **签名与谱系计算（Step 5a）：**

   对于每个 finding，在写入 JSON 文件 BEFORE 之前计算 `signature`、`lineage_id`，以及可选的 `cwe`：

   1. **`cwe`（可选）：** 如果该漏洞映射到已知的 CWE，则将 `cwe` 字段设置为该标识符（例如 `"CWE-787"`、`"CWE-416"`）。如果没有适用的 CWE，则省略该字段。这是签名的一项输入。

   2. **计算 `signature`（确定性的内容身份哈希）：**

      - `normalized_title` = finding 的 `title`，转为小写，并去除 ALL 个非字母数字字符（仅保留 ASCII `[a-zA-Z0-9]`；包括 Unicode 字母、标点符号和空格在内的所有其他字符均被移除）。如果去除后 `normalized_title` 为 EMPTY（例如标题完全由非 ASCII/Unicode 字符组成），则设置 `normalized_title` = `sha256(<original raw title as UTF-8 bytes>)` 的前 16 个十六进制字符，以免两个不同的非 ASCII 标题在空字符串上发生碰撞。
      - `cwe_part` = 该 finding 的 `cwe` 字段（若存在且非 null），否则为空字符串。
      - `primary_target` = `code_paths` 的第一个条目，去除末尾的 `:line`（例如 `src/auth.c:145` → `src/auth.c`）。如果 `code_paths` 为空，或第一个条目是非源码 LOCATOR（URL 中包含 `://`，或按 Block A 步骤 3 属于非文件符号/偏移量），则使用空字符串。以确定性方式对 `code_paths` 排序（主 sink 在前，并在各轮之间保持稳定），使 `primary_target`——进而使 `signature`——不会在各轮之间发生漂移。（如果顺序不稳定，唯一代价是漏掉谱系继承 → 该 finding 会被过度报告为新发现，绝不会被隐藏——但稳定的顺序可保留跨轮折叠。）
      - 如果 `primary_target` 非空：`signature` = `sha256(normalized_title + "|" + cwe_part + "|" + primary_target)` 的前 16 个十六进制字符。
      - 如果 `primary_target` 为空：`signature` = `sha256(normalized_title + "|" + cwe_part + "|" + sorted(code_paths).join(","))` 的前 16 个十六进制字符。
      - 在创建 finding 时ONCE计算一次签名，NEVER重新计算、编辑或凭空构造它（规则与 `discovery_commit` 相同）。

   3. **计算 `lineage_id`（跨轮谱系链）：**

      - 扫描 `workspace/archive/findings_pass_*/` 和 `workspace/archive/loop*_findings/` 中的任何已归档 finding JSON，其 `signature` 字段等于本 finding 计算出的 `signature`。
      - 如果找到匹配：`lineage_id` = 该已归档祖先的 `lineage_id`（继承谱系链，以便下游消费者可跨轮折叠）。如果MULTIPLE已归档 finding 共享同一签名，则从MOSTRECENT（轮次编号最高）的祖先继承。所有具有相同签名的祖先SHOULD共享同一 lineage_id；如果它们不共享，则从最近的一个继承，并记录一条警告。
      - 如果按精确签名未匹配到：对 TRUE 重命名 ONLY 尝试**基名重命名回退**。NOT 重新计算签名（签名在创建时 ONCE 计算一次，之后永不重算——不变量 #4）。而是：
        1. 计算CURRENT finding 的基名：取 `primary_target`（`code_paths` 的第一个条目，去除末尾的 `:line`，已在步骤 2 中为签名计算过），并取其基名（例如 `src/auth.c` → `auth.c`）。如果 `primary_target` 为空（非源码 LOCATOR 或 code_paths 为空），则SKIP此回退——转到下面的全新 UUIDv4。
        2. 对于步骤 3 的归档扫描中定位到的每个已归档 finding：从其存储的 `code_paths[0]` 重建ARCHIVED finding 的基名（去除末尾的 `:line`，取基名——例如 `lib/old_auth.c:88` → `old_auth.c`）。NOT重新计算已归档 finding 的签名，且NOT比较签名；而是将两个基名作为STRINGS进行比较。
        3. 通过此回退继承祖先的 `lineage_id` ONLY IF (i) 基名匹配 AND (ii) 祖先的完整 `primary_target`（其 `code_paths[0]` 去掉 `:line` 后）NO LONGER EXISTS 于当前快照（检查旧完整路径在 CODE_ROOT 下不存在——以此区分真正的重命名与仅仅共享基名的第二个独立文件）。如果 MULTIPLE 个已归档祖先满足 (i) 和 (ii)，则从 MOST RECENT 的祖先继承（pass 编号最高者）。当你通过基名继承时，ALSO 添加一条发现历史备注 `lineage-via-basename-rename`，以便下游消费者将该链接视为源自基名（报告仅当两个发现的完整 `signature` 按 SAME-BUG 谓词 ALSO 匹配时才合并这两个发现，因此源自基名的谱系链接绝不会合并不同的缺陷）。
        4. 如果旧完整路径在当前快照上仍然存在，则NOT继承——视为无匹配（转到下面的全新 UUIDv4）。
      - 如果按精确签名或基名重命名均未找到匹配：`lineage_id` = 一个全新的 UUIDv4。
      - 这些是 STATE-RELATIVE 路径（Block A 步骤 3）——在 `--state_root/workspace/archive/` 下读取，NEVER在 CODE_ROOT 下读取。

   4. **将全部三个字段**（`cwe`、`signature`、`lineage_id`）与 `discovery_commit` 一起写入 finding JSON。

   **模式无关性：** 与 `discovery_commit` 不同（后者在 DEGRADED/legacy 模式下被省略），`signature` 和 `lineage_id` 是ALWAYS计算的——它们是内容身份字段，而非快照身份字段。无论快照是固定、未固定还是不存在（MODE-OFF），它们都能正常工作。这是 Phase-3 的有意改进：在 MODE-OFF 中，finding JSON 现在携带 2–3 个额外的可选键（`cwe`、`signature`、`lineage_id`），这些键在 Phase 1 中并不存在。这NOT改变快照模型（无 `active_snapshot`、无同步、无固定——3-state 规则不受影响）。去重 MODE-OFF 回退现在在存在时优先使用 `signature` 而非 `stable_key`，这具有严格MORE的区分度（签名在标题+路径的组合中增加了 `cwe`），因此它只能SPLIT那些`stable_key`本会合并的条目（全新预算，更保守 = 安全）——它绝不会产生错误合并或抑制合法 finding。缺少 `signature` → 与今天的 `stable_key` 行为完全一致。

   **目标文件缺失或不可读：** 如果 `target_files` 中的某个路径在 CODE_ROOT 下不存在或无法读取（例如自计划编写以来该文件已被删除或重命名），则 NOT 编造发现项、行号或文件内容。跳过该目标，并在与其相关的任何发现项的 `description` 中如实记录该跳过（或完全省略该发现项）。绝不虚构你未曾阅读过的代码。

   **非源码目标：** 对于非源码 LOCATOR（二进制文件、固件，或 URL 端点——参见 Block A 步骤 3），`code_paths` 条目 MUST 是一个 STABLE LOCATOR（符号名、偏移量或裸路径），WITHOUT `:line` 后缀。不得为无法以文本方式打开的目标附加编造的行号。

### 发现项 Schema 格式（按文件）

```json
{
  "id": "A unique identifier generated for this finding (e.g., a UUID or random hash). This must be included and match the filename.",
  "title": "Authorization bypass or Memory bounds violation in [function_name]",
  "description": "Thorough root cause analysis detailing why the function is flawed under untrusted input.",
  "impact": "Exploit outcome (e.g., Privilege escalation, Memory corruption, Data exfiltration).",
  "severity": "CRITICAL / HIGH / MEDIUM / LOW",
  "privileges_required": "NONE / LOW / HIGH",
  "attacker_position": "EXTERNAL / INTERNAL_NETWORK / IN_CLUSTER / LOCAL / HOST_SYSTEM / SUPPLY_CHAIN / PHYSICAL_TEMPORARY / PHYSICAL_LONG_TERM",
  "user_interaction": "NONE / REQUIRED",
  "status": "PROVISIONALLY_VALID",
  "code_paths": ["SNAPSHOT-RELATIVE path under CODE_ROOT, e.g. 'relative/file/path.c:145'. For a NON-SOURCE target (binary/firmware/URL, per Block A step 3) use a STABLE LOCATOR (symbol name, offset, or bare path) WITHOUT a fabricated ':line'. Never invent a line number."],
  "discovery_commit": "The active_snapshot.snapshot_id read from workspace/.mantis_state.json at the start of this pass (Step 1). REQUIRED and NON-EMPTY whenever the snapshot is pinned: copy it VERBATIM, set it exactly ONCE at finding creation, and never recompute, edit, or invent it. OMIT this key entirely (do not write \"\" or null) if active_snapshot is absent or snapshot_pinned is false (DEGRADED/legacy mode).",
  "cwe": "CWE-787 (optional; omit if no CWE applies)",
  "signature": "First 16 hex chars of sha256(normalized_title + '|' + cwe_part + '|' + primary_target). Computed once at creation, never recomputed.",
  "lineage_id": "UUIDv4, or inherited from an archived finding with the same signature. Computed once at creation.",
  "mitigation": "Recommended corrective modification.",
  "history": [
    {
      "stage": "researcher",
      "action": "created",
      "details": "Initial audit finding recorded.",
      "pass_number": <current_pass_number>,
      "timestamp": "<current_iso8601_timestamp>"
    }
  ]
}
```

**`discovery_commit` 规则（在创建时设置一次）：** 本阶段是 `discovery_commit` 的创建位置。当快照被固定时，你（或你的 Wave-2 子代理）写入的每个发现项 MUST 携带一个非空的 `discovery_commit`，其值等于 `active_snapshot.snapshot_id`。下游阶段将缺失的 `discovery_commit` 视为 NOT_MATCHED（保守分支），因此写入空字符串或错误的值会静默破坏匹配——绝不要这样做。在 DEGRADED/legacy 模式下（没有 `active_snapshot`，或 `snapshot_pinned` 为 false），省略该键，以使行为与当前流水线一致。

**`signature`/`lineage_id` 规则（在创建时设置一次）：** 这些字段是 ALWAYS 计算的（不同于 `discovery_commit`，后者在降级模式下会被省略）。签名是发现项内容标识的确定性哈希；lineage_id 将发现项链接到其归档祖先。下游消费者（去重、链、报告、复现）以签名/谱系为键，并以 UUID 作为回退：缺失 `signature` → 与当前的 UUID 行为完全一致（不会出现静默的错误结果）。

确保所有单独的发现项文件都写入 `workspace/findings/` 目录。完成后，通知用户。
