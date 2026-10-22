---
name: mantis-threat-model
description: >-
  将信任边界、攻击面和攻击者画像综合为一份持续演进的威胁模型。
  用作知识库生成流程的阶段 B，从 KB 读取架构与实体定义。
  不要用于分析源代码，或从 JSONL 文件中提取原始经验。
---

# 威胁建模器（/mantis-threat-model）

## 系统目标

安全架构师。仅基于知识库（KB）中定义的实体与架构，将信任边界、攻击面和攻击者画像综合为 `THREAT_MODEL.md`。

## 命令定义

- **命令：** `/mantis-threat-model`
- **描述：** 评估架构周界、入口点和信任边界，以构建威胁模型。
- **参数（均为可选；缺省 → 采用当前行为）：**
  - `--state_root <dir>`：`workspace/` 的父目录。若缺省，则使用相对于当前目录的 `./workspace/...`。用于定位 `.mantis_state.json`、KB 以及归档目录。
  - `--snapshot_id <id>`：本轮所固定（pinned）的 `SNAPSHOT_ID`。ONLY 用作写入 `THREAT_MODEL.md` 的来源值（即 `KB_SNAPSHOT:` 行）。若缺省，则回退到状态中的 `active_snapshot.snapshot_id`；若该值也缺省，或 `snapshot_pinned` 为 false，则标记为字面量 `UNPINNED`。
  - `--snapshot_root <dir>`：为保持接口一致性而接受，但 NOT 使用——本阶段从不读取目标源代码（Block A 步骤 0，仅发现项角色）。

## 输入/输出契约

- **读取**：
  - `workspace/.mantis_state.json` —— `pass_number`（用于每轮归档），以及仅用于来源追溯的 `active_snapshot`（`{root, snapshot_id, snapshot_pinned}`）。两者均为可选；缺省 → 降级（见向后兼容）。
  - `workspace/kb/architecture.md`.
  - `workspace/kb/entities/*.md`.
  - 上一轮的 EXISTING `workspace/kb/THREAT_MODEL.md`（若存在）——仅读取其第一个 `KB_SNAPSHOT:` 行，用于步骤 0 中的新鲜度检查。
- **写入**：
  - `workspace/kb/THREAT_MODEL.md`（第一行为 `KB_SNAPSHOT:` 来源头）。
  - 将先前的 `workspace/kb/THREAT_MODEL.md`（如有）归档到 `workspace/archive/kb/THREAT_MODEL_pass_${N}.md`，且须在 BEFORE 覆盖之前完成。
- **前置条件**：
  - 知识库文件必须存在且已填充内容。
- **幂等性保证**：
  - 将先前的 `THREAT_MODEL.md`（若有）复制到本轮归档，然后确定性地就地覆盖 `workspace/kb/THREAT_MODEL.md`。在快照未变的情况下重跑同一轮，会复现一份等价的模型，并附加 `STALE` 横幅（见步骤 0 和最终保存步骤）。

## 指令

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

**本阶段的角色（仅发现项 / 仅 KB）。** `/mantis-threat-model` NEVER 读取目标源代码。它使用的每一项输入——`architecture.md`、`entities/*.md`、先前的 `THREAT_MODEL.md` 以及 `.mantis_state.json`——都是 STATE-RELATIVE（在 `state_root/workspace` 下读取，NEVER 在 `CODE_ROOT` 下读取）。因此本阶段采用 Block A 步骤 0 的 FINDINGS-ONLY 路径：SKIP Block A 步骤 2–6，但 STILL 从状态中读取 `active_snapshot` 以进行来源追溯，且 NEVER 仅因代码根未设置或未固定而停止。

维护一份高层威胁模型，明确界定攻击者*是谁*以及他们*在哪里*能与系统交互，并依赖 KB 中已预处理的实体。

按如下方式执行威胁建模流程：

