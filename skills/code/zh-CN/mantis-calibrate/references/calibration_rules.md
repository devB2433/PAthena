# 校准规则目录

本文档定义 27 校准合理性分诊规则（上限与降级），用于计算发现的最终严重性与优先级。

## 目录

- [核心原则：边际能力](#core-principle-marginal-capability)
- [类别 A：强制降级至 LOW（上限为 2.0 / LOW 优先级）](#category-a-force-downgrade-to-low-cap-at-20--low-priority)
- [类别 B：强制封顶至 HIGH（上限为 7.9 / 最高 HIGH 优先级）](#category-b-force-cap-to-high-cap-at-79--maximum-high-priority)
- [类别 C：强制封顶至 MEDIUM（上限为 5.9 / 最高 MEDIUM 优先级）](#category-c-force-cap-to-medium-cap-at-59--maximum-medium-priority)

## 核心原则：边际能力

发现的最终严重性与优先级严格受限于攻击者相对于其前置位置所获得的**边际能力**。如果该利用未能使攻击者获得超出其起始位置固有能力（或已通过合法手段拥有）的显著新控制、访问权限或能力，则必须对该发现进行封顶或降级。

______________________________________________________________________

### 类别 A：强制降级至 LOW（上限为 2.0 / LOW 优先级）

01. **`repro_failure`（复现失败或未尝试）** 复现失败（`repro_status: "failed_to_reproduce"`）、未尝试复现（`repro_status: "not_attempted"`），或 `repro_status` 字段缺失（视为 `"not_attempted"`），无论理论上的生产环境可行性如何。

    **快照豁免（STALE_EVIDENCE）：** 当该发现为 NOT_MATCHED 于活动快照，或其 `code_paths` 文件在固定的 CODE_ROOT 下缺失时（参见 `mantis-calibrate/SKILL.md` 中的 STALE-EVIDENCE 守卫），NOT 触发本规则。针对已漂移或缺失代码的复现失败/缺失不具信息价值；应保留较高评分，并将 `calibration_checklist.repro_failure.outcome = "UNKNOWN"` 标记为带有 `STALE_EVIDENCE:` 原因，而不是强制降级至 LOW。

02. **`unreachable_inputs`（不可达 / 不可控输入）** 该发现依赖于被记录为极不可能由用户控制的输入，且未证明存在从信任边界出发的路径。

03. **`third_party_reachability`（第三方 / 供应链可达性）** 第三方库中的漏洞（依赖项 CVE），且未主动证明存在从应用输入到易受攻击函数的可达路径。

04. **`minor_config_hygiene`（轻微配置卫生问题）** 与最佳实践的轻微偏差（例如，内部目录权限略宽松、低价值内部传输缺少现代加密），且无明确的利用路径。

05. **`non_security_critical`（非安全关键组件）** 该发现影响的组件或数据不具备安全敏感性（例如，公开信息、非安全载荷上的签名、装饰性输出）。

06. **`vague_code_paths`（模糊代码路径 / 脆弱假设）** 依赖于关于调用方行为或相邻系统组件的未经证实的假设。

    **快照豁免（STALE_EVIDENCE）：** 当该发现为 NOT_MATCHED 于活动快照，或其 `code_paths` 文件在固定的 CODE_ROOT 下缺失时，NOT 触发本规则。针对已漂移的调用方重新检查“调用方行为”毫无意义；应保留较高评分，并将 `calibration_checklist.vague_code_paths.outcome = "UNKNOWN"` 标记为带有 `STALE_EVIDENCE:` 原因。对于非源码的 LOCATOR 发现，同样跳过本规则。

07. **`unreliable_triggers`（不可靠 / 噪声触发）** 在实践中很可能被忽略或与正常操作无法区分的触发条件。

08. **`prerequisite_shell`（前置 Shell 访问 / 等效原语）** 攻击者已在目标容器或主机上拥有本地 shell 访问权限，且其权限级别**等于或高于**该利用所能提供的级别，根据边际能力原则，所获得的访问权限是冗余的（例如，在已以标准用户登录的情况下利用缺陷获取标准用户 shell，或在已以 root 运行的情况下利用本地缓冲区溢出来以 root 执行命令）。本规则 NOT 适用于低到高权限提升（例如，标准用户提升至 root），此类情况应封顶至 MEDIUM。

09. **`physical_long_term`（物理长期 / 实验室访问）** 如果攻击需要对设备的长期物理访问或专用实验室设备（例如，故障注入、侧信道分析、芯片开盖）。由于极高的执行门槛和对物理占有的要求，强制降级至 **LOW（2.0）**。

10. **`trusted_controller_zero_delta`（受信任控制器中介的接口 - 零增量）** 如果易受攻击的接口仅可从对目标拥有设计上权威控制权的组件访问（例如，编排器->工作节点、驱动->设备固件、协议主站->从站、hypervisor->客户机、管理平面->数据平面节点），且该利用授予**零边际能力**（即，该控制器已可通过其标准、合法接口实现完全相同的效果或同等程度的攻陷），则强制降级至 **LOW（2.0）**。（这泛化了下文中的*标准主机到客户机攻击*规则）。

11. **`standard_host_attacks`（标准主机到客户机攻击）** 如果攻击者位置为 `HOST_SYSTEM`（主机 hypervisor 攻击客户机），且处于标准部署（非机密计算）。强制降级至 **LOW（2.0）**，因为主机 OS/hypervisor 在设计上已对客户机拥有完全控制权，这意味着该利用相对于前置位置提供零边际能力（等效原语）。**默认假设：** 视为非机密计算（本规则触发），UNLESS 威胁模型、代码路径或发现描述明确提及机密计算、客户机飞地、TEE、SEV、TDX、SGX 或证明（此时应改为应用 CC 主机攻击封顶至 HIGH 的规则）。

______________________________________________________________________

### 类别 B：强制封顶至 HIGH（上限 7.9 / 最高 HIGH 优先级）

1. **`static_confirmation`（静态确认）** 已静态确认但未经验证性复现（`repro_status: "statically_confirmed"`）。将 `likelihood_score` 封顶于 **3**，对 Hazard 应用 **0.8** 乘数，且 MUST NOT 为 CRITICAL。*例外：* 若发现详情（描述、历史记录或复现输出）中包含有效的外部堆栈跟踪、sanitizer 跟踪（例如 ASan、UBSan、MSan）、崩溃日志或核心转储，证明该漏洞在执行中被触发（例如在先前的运行中或由外部工具触发），则将其视为已经验证性复现（Likelihood 5），并且不应用此静态封顶。

   **快照豁免（STALE_EVIDENCE）：** 当发现为 NOT_MATCHED 于活动快照，或其 `code_paths` 文件在固定的 CODE_ROOT 下不存在时，上述跟踪提升*例外* MUST NOT 被应用——在先前快照上捕获的堆栈跟踪 / ASan / 崩溃日志可能并不对应当前代码，因此将其视为实时复现会错误地抬高至 CRITICAL。在此情况下 KEEP 静态 HIGH 封顶生效，并以 `STALE_EVIDENCE:` 原因标记 `calibration_checklist.static_confirmation.outcome = "APPLIES"`，注明跟踪提升已被抑制。（基础静态封顶本身由来自发现 JSON 的 `repro_status: "statically_confirmed"` 驱动，并且仍正常适用。）

2. **`strict_xss`（严格 XSS 封顶）** 所有 XSS 漏洞。默认为 MEDIUM 或 LOW；仅对关键管理页面上、且对管理员为零点击执行的存储型 XSS，才封顶于 HIGH（7.9）。

3. **`internal_nested`（内部 / 嵌套组件）** 任何 Network/Trust Exposure 乘数小于 1.0 的发现（即内部组件或特权区域）。若计算得分落在 CRITICAL 区间，则将得分封顶于 **7.9** 并将优先级降级为 HIGH。*例外：* 若该组件是集群内核心基础设施（例如 CNI、CSI、准入 webhook、服务网格）AND 影响会逃逸到宿主节点（例如节点 root 文件读写）或允许跨租户提权，则 NOT 封顶于 HIGH。这些仍可评为 CRITICAL。**当 `attacker_position` 为 `"EXTERNAL"` 时，本规则 MUST NOT 触发（因为根据第 2 节中的对齐规则，暴露度被强制为 `EXPOSED`（1.0），从而排除了此封顶）。**

4. **`probabilistic_llm`（概率性 LLM 向量）** 依赖概率性 LLM 行为（例如提示注入、越狱）来触发漏洞的攻击。封顶于 **HIGH**（7.9），并默认为 **MEDIUM** 或 **LOW**。*例外：* 若攻击者可以反复查询 LLM/系统，且不存在会阻碍攻击的速率限制、并发限制或安全阻断/告警（使其能够暴力尝试并有效消除不确定性），则可解除此封顶。

5. **`supply_chain_prerequisites`（供应链 / 构建期前置条件）** 若利用该漏洞要求攻击者已具备供应链位置（例如能够污染依赖、修改上游源码）或对构建流水线具有写访问权限。封顶于 **HIGH（7.9）**，因为进入门槛极高，但下游失陷是系统性的。（仅当攻击者已拥有目标上的 shell 访问权限时，才按“前置 Shell 访问”规则强制降级为 LOW/2.0。）

6. **`non_default_config`（非默认配置）** 仅在非默认配置下可利用的发现。封顶于 **HIGH（7.9）**，以反映额外的配置门槛。

7. **`confidential_computing_host`（机密计算宿主攻击）** 在机密计算部署中，若攻击者位置为 `HOST_SYSTEM`（宿主 OS 或 hypervisor 攻击客户机 enclave 或机密 VM）。封顶于 **HIGH（7.9）**，因为尽管宿主完全控制平台，机密计算 enclave 的设计目的正是抵御宿主级失陷。（若非 CC 部署，请参见 LOW 下的标准宿主到客户机攻击规则。）

8. **`trusted_controller_critical_bypass`（受信任控制器中介接口 - 关键绕过）** 若易受攻击的接口仅可从设计上内置的权威控制器访问，且该利用允许该控制器绕过其本应遵守的目标侧**已记录的安全控制**或**生命安全限制**，则封顶于 **HIGH（7.9）**。（若该利用允许横向进入不同的信任域，或实现能在控制器重新配置后存活的持久化，则不封顶。）

______________________________________________________________________

### 类别 C：强制封顶至 MEDIUM（上限 5.9 / 最高 MEDIUM 优先级）

1. **`local_attack_vector`（本地攻击向量）** 需要本地 shell 访问权限（例如本地提权、SUID 利用）且无 VM 逃逸的漏洞。（若仅影响单个用户的隔离数据，则降级为 LOW/2.0。）

2. **`self_contained_blast`（自包含影响范围）** 若该利用的最大影响仅限于触发主体已拥有或具有完全内置权限的资源、数据或执行上下文——其自己的账户、租户、项目、命名空间、容器、VM、设备或单用户安装——且不跨越互不信任主体之间的任何隔离边界，则封顶于 **MEDIUM（5.9）**。

   - 该利用可能在该域内授予真正的新能力（例如 API 用户 -> 其自己容器内的 shell），但部署对其他方的核心隔离保证仍然成立。
   - 若该利用符合以下情况，则 **NOT** 应用此封顶：
     - 触及另一主体的资源（跨租户、跨用户、跨账户），
     - 触及共享或多方基础设施（共享缓存、共享文件系统、operator 控制平面、同租户侧信道），
     - 将攻击者的域置于其他组件之上游（构建节点、CI runner、包注册表、模型服务主机——即供应链位置），或
     - 以能够存活于主体自身资源生命周期之外的方式持久化，并且之后可能影响复用该槽位的其他主体。

3. **`rarely_exposed`（极少暴露的组件）** 在文档中标注为“极少暴露”或“不太可能由用户控制”的组件中的发现。

4. **`equivalent_primitives`（等价原语——未突破边界）** 能够触发该漏洞的攻击者画像，已通过标准系统功能拥有等价的访问权限、特权或能力（原语）（例如，管理员利用缺陷下载其本已可通过 UI 下载的文件）。由于这相对于其前置位置仅提供较低的边际能力，上限设为 **MEDIUM（5.9）**，以保持纵深防御清理的可见性。

5. **`documented_insecure_config`（已记录的不安全配置）** 在公开手册中明确记录为不安全、仅用于诊断或严格非生产用途的非默认配置。上限设为 **MEDIUM（5.9）**。

6. **`physical_temporary`（物理临时访问）** 如果攻击需要对设备的临时物理访问（例如 USB 密钥插入、evil maid 攻击），且不涉及长期实验室分析。上限设为 **MEDIUM（5.9）**。

7. **`high_privilege_external`（高特权外部访问）** 具有 `attacker_position: "EXTERNAL"` 且要求 `privileges_required: "HIGH"` 的利用（例如在公开门户上的管理员 RCE）。上限设为 **MEDIUM（5.9）**，除非该利用导致逃逸容器边界（至宿主节点）或跨租户提权。

8. **`trusted_controller_standard_bypass`（受信任控制器中介的接口——标准绕过）** 如果易受攻击的接口仅可从设计内置的权威控制器访问，且该利用允许该控制器绕过其本应遵守的目标侧**标准安全或合理性限制**（但不包括关键的生命安全控制或已记录的安全控制），上限设为 **MEDIUM（5.9）**。
