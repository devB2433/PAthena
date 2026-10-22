---
name: mantis-history
description: >-
  分析仓库的版本控制系统（VCS）历史，以提取过往漏洞、安全修复和漏洞模式。
  作为初始预处理步骤使用，用于构建历史漏洞数据库（workspace/historical_learnings.jsonl），以便向后续阶段提供过往问题与修复的信息。
  不要用于代码审查、编写测试脚本或修补代码。
---

# 历史分析器（/mantis-history）

## 系统目标

历史漏洞提取器。分析仓库的版本控制系统（VCS）历史，以提取过往漏洞、与安全相关的修复、补丁及相关文件，创建历史学习数据库，为下游技能提供信息。

## 命令定义

- **命令：** `/mantis-history`
- **描述：** 分析仓库的版本控制系统（VCS）历史，以提取过往漏洞与修复，生成结构化的历史学习文件（`workspace/historical_learnings.jsonl`）。
- **参数（可选；由编排器提供，由 Block A 消费）：**
  `--snapshot_root`/`--snapshot_id`/`--state_root`。History 从 LIVE 仓库根目录读取 VCS 日志（Block A 步骤 5），而不是从快照读取；它仅使用 `--state_root` 来放置其缓存/输出/脚本。全部缺失 -> DEGRADED（行为与当前一致，使用实时 cwd）。

## 输入/输出契约

- **读取**：
  - `workspace/.mantis_state.json`（用于跟踪当前循环轮次）。
  - 代码库目录结构和关键文件（用于确定技术栈）。
  - VCS 历史日志（提交信息、标题、差异）。
  - `mantis-summary.md`（可选，若可用）。
  - 内部历史分析缓存（可选，若存在）。
- **写入**：
  - VCS 提取脚本（即时写入工作区）。
  - `workspace/historical_learnings.jsonl`.
  - 内部历史分析缓存。
  - 全部位于 `--state_root/workspace/` 之下（缓存、`historical_learnings.jsonl`、提取脚本）：保持在目标树之外。可选的 `history_status` 标记（`UNSUPPORTED_VCS` / `PARTIAL_SHALLOW`）。
- **前置条件**：
  - 目标仓库和 VCS 日志必须可访问。
- **幂等性保证**：
  - 缓存是唯一事实来源：每次运行都从缓存重建输出 DB，且当缓存非空时，输出绝不会被截断为空。当发生 VCS 历史重写（`_analyzed_head` 不再可达）或 vcs_type/仓库标识发生变化时，缓存失效。在 `none`/`unknown` VCS 情况下，该阶段写入空的 DB + `history_status=UNSUPPORTED_VCS` 并退出。

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

CRITICAL 关于历史：固定快照副本 STRIPS `.git`/`.hg`/`.repo`，因此按照 Block A 步骤 5，你 MUST 在 LIVE 仓库根目录（启动 Mantis 时所在的工作目录）中运行每一条 VCS 日志 / 差异 / blame 命令，NOT 在 CODE_ROOT 下运行。NOT 因为 CODE_ROOT 缺少 VCS 元数据而停止。将缓存、`workspace/historical_learnings.jsonl` 以及你生成的提取脚本写入 `--state_root/workspace/`（STATE-RELATIVE）之下——绝不要写入目标树，以免同步/清理将其清除。仅在条目上记录 `active_snapshot.snapshot_id` 用于溯源。

你的任务是分析代码库架构，确定哪些内容构成与安全相关的历史，并即时编写脚本，从项目的版本控制系统（VCS）中提取并记录历史漏洞。

按如下方式执行历史分析阶段：

1. **阶段 1：分析代码库并定义目标历史：**

   - 阅读现有摘要（例如 `mantis-summary.md`，若可用），或快速检查根代码库结构，以了解主要文件、编程语言和核心组件。
   - 确定哪些类型的历史安全问题（例如内存破坏、认证绕过、SQL 注入、缓冲区溢出或其他）与本项目相关，以及应针对哪些关键词或提交。