0. **解析快照来源与新鲜度，并归档先前的模型：**

   a. **计算 `CUR`（本威胁模型所标记对应的快照）：**

   - 若传入了 `--snapshot_id` → `CUR` = 该值。
   - 否则，若状态 `active_snapshot.snapshot_pinned` 为 true → `CUR` = `active_snapshot.snapshot_id`。
   - 否则 → `CUR` = 字面量 `UNPINNED`（降级 / 当前行为）。

   b. **计算 `PREV_TM`（上一个模型所标记对应的快照）：** 在现有的 `workspace/kb/THREAT_MODEL.md` 中读取与 `^KB_SNAPSHOT:` 匹配的第一个 FIRST 行；其值（去除首尾空白后）即为 `PREV_TM`。若文件不存在、没有该行，或该值为空 → `PREV_TM` = 空字符串 `""`。

   c. **SYNC-DELTA DECISION（机械判定；精确字符串相等，不做模糊比较）：**

   - `CUR == "UNPINNED"` → `SYNC_OCCURRED = true`（降级：始终重新推导）。
   - 否则 `PREV_TM == ""` → `SYNC_OCCURRED = true`（首次运行 / 旧版 KB）。
   - 否则 `PREV_TM != CUR` → `SYNC_OCCURRED = true`（一次同步或脏编辑推进了快照）。
   - 否则（`PREV_TM == CUR`）→ `SYNC_OCCURRED = false`（自上一个模型以来快照未变）。

   d. **归档先前的模型（按轮次），使覆盖操作不具破坏性：**

   - 解析 `N`：从 `workspace/.mantis_state.json` 读取 `"pass_number"`。若缺失或无效，则扫描 `workspace/archive/` 中匹配 `findings_pass_N` 或 `loopN_findings` 的文件夹，并设置 `N = max_found + 1`；若不存在任何归档，则默认为 `1`。（与架构阶段使用的规则相同。）
   - 若 `workspace/kb/THREAT_MODEL.md` 存在：确保 `workspace/archive/kb/` 存在（`mkdir -p workspace/archive/kb/`），并 COPY（NOT 移动）该文件到 `workspace/archive/kb/THREAT_MODEL_pass_${N}.md`。你将在步骤 3 中覆盖该活动文件。若文件不存在，则跳过复制。
   - 本步骤中的所有路径均为 STATE-RELATIVE（位于 `state_root/workspace` 下）；NEVER 添加 `CODE_ROOT` 前缀。

   将 `CUR`、`SYNC_OCCURRED` 和 `N` 传递到步骤 1–3 以及最终保存中。

1. **读取已综合的 KB：**

   - 读取 `workspace/kb/architecture.md`，以理解系统的数据流和高层设计。
   - 读取 `workspace/kb/entities/` 内的文件，以理解各个组件，以及 `/mantis-architecture` 阶段映射到这些组件上的任何历史约束或漏洞模式。

2. **分析信任边界：**

   - 评估各实体，以确定信任边界所在位置。不可信数据在何处跨越进入可信上下文？哪些组件暴露于外部输入？

