---
name: mantis-structural-index
description: >-
  从源代码构建内容寻址的语义单元索引，用于提供结构上下文。当存在已固定（pinned）或实时的代码库，且结构化交叉引用数据能够提升研究质量时使用。不要用于发现项分析、打补丁或报告。
---

# 结构化代码索引构建器

这是 Pass Lifecycle Contract 中的一个**可选的一等阶段**。它在快照被固定（Block D）之后立即运行，位于第一个代码阅读分析阶段（summarize/architecture）之前。它只需要 `CODE_ROOT` + `SNAPSHOT_ID`，且不得依赖 architecture/KB。

## 系统目标

结构化代码索引构建器。使用基于能力的按分区后端选择，从源代码构建内容寻址的语义单元索引，并在不可用时优雅降级到 grep。提供 `find_callers(symbol)`、`get_function_boundary(file, line)` 和调用点感知能力，以在发现阶段提升 LLM 的推理质量——用结构化数据补充（绝不替代）基于 grep 的调用点发现。该索引由独立的语义编译单元组成，持久化为 manifest + SQLite 目录，并通过一个有界的查询辅助脚本对外提供。

## 命令定义

- **命令：** `/mantis-structural-index`
- **描述：** 从 `CODE_ROOT` 下的源代码构建内容寻址的语义单元索引。
- **参数（可选；由编排器提供，由 Block A 消费）：** `--snapshot_root`/`--snapshot_id`/`--state_root`。全部缺省 → MODE-OFF/legacy 模式（从当前目录读取源代码，将索引写入 `./workspace/kb/structural_index/`）。

## 输入/输出契约

- **读取**：
  - `workspace/.mantis_state.json`（读取 `active_snapshot`，用于来源校验和快照感知的重建逻辑）。
  - `workspace/kb/structural_index/manifest.json`（检查 `snapshot_id`，用于匹配即复用的幂等性——主要检查）。
  - `workspace/kb/structural_index.jsonl`（当 manifest 缺失时的向后兼容来源校验）。
  - `workspace/kb/structural_index/units/`（用于增量单元复用的内容寻址缓存）。
  - `workspace/kb/structural_index/native/` 及 sidecar `provenance.json` 文件（预构建索引附件与元数据清单）。
  - CODE_ROOT 源文件（通过生成的辅助脚本——该脚本解析 `CODE_ROOT` 下的所有源文件）。
- **写入**：
  - `workspace/kb/structural_index/manifest.json`（STATE-RELATIVE——原子提交点，LAST 写入）。
  - `workspace/kb/structural_index/catalog.sqlite`（STATE-RELATIVE——面向查询优化的服务存储）。
  - `workspace/kb/structural_index/units/`（STATE-RELATIVE——内容寻址的不可变单元输出）。
  - `workspace/kb/structural_index/shards/`（STATE-RELATIVE——面向大型代码库的分区服务数据）。
  - `workspace/kb/structural_index/native/`（STATE-RELATIVE——预构建索引
    attachments：SCIP、Kythe、LSIF）。
  - `workspace/kb/structural_index/tmp/`（STATE-RELATIVE——构建期间的临时对象）。
  - `workspace/kb/structural_index.jsonl`（STATE-RELATIVE——兼容性指针；低于阈值时完整导出）。
  - `workspace/helpers/build_structural_index.py`（STATE-RELATIVE——构建器辅助脚本）。
  - `workspace/helpers/query_structural_index.py`（STATE-RELATIVE——查询辅助脚本）。
- **前置条件**：
  - 源文件必须存在于 `CODE_ROOT` 下。如果 `CODE_ROOT` 未被解析（MODE-OFF 且没有可读的 `active_snapshot`），则针对当前目录进行构建，并将 `snapshot_id` 设为 `"unknown"`。不要NOT跳过——这是独立效率场景。
- **在接入前保持惰性：** 在调用方（harness、`mantis-plan` 或 `mantis-researcher`）调用本技能之前，它返回空索引。它从不失败——如果工具不可用或源代码无法解析，它只是返回空索引。
- **幂等性保证**：
  - 对 `CODE_ROOT` 只读。仅写入 STATE-RELATIVE 路径。以相同的 `CODE_ROOT` 和 `SNAPSHOT_ID` 重新运行时会复用现有索引（manifest 的 `snapshot_id` 匹配），而不是重建——但在 MODE-OFF 下除外，此时它总是重建。当各语义单元的内容寻址缓存键匹配时，这些单元可跨快照复用（增量重建）。

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