2. **阶段 2：编写历史提取脚本：**

   - **VCS 支持防护（在编写/执行脚本 BEFORE 运行）：** 从 `.mantis_state.json` `vcs_info` 确定 `vcs_type`（或在 LIVE 根目录中检测它）。如果 `vcs_type` 为 `none` 或 `unknown`，OR VCS 历史在其他情况下不可达：写入一个 EMPTY `workspace/historical_learnings.jsonl`，在文件顶部/附属文件设置标记 `history_status = "UNSUPPORTED_VCS"`，并 EXIT——绝不编造历史。对于 `multi-vcs`（.repo）：要么按子项目逐个遍历历史，要么写入带有 `history_status = "UNSUPPORTED_VCS"` 的空文件，而不是运行会报错的 git 形式脚本。对于 SHALLOW git 克隆（`git rev-parse --is-shallow-repository` == true）：继续执行，但设置 `history_status = "PARTIAL_SHALLOW"`，以便下游阶段 NOT 将“该文件无历史漏洞”误读为“干净”。

   - 从状态文件 `workspace/.mantis_state.json` 读取当前轮次编号，并解析当前 ISO 8601 时间戳。

   - 直接在你的工作区中编写脚本（例如 Python、bash 或你选择的语言），与你的仓库版本控制系统（VCS）历史日志交互。

   - **成本效益与规模优化：** 为确保历史分析保持成本效益并高效运行，脚本必须实现以下优化：

     - **提交信息/描述预过滤：** 在获取差异之前，脚本应动态确定用于提交信息筛选的相关关键词。它可以通过以下方式实现：首先检查仓库中范围内的文件与语言/领域，以确定其主要技术栈（例如软件与硬件/RTL），然后要么向 LLM/分析智能体查询定制的安全/缺陷关键词列表，要么使用更广泛的关键词集并补充这些特定领域术语。跳过信息或标题不匹配这些相关关键词的修订，以避免不必要地获取和分析与范围内代码库无关的变更集。
     - **Diff 过滤与大小限制：** 忽略仅涉及非生产代码（例如测试、文档或配置文件）的提交。跳过 diff 过大的提交（例如变更超过数千行），因为它们通常是自动化格式化变更或大规模重构，而非离散的安全补丁。
     - **缓存结果（缓存是事实来源）：** 在 `workspace/` 下维护一个本地缓存（JSON 或 SQLite），以 `revision_id -> analyzed` AND 为映射，为每个已分析的修订版本存储完整的提取记录，并存储 `_analyzed_head` 高水位标记以及该缓存构建时所依据的 `vcs_type` + 仓库标识。每次运行时，REBUILD `workspace/historical_learnings.jsonl` 中的内容（NOT 仅因缓存标记为“analyzed”就跳过某个修订版本，随后又让输出保持为空——那正是失步缺陷）。NEVER 将非空的输出 DB 截断为空。仅分析比 `_analyzed_head` NEWER 的修订版本。**重写检测：** 在信任缓存之前，验证 `_analyzed_head` 在当前 LIVE 历史中仍可解析（git：`git cat-file -e <_analyzed_head>` 成功 AND `git merge-base --is-ancestor <_analyzed_head> HEAD` 成功；hg：`hg log -r <_analyzed_head>` 成功）。如果它不可解析（force-push / rebase / squash）OR `vcs_type`/仓库标识已改变，则 INVALIDATE 缓存并从头重新提取。
     - **批处理（批量处理 Diff）：** 脚本不应为每个提交 diff 单独发起一次 LLM 调用，而应将多个提交 diff 与提交消息（例如 3 到 5 个提交）批量合并为单次 LLM 调用。要求 LLM 分析该批次中的所有提交，并返回该批次的 JSON 发现结果数组，从而降低请求开销与成本。
     - **模型选择：** 在初始提交分析/过滤阶段使用轻量且成本高效的模型，仅在需要对疑似漏洞进行更深入验证时，才回退到更重的模型层级。

   - **详细分析：** 对于看起来与安全相关的修订或提交，脚本应获取提交 diff 以及提交消息/变更描述。

   - **LLM/Agent 分析：** 脚本应发起 API 调用（例如通过 subagent 消息或 Agent API 子命令），或使用 LLM subagent 来分析变更的 diff 与消息，以判定某个修订/提交是否为安全修复、影响了哪个组件、漏洞类型、影响以及缓解 diff。确保使用批处理与缓存以尽量减少 API 调用。

   - **信息缺失：** 不要过度依赖安全相关关键词的缺失。许多安全修复并未获得 CVE，许多安全漏洞在修复时并未被意识到是漏洞。

   - **输出格式：** 指示你的脚本将提取的发现结果输出到名为 `workspace/historical_learnings.jsonl` 的 JSONL 文件中，格式如下：

### 历史学习记录 Schema 格式（`workspace/historical_learnings.jsonl`）

```json
{
  "revision_id": "...",
  "title": "...",
  "description": "...",
  "code_paths": ["file:line_number"],
  "vuln_type": "...",
  "mitigation_diff": "...",
  "cve": "...",
  "history": [
    {
      "stage": "history_extractor",
      "action": "created",
      "details": "Extracted from repository revision history.",
      "pass_number": <current_pass_number>,
      "timestamp": "<current_iso8601_timestamp>"
    }
  ]
}
```

1. **阶段 3：执行并验证：**
   - 运行你刚刚编写的脚本，以分析 VCS 历史、处理修订并生成 `workspace/historical_learnings.jsonl` 文件。
   - 等待脚本完成，并验证 `workspace/historical_learnings.jsonl` 已成功写入且包含提取的数据。

完成后，通知用户。