3. **综合威胁模型：**

   - 编写一份全面、结构化的 Markdown 文件，并直接保存到 `workspace/kb/THREAT_MODEL.md`（覆盖旧文件）。
   - **Token 优化：** 使用你的文件写入工具将文件直接写入磁盘；不要在聊天回复中输出威胁模型文本。

   包含以下章节，以确保下游规划智能体拥有足够的上下文：

   - **系统概览摘要：** 从 `architecture.md` 推导出的简明摘要。

   - **部署意图：** 必须且只能声明 `Intent: PRODUCTION` 或 `Intent: SAMPLE_OR_TEST_ONLY` 之一。该判定具有很大的影响范围：`/mantis-critic` 一旦读取到 `Intent: SAMPLE_OR_TEST_ONLY`，就会将 EVERY 发现项标记为 `SAMPLE_OR_TEST`（从而驳回整轮）。因此，`SAMPLE_OR_TEST_ONLY` 必须 FAIL-CLOSED，并依据一份机械化的检查清单判定：

     **PRODUCTION-SIGNAL CHECKLIST — 你可以写入 `Intent: SAMPLE_OR_TEST_ONLY`
     仅当全部五项检查均为 TRUE 时，才可写入 ONLY IF ALL。如果 ANY 一项为 FALSE，或 KB 对 / 未作说明，
     或你对其中任何一项不确定，你 MUST 写入
     `Intent: PRODUCTION`。**

     1. NO 在 `workspace/kb/entities/*.md` 中的实体被分类为 `CRITICAL` 或
        `STANDARD` 可用性（任一即意味着这是一个被运营的/生产服务）。
     2. `architecture.md` 没有（NO）列出任何外部可达的服务、守护进程、服务器、API 或网络端点，并且（AND）没有（NO）任何部署或打包描述符（systemd、Dockerfile/`docker`、kubernetes/`k8s`/helm、负载均衡器、云/VPC/基础设施即代码、CI/CD 发布或发行）。
     3. KB 没有（NO）描述可安装或可发布的软件包或运行时入口（例如 `console_scripts`/`entry_points`、`main()`/服务二进制、已发布的库或包清单）。
     4. KB 中引用的 EVERY 组件/路径完全位于
        测试/示例目录之下——其路径包含 `test`、`tests`、
        `example`、`examples`、`sample`、`samples`、`tutorial`、`demo`、`docs`、
        `fixtures` 之一——且 NONE 位于生产源码根目录之下，例如 `src`、
        `lib`、`pkg`、`internal`、`cmd`、`app`、`server` 或 `core`。
     5. NO 实体记录了真实的（非 mock、非测试）不可信外部输入
        跨越信任边界进入特权/生产逻辑。

     **在一次同步之后（来自步骤 0 的 `SYNC_OCCURRED == true`），你 MUST 针对 CURRENT KB 从头重新运行本
     检查清单，且 MUST NOT 继承先前的
     `Intent:` 判定**——同步可能向原本仅为示例的代码树中加入生产代码，
     否则这会静默地忽略掉每一个新发现。

   - **信任边界：** 清晰、严谨地定义不可信输入与内部可信状态
     相接的位置。引用具体的实体（例如
     `[Auth Module](entities/auth_module.md)`）。

   - **威胁行为者与攻击向量：** 定义潜在攻击者的画像
     （例如未认证的网络攻击者、恶意本地用户）以及他们能够触及的
     具体边界。

   - **高风险资产：** 攻击者想要破坏的数据、执行权限或可用性
     目标。**对于可用性目标，请根据 KB 将其归入以下可用性层级之一：**

     - `CRITICAL`：一旦中断，24/7 立即产生运营影响。
     - `STANDARD`：重要业务；短暂停机可容忍。
     - `LOW_CRITICALITY`：非阻塞性工具；中断仅造成轻微
       困扰。

为模型加盖标记并保存：

- `workspace/kb/THREAT_MODEL.md` 的 FIRST 行 MUST 是来源标记
  `KB_SNAPSHOT: ` 后接 `CUR`（来自步骤 0）——例如 `KB_SNAPSHOT: <CUR>`，
  或在降级模式下为 `KB_SNAPSHOT: UNPINNED`。下一轮会将其读回为
  `PREV_TM`，而 `mantis-critic` 会将其读取为 KB 新鲜度检查。
- 如果 `SYNC_OCCURRED == false`（自上次模型以来快照未变）：你 MAY
  复用先前模型的实质内容，但你 MUST 将此 STALE 横幅插入为
  `KB_SNAPSHOT:` 行紧 AFTER 的那一行：
  `> STALE: Threat model NOT re-evaluated this pass; carried unchanged from snapshot <CUR>.`
  如果 `SYNC_OCCURRED == true`：则 NOT 输出 STALE 横幅——你在本轮已
  重新推导了每个章节，包括部署意图检查清单。
- 直接保存到 `workspace/kb/THREAT_MODEL.md`（STATE-RELATIVE；先前的
  文件已在步骤 0d 中复制到本轮归档）。使用你的文件写入
  工具；不要将模型打印到聊天中。完成后，通知用户。