这是一个 CODE-READING 阶段——它通过辅助脚本读取 CODE_ROOT 下的目标源文件。Block A 步骤 0 的仅发现项跳过不NOT适用。

### 步骤 1：幂等性 / 新鲜度检查（MANDATORY FIRST）

**MANDATORY FIRST STEP：** 在编写任何构建脚本、探测后端或提取符号之前，检查 MODE-OFF 或现有 manifest：

1. **MODE-OFF 检查（FIRST）：** 在 MODE-OFF 中（`SNAPSHOT_ID` 为 `"unknown"` 或缺失）→ **始终重建**。不要NOT复用先前的 `"unknown"` 索引，因为活动树是可变的，且 `"unknown"` 是常量（不是新鲜度信号）。直接跳到步骤 2（内容未变化的单个单元仍可能命中内容寻址缓存）。
2. 如果 `workspace/kb/structural_index/manifest.json` 存在，读取其 `snapshot_id` 字段。
3. 如果 `snapshot_id` 与当前 `SNAPSHOT_ID` 匹配（且 `SNAPSHOT_ID` 不是 NOT `"unknown"`）→ **立即复用该索引并 STOP**。不要NOT调用步骤 2、探测后端或编写构建脚本。这限制了重试/崩溃恢复过程中的成本。
4. 如果 `manifest.json` 缺失但 `workspace/kb/structural_index.jsonl` 存在（向后兼容），则读取其来源头（`_provenance`、`snapshot_id` 键）。如果 `snapshot_id` 匹配（且不是 NOT `"unknown"`）→ **复用并 STOP**。否则继续重建。
5. 如果 `SNAPSHOT_ID` 不同 → 继续重建（步骤 2–5）。对于增量
   reuse：在重建某个语义单元之前，检查 `units/` 是否有内容寻址缓存命中（见下文“内容寻址缓存键”）。缓存命中会复用该单元的输出，而无需重新提取。

### 步骤 2：按分区选择后端

将代码库划分为语义单元（见下文“按语言的语义单元”）。对每个单元，使用**基于能力、按分区决策**的方式选择此环境中可用的最精确后端——而不是单一的全局阶梯。以下为**示例，并非穷尽枚举**。辅助脚本按分区探测每一层级，并选择可用的最高层级：

1. **与快照匹配的 SCIP / LSIF / Kythe / clangd-static / 远程索引**（精度：`semantic`）——前提是存在与当前 `snapshot_id` 或 `root_fingerprint` 匹配的预构建索引。精度最高：完整的类型感知交叉引用、调用层级以及悬停/签名数据。
2. **由编译器 / 类型检查器支持的提取器**（精度：`typecheck`）——前提是 `compile_commands.json`、构建上下文或类型检查器可用。类型准确的符号解析与调用边。
3. **语言感知的 AST 提取**（精度：`ast`）——前提是 `tree-sitter`、`ast-grep` 或特定语言的解析器可用。完整的 AST 解析：函数边界、调用表达式、签名。
4. **仅符号提取**（精度：`symbol-only`）——前提是 `ctags` 或等价工具位于 `PATH` 上。仅符号表（函数定义、位置——不含调用图）。调用点提取在已知函数边界内使用轻量级正则表达式扫描。
5. **启发式回退**（精度：`heuristic`）——使用 Python 标准库对源文件进行正则表达式扫描。使用与语言无关的启发式方法识别函数定义和调用模式。精度较低，但零依赖。
6. **仅覆盖清单 + 词法回退**（精度：`coverage-only`）——使用 grep；不写入任何结构化索引。清单记录 `status: "empty"`。消费方回退到基于 grep 的发现（与当前行为逐字节一致）。

**原生索引探测与解析规则**：

- **探测指令**：在评估各分区的后端之前，先探测 `workspace/kb/structural_index/native/` 及其子目录 `native/{scip,lsif,kythe}/` 中是否存在预构建的索引文件。
- **快照声明约定**：由于原生格式（SCIP、LSIF、Kythe）不会在其二进制载荷中直接嵌入快照标识，预构建索引 MUST 使用位于 `workspace/kb/structural_index/native/provenance.json` 或 `native/<kind>/provenance.json` 的旁挂 `provenance.json` 清单来声明其目标快照。该清单包含一个数组，其元素为
  attachments：
  `[{"kind": "scip|lsif|kythe", "path": "...", "snapshot_id": "...", "root_fingerprint": "...", "language": "...", "indexer": "...", "precision": "semantic", "files": [...]}]`。
  若原生索引声明的 `snapshot_id` 等于 `SNAPSHOT_ID`（当 `SNAPSHOT_ID != "unknown"` 时），或其 `root_fingerprint` 与工作区计算出的根指纹匹配，则该原生索引视为匹配。若 `files` 数组缺失或为空，则将原生索引视为不直接覆盖任何单个文件（记录到 `manifest.native_indexes` 中，但不更新 `coverage` 行；对所有文件回落到更低层级）。
- **记录并延后摄取规则**：在无依赖的纯 Python 中解析原始二进制原生索引（例如 SCIP protobuf）代价高昂且复杂。Option A 构建脚本 MUST 检测到匹配的预构建原生索引，将其条目记录到 `manifest.native_indexes` 中，并为 provenance 清单中列出的所有文件将 `coverage` 表的 `backend` 设置为（例如）`"scip"` 或 `"scip-clangd"`。若 `catalog.sqlite` NOT 已用来自原生索引的符号填充（原始二进制延后交由 harness/MCP 读取器处理），则设置 `coverage.status = "deferred"` 和 `precision = "deferred"`（并设置 `indexed_files = 0`）。这可防止查询辅助程序在 `semantic` 精度下将未摄取的声称分区为“权威空”，从而确保消费方执行强制性的 grep 回退。当 `catalog.sqlite` IS 已填充（例如通过 Option B 预摄取或 `scip-to-sqlite`），则设置 `precision = "semantic"` 和 `status = "indexed"`。

**LSP NOT 等同于 SCIP / LSIF。** LSP 是一种交互式协议，其工作区状态可能是部分或不稳定的。仅当服务器能够证明快照标识 AND 完整的工作区覆盖时，才使用它。运行中的语言服务器不会自动具备 `semantic` 后端的资格。

**SCIP 合并。** SCIP 明确允许合并来自不同精度级别索引器的互补信息。其格式记录索引器版本和每文档的语言元数据。构建器 MAY 在单个目录中合并来自多个索引器的结果（例如，用于 Go 的 SCIP 索引 + 用于 Python 的 tree-sitter）。

确定性体现在一个运行时生成的带版本辅助脚本中（`build_structural_index.py`，`# MANTIS_HELPER_VERSION = 5`，复用时先 grep 再重新生成），该脚本按分区探测并选择后端。不随附任何二进制文件；可安全用于气隙环境。

**确定性的部分覆盖。** 在非常大的源码树上，完整重建可能主导阶段 0 的实际耗时。用确定性的优先级队列替代按发现顺序截断：

1. **显式目标文件与符号**（来自 `plan.json`，若可用）。
2. **变更单元及已知反向依赖**（来自 Block E 差异 + 现有目录中的反向依赖边）。
3. **包含层级 1+2 单元的包/模块及其直接导入**。
4. **按规范化路径顺序排列的其余单元**。

对该队列应用确定性的 `max_units`（默认：10000）或 `max_source_bytes`（默认：500MB）上限。将延后的单元持久化到清单中，以便另一次调用可以恢复。状态：`complete`、`partial`、`empty`、`failed`。

只有当相关分区完整、快照匹配、已覆盖且语义精确时，消费方才应强烈地将“无调用者”作为结论。否则答案应为“无已索引的调用者”，随后执行现有的词法回退。该索引仅为 HINT，因此部分覆盖是安全的（grep 仍具权威性）。

### 步骤 3：编写并运行辅助脚本

使用两个运行时生成的辅助脚本。两者都遵循“先 grep 再重新生成”的
pattern：复用之前，先 grep 第一行查找版本标记；若缺失或为不同的整数，则 REGENERATE。

#### 构建器：`build_structural_index.py`

1. 将构建器写入 `workspace/helpers/build_structural_index.py`。FIRST LINE MUST 恰好为 `# MANTIS_HELPER_VERSION = 5`。在复用现有辅助脚本之前，先 grep 其前几行查找 `MANTIS_HELPER_VERSION = 5`；若该标记缺失或为不同的整数，则 REGENERATE 该辅助脚本。
2. 构建器将代码库划分为语义单元（参见“按语言的语义单元”），计算内容寻址的缓存键，检查 `units/` 以复用，按分区选择后端（步骤 2），提取符号 + 边，并将结果写入 `catalog.sqlite`。
3. 构建器必须提取：
   - **符号**：`symbol_id`、`name`、`qualified_name`、`namespace`、`language`、`file_path`、`start_line`、`end_line`、`kind`、`signature`、`backend`、`precision`。
   - **调用边**：`caller_id`、`callee_id`、`callee_name`、`file_path`、`line`、`edge_kind`。
   - **函数边界**：`symbol_id`、`file_path`、`start_line`、`end_line`、`signature`、`language`。
   - **覆盖率**：`file_path`、`indexed`、`backend`、`precision`、`unit_cache_key`、`status`。
4。构建器写入清单 LAST（步骤 4）以及兼容性指针。
5。构建器 MUST 使用 ABSOLUTE 路径，并设置其自身的工作目录（Block A 步骤 6）。当 `snapshot_pinned` 为 true 时，它 MUST NOT 在 `CODE_ROOT` 下写入任何内容（Block A 步骤 4）。会产生附属文件的后端（ctags `tags`、cscope `cscope.out`、clangd 缓存）MUST 被重定向到 STATE-RELATIVE 路径：`ctags -f <state>/helpers/tags`、`cscope -f <state>/helpers/cscope.out`、`CLANGD_INDEX_STORAGE=<state>/helpers/`。对运行中服务器的只读 LSP/SCIP 查询无需重定向。

#### 查询辅助脚本：`query_structural_index.py`

1。将查询辅助脚本写入 `workspace/helpers/query_structural_index.py`。FIRST LINE MUST 恰好为 `# MANTIS_HELPER_VERSION = 5`。在复用已有辅助脚本之前，用 grep 检查其前几行是否包含 `MANTIS_HELPER_VERSION = 5`；若缺失或为不同的整数，则 REGENERATE。
2。查询辅助脚本针对 `catalog.sqlite`（或远程端点——API 完全相同）提供有界、分页的操作。它 IS 消费契约（见下文“查询接口”）。
3。若 `catalog.sqlite` 不存在但 `structural_index.jsonl` 存在，查询辅助脚本回退为对 JSONL 文件进行线性扫描（较慢但可用）。若两者都不存在，则返回空结果，并设置 `coverage.partition_status = "empty"`。

### 步骤 4：写入清单并提交

1。首先将清单写入 `workspace/kb/structural_index/tmp/manifest.json`。然后以原子方式将其重命名为 `workspace/kb/structural_index/manifest.json`。这就是原子提交点——清单是在所有单元、catalog 和覆盖率数据写入之后 LAST 写入的。
2。将兼容性指针写入 `workspace/kb/structural_index.jsonl`（STATE-RELATIVE——在 `CODE_ROOT` 下 NEVER）。低于可配置阈值（默认：10K 条记录）时，输出完整的 JSONL 导出（来源头 + 全部记录）。高于阈值时，仅输出带有 `compat_pointer.full_export = false` 和 `compat_pointer.symbol_count` 设置的来源头。大型消费者 MUST 使用查询接口。
3。清单的 `provider.backend_versions` 字段记录每种语言所使用的后端（例如 `{"go": {"backend_name": "scip-clangd", "precision": "semantic"}, "python": {"backend_name": "tree-sitter", "precision": "ast"}}`），以便消费者了解精度级别。
4。被中断的构建会在 `tmp/` 中留下未被引用的临时对象，但不会损坏最后发布的索引。恢复时，检查 `manifest.json` 状态和 `snapshot_id`（步骤 1）。

### 步骤 5：返回结果 / 通知调用方

1。返回 `manifest.json`、`catalog.sqlite` 以及查询辅助脚本的路径。包含摘要（单元数、符号数、调用边数、所用后端、覆盖率状态）。
2。若索引为空（无可用工具或未找到源文件），通知调用方：“结构索引为空——结构上下文不可用。”
3。不要直接通知用户——本技能由 harness、planner 或 researcher 作为子代理调用。

## 索引模式与磁盘契约

### 磁盘布局

```
workspace/kb/structural_index/
├── manifest.json          # Atomic commit point — written LAST
├── catalog.sqlite         # Query-optimized serving store (both directions indexed)
├── units/                 # Content-addressed immutable unit outputs
│   └── ab/cd/abcdef...    # sha256 prefix sharding (2+2 hex dirs)
├── shards/                # Partitioned serving data (large corpora)
│   └── shard_0000.sqlite
├── native/                # Prebuilt index attachments (SCIP, Kythe, LSIF)
│   ├── provenance.json    # Prebuilt index provenance manifest
│   ├── scip/
│   └── kythe/
└── tmp/                   # Temporary objects during build

workspace/helpers/
├── build_structural_index.py    # Builder (MANTIS_HELPER_VERSION = 5)
└── query_structural_index.py    # Query helper (MANTIS_HELPER_VERSION = 5)

workspace/kb/structural_index.jsonl  # Compatibility pointer
```

### 清单模式（`manifest.json`）

```json
{
  "schema_version": 1,
  "snapshot_id": "<SNAPSHOT_ID or 'unknown'>",
  "root_fingerprint": "<sha256 of sorted (path, content_sha256) for all source files>",
  "status": "complete|partial|empty|failed",
  "provider": {
    "kind": "local-build|baseline+overlay|remote",
    "catalog": "catalog.sqlite",
    "backend_versions": {
      "cpp": {"backend_name": "tree-sitter", "backend_version": "0.20.8", "precision": "ast"},
      "go": {"backend_name": "scip-clangd", "backend_version": "0.2.3", "precision": "semantic"}
    }
  },
  "units": {"total": 0, "reused": 0, "rebuild": 0, "failed": 0},
  "coverage": {"total_files": 0, "indexed_files": 0, "failed_files": 0, "deferred_files": 0},
  "shards": [{"id": "", "path": "", "checksum": "", "partition_key": "", "symbol_count": 0, "edge_count": 0}],
  "deferred_units": [{"unit_id": "", "language": "", "files": [], "priority": 4, "reason": ""}],
  "native_indexes": [{"kind": "scip", "path": "", "snapshot_id": "", "root_fingerprint": "", "language": "", "indexer": "", "precision": ""}],
  "baseline": {"source": "ci|local|none", "snapshot_id": "", "manifest_path": ""},
  "overlay": {"units_added": 0, "units_modified": 0, "files": []},
  "compat_pointer": {"path": "structural_index.jsonl", "full_export": true, "symbol_count": 0},
  "created_at": "<ISO 8601>",
  "build_duration_ms": 0
}
```

**原子提交**：清单是 LAST 写入的（从 `tmp/` 原子重命名）。被中断的构建会留下未被引用的临时对象，但不会损坏最后发布的索引。

### SQLite Catalog 模式（`catalog.sqlite`）

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    namespace       TEXT,
    language        TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER,
    kind            TEXT NOT NULL,
    signature       TEXT,
    backend         TEXT NOT NULL,
    precision       TEXT NOT NULL CHECK (precision IN ('semantic','typecheck','ast','symbol-only','heuristic','deferred','coverage-only')),
    corpus          TEXT DEFAULT 'default',
    partition_key   TEXT,
    unit_cache_key  TEXT,
    source_layer    TEXT NOT NULL DEFAULT 'baseline'
);

CREATE TABLE IF NOT EXISTS call_edges (
    edge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_id   TEXT NOT NULL,
    callee_id   TEXT,
    callee_name TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    line        INTEGER NOT NULL,
    edge_kind   TEXT NOT NULL CHECK (edge_kind IN ('direct','indirect','virtual','macro','unresolved')),
    corpus      TEXT DEFAULT 'default',
    partition_key TEXT,
    source_layer TEXT NOT NULL DEFAULT 'baseline',
    FOREIGN KEY (caller_id) REFERENCES symbols(symbol_id),
    FOREIGN KEY (callee_id) REFERENCES symbols(symbol_id)
);

CREATE TABLE IF NOT EXISTS function_boundaries (
    symbol_id   TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    signature   TEXT,
    language    TEXT NOT NULL,
    FOREIGN KEY (symbol_id) REFERENCES symbols(symbol_id)
);

CREATE TABLE IF NOT EXISTS coverage (
    file_path   TEXT NOT NULL,
    indexed     INTEGER NOT NULL DEFAULT 0,
    backend     TEXT,
    precision   TEXT,
    unit_cache_key TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    corpus      TEXT DEFAULT 'default',
    partition_key TEXT,
    PRIMARY KEY (file_path, corpus)
);

CREATE TABLE IF NOT EXISTS unit_cache (
    cache_key       TEXT PRIMARY KEY,
    unit_id         TEXT NOT NULL,
    language        TEXT NOT NULL,
    extractor_name  TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    file_count      INTEGER NOT NULL,
    symbol_count    INTEGER NOT NULL,
    edge_count      INTEGER NOT NULL,
    source_bytes    INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    snapshot_id     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deferred_units (
    unit_id     TEXT PRIMARY KEY,
    language    TEXT NOT NULL,
    files       TEXT NOT NULL,
    priority    INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    cache_key   TEXT,
    created_at  TEXT NOT NULL
);

-- Indexes: both directions!
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path, start_line);
CREATE INDEX IF NOT EXISTS idx_symbols_lang ON symbols(language);
CREATE INDEX IF NOT EXISTS idx_edges_caller ON call_edges(caller_id);
CREATE INDEX IF NOT EXISTS idx_edges_callee ON call_edges(callee_id);
CREATE INDEX IF NOT EXISTS idx_edges_callee_name ON call_edges(callee_name);
CREATE INDEX IF NOT EXISTS idx_edges_file ON call_edges(file_path, line);
CREATE INDEX IF NOT EXISTS idx_boundaries_file ON function_boundaries(file_path, start_line, end_line);
CREATE INDEX IF NOT EXISTS idx_coverage_status ON coverage(status);
CREATE INDEX IF NOT EXISTS idx_deferred_priority ON deferred_units(priority);
```

**分区**：当符号超过 500K 或边超过 2M 时，按 `lang:{language}:bucket:{NN}`（稳定哈希）分区为 `shards/` 下的分片 SQLite 文件。每个分片具有相同的模式。查询辅助脚本根据清单将查询路由到正确的分片。

### 内容寻址缓存键

```python
cache_key = sha256(
    schema_version +
    extractor_name@version +
    language +
    compile_context_digest +
    ordered_input_content_digests +
    dependency_or_interface_digest
)
```

**关键**：`snapshot_id` NOT 包含在缓存键中。它仅出现在来源信息中。这允许在具有相同单元的提交、分支和快照之间复用。

### 规范符号 ID

**原生 ID**（当语义后端可用时）：

- SCIP: `scip:{symbol}`
- Kythe：`kythe:{uri}`
- clangd：`clangd:{usr}`

**回退 ID**（当无语义后端时）：

```
fallback:{language}:{file_path}:{sha256(qualified_name|start_line|signature)[:16]}
```

这可以区分命名空间、重载、方法、局部函数，以及跨语言或跨仓库的重名。

### 边类型

边类型被显式分开保留——绝不静默合并：

- `direct`——对已知符号的静态解析调用
- `indirect`——函数指针、闭包、回调
- `virtual`——虚方法分派（运行时解析）
- `macro`——由宏展开引入的调用
- `unresolved`——找到被调用方名称但未解析出 symbol_id

### 各语言语义单元

| 语言        | 单元类型         | 编译上下文                  | 依赖摘要                                    |
| ----------- | ---------------- | --------------------------- | ------------------------------------------- |
| C/C++       | 编译单元         | compile_commands.json 条目          | 传递头文件接口的 sha256                     |
| Go          | 包               | go.mod + 构建标签           | 被导入包导出的 API 的 sha256           |
| Rust        | Crate            | Cargo.toml + features       | extern crate 签名的 sha256                  |
| Java/Kotlin | 编译器批次       | 模块路径 + classpath        | 被导入类签名的 sha256                       |
| TypeScript  | 项目             | tsconfig.json               | 被导入模块类型声明的 sha256                 |
| 回退        | 单个文件         | 无                          | 空字符串                                    |

**回退粒度规则：** 对于回退层级（regex、AST 解析器，或无语言构建系统的轻量级爬虫），**每个源文件 MUST 作为其自身独立的语义单元**。绝不将多个源文件捆绑到单个回退单元中。按文件隔离单元对于内容寻址缓存效率至关重要——当未来快照中某个文件发生变化时，只有该单个文件的单元失效，而所有未变化的文件都命中缓存（`reused = N-1`）。

### 基线 + 增量叠加

- **静态基线**：在 CI 中为完整快照构建一次。
- **增量缓存**：不可变的单元输出在快照之间复用。
- **增量叠加**：变更/目标文件在本地建立索引，并叠加在基线之上。
- **远程服务**：当全局索引在本地代价过高时可选。

查询时，查询辅助程序合并基线 + 叠加层结果。对于同一文件，叠加层符号取代基线符号。清单记录 `provider.kind = "baseline+overlay"`，并附带基线清单引用和叠加层单元数量。

### 兼容性 JSONL 导出

旧的 `structural_index.jsonl` 格式仅作为兼容性导出保留——它 NOT 查询契约。低于阈值（默认 10K 条记录）时，输出完整的 JSONL 导出。高于阈值时，仅写入一个溯源指针。大型消费方 MUST 使用查询接口。

第 1 行——溯源头：

```json
{"_provenance": true, "snapshot_id": "abc123", "tool": "tree-sitter"}
```

第 2 行及之后——结构记录（每行一条，`_type` 判别字段）：

```json
{"_type": "function", "key": "src/parser.c:parse_input", "start_line": 45, "end_line": 120, "signature": "int parse_input(char *buf, size_t len)", "calls": ["malloc", "validate_input", "memcpy"]}
{"_type": "call_edge", "caller": "parse_input", "callee": "malloc", "file": "src/parser.c", "line": 78}
{"_type": "call_edge", "caller": "main", "callee": "parse_input", "file": "src/main.c", "line": 203}
```

**溯源头字段：**

| 字段          | 类型   | 描述                                                                                |
| ------------- | ------ | ----------------------------------------------------------------------------------- |
| `_provenance` | bool   | 始终为 `true`——标记此行为溯源头行                                                    |
| `snapshot_id` | string | 索引构建所针对的 `SNAPSHOT_ID`                                                  |
| `tool`        | string | 所使用的后端（例如 `"lsp-clangd"`、`"tree-sitter"`、`"ctags"`、`"regex"`、`"grep"`） |

**记录类型：**

| `_type`     | 描述                                            | 关键字段                                              |
| ----------- | ----------------------------------------------- | ----------------------------------------------------- |
| `function`  | 带边界和签名的函数定义                          | `key`、`start_line`、`end_line`、`signature`、`calls` |
| `call_edge` | 从调用方到被调用方在 file:line 处的一次调用      | `caller`、`callee`、`file`、`line`                    |

## 快照安全性

1. **从 `CODE_ROOT` 构建，而非活动目录树（当快照被固定时）。** 当快照被固定时，结构索引从被固定的 `CODE_ROOT` 构建，确保其反映流水线正在分析的精确字节。
2. **匹配即复用。** 如果 `manifest.json` 已携带当前 `SNAPSHOT_ID`（且 `SNAPSHOT_ID` NOT `"unknown"`——在 MODE-OFF 模式下，始终重建），则复用它——不要重建。当 `SNAPSHOT_ID` 不同或为 `"unknown"` 时重建。单个单元仍可从内容寻址缓存中复用。这限制了重试/崩溃恢复过程中的开销。
3. **清单原子性。** 清单 LAST 通过从 `tmp/` 原子重命名写入。被中断的构建会留下未被引用的临时对象，而不会损坏最后发布的索引。恢复时，检查 `manifest.json` 状态和 `snapshot_id`。
4. **HALT 模式下的 STALE 标志。** 当 `snapshot_pinned` 为 false（HALT）时，索引可从非固定的 `CODE_ROOT` 构建，但会被标记为可能过期。消费方将结构提示视为参考性建议。
5. **MODE-OFF：构建，不要跳过。** 当 `active_snapshot` 不存在（MODE-OFF）时，针对当前目录（cwd）构建，并将溯源 `snapshot_id` 设为 `"unknown"`。这是独立效率场景——即使没有快照固定，该索引对研究员/规划器仍然有用。NOT 仅因快照缺失就返回空索引。

## 消费契约

以下是面向调用方的运行时指令——它们定义消费方如何使用结构索引。结构索引是仅 HINT 的增强；不使用它的技能行为与当前完全一致。

### 查询接口

**查询辅助程序 IS 契约。** 所有消费方使用 `query_structural_index.py`（或兼容的远程端点）。JSONL 文件 NOT 查询契约——它仅是兼容性导出。

**操作**：

1. `resolve_symbol(name, language?, file?, namespace?)` →
   `{results, total, ambiguous, coverage}`

   - 返回 ALL 匹配项——不在有歧义的符号之间做静默选择。
   - 调用方 MUST 在调用 `find_callers` / `find_callees` 之前先消除歧义。

2. `find_callers(symbol_id, limit=100, offset=0)` →
   `{results, total, has_more, coverage}`

   - 有界、分页的调用方查找。
   - 每个结果携带 `precision`、`backend`、`edge_kind`。
   - 空结果携带 `coverage.partition_status`。

3. `find_callees(symbol_id, limit=100, offset=0)` →
   `{results, total, has_more, coverage}`

   - 与 `find_callers` 形状相同。

4. `get_function_boundary(file, line)` →
   `{symbol_id, start_line, end_line, signature, precision, backend}`

5. `get_coverage(file?)` →
   `{total_files, indexed_files, failed, deferred, partition_status, backends_used}`

**关键属性**：

- 有界结果与分页（防止将整个索引加载到内存中）。
- 在图遍历之前进行显式名称解析（不在有歧义的符号之间做静默选择）。
- 每个结果都附带精度和后端。
- 空结果附带覆盖率信息。
- 本地和远程提供方具有相同的操作（远程通过 `MANTIS_STRUCTURAL_INDEX_URL` 环境变量或清单 `provider.kind = "remote"`）。

**空结果上的覆盖率**：

| `partition_status` | 含义                          | 消费方操作                                               |
| ------------------ | ----------------------------- | -------------------------------------------------------- |
| `complete`         | 所有文件均已索引              | “无已索引的调用方”（仍按仅 HINT 规则运行 grep） |
| `partial`          | 部分文件被推迟或失败          | “未完全索引”——MUST 运行 grep 回退             |
| `empty`            | 无可用后端                    | “未索引”——MUST 运行 grep 回退                   |
| `failed`           | 后端已尝试但失败              | “索引失败”——MUST 运行 grep 回退                  |

### mantis-plan

- 使用结构索引查询辅助程序进行函数级依赖扇出。在规划调查时，先调用 `resolve_symbol()`，再调用 `find_callers()`，以识别所有调用目标函数的位置——这将审计集合扩展到单文件分析之外。
- 结构索引决定调查的 ORDER（基于调用图中心性决定先审计哪些函数），绝不决定 MEMBERSHIP。它可以扩大审计集合（安全的过度报告），但绝不能 REMOVE 或丢弃文件；规划器现有的逻辑仍是成员资格的下限。

### mantis-researcher

- **第 1 波（快速分诊）：** 对函数名运行全仓库 grep，以构建候选调用点的穷尽集合——这是强制下限。然后使用结构索引查询辅助程序（先 `resolve_symbol`，再 `find_callers`）来 RANK 并优先排序先审计哪些调用点（索引可区分实际调用与注释/字符串/变量名）。审计两个结果集的并集——结构索引可能遗漏基于宏的调用、函数指针和动态分派，因此 grep 仍是下限。
- **Wave 2（深度审计）：** 使用 `get_function_boundary(file, line)` 从所在的外层函数开始，按需扩展到调用方/被调用方/文件，以获得跨函数上下文——这样既能节省上下文，又能保持覆盖范围。
- 如果结构索引缺失或为空，则回退到基于 grep 的发现方式（即当前行为）。结构索引仅是一种覆盖 HINT——它能提升审计质量，但从来不是必需的。

## 安全性

不可协商的不变量：

1. **无关性（Agnostic）。** 任何内容都从来不是必需的；无工具 / 解析失败 / 未被调用 → 空索引 → grep 回退 = 与当前行为逐字节一致。在 Pass Lifecycle Contract 中为可选；不符合约定的 harness 直接跳过它即可。
2. **仅 HINT / 取并集 / 绝不 MEMBERSHIP。** 消费方用 grep 审计并集；索引遗漏的符号仍必须能通过穷举扫描触达。结构索引决定 ORDER，绝不决定 MEMBERSHIP。查询契约强制保证这一点：`find_callers` 返回 HINT，绝不返回权威成员关系——消费方 MUST 将其与 grep 结果取并集。
3. **不作出判定，不触碰任何发现项。** 不能违反 INV-1，其自身也不能丢弃任何发现项。
4. **范围狭窄：仅限源码交叉引用。** 二进制/构建产物派生的可达性（“它是否被编译进生产环境”）明确不在范围内——那是开发侧的自定义，不属于本技能，因为“未出现在某次构建中”可能掩盖真实的发现项（INV-2）。不要将构建派生的可达性并入本技能。

参考蓝图位于 [mantis-pipeline-adapter/references/mantis-structural-index.md](../mantis-pipeline-adapter/references/mantis-structural-index.md)。它只是一个存根，指向本 SKILL.md 作为唯一事实来源——不要在那里重复规格内容。
