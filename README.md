# PAthena

从设计文档和源码生成安全需求、威胁模型、需求实现检查与 Findings 的静态安全分析系统。

四个阶段使用统一的 Google ADK 调度框架。文档分析和需求检查采用本项目的领域逻辑；代码理解、威胁建模和 Findings 审计直接运行固定版本的 Mantis 核心与完整技能。系统不执行目标程序，不提供动态复现或自动补丁功能，也不预置项目、文档或演示结果。

## 四个输出物

| 阶段 | 输入与处理 | 输出 |
| --- | --- | --- |
| 安全需求 | 解析设计文档，提取事实和安全需求，检索并匹配结构化 PCI DSS 条款 | 需求、验收条件、设计来源、关联条款与适用条件 |
| 威胁建模 | Mantis 读取固定源码快照，建立架构、信任边界和攻击路径 | 威胁模型与具体威胁 |
| 需求实现情况 | 每项需求创建独立检查任务，逐条检查验收条件 | 与需求一一对应的检查结果及源码位置 |
| Findings | Mantis 规划、研究、去重、静态复核、质疑、攻击链分析和评级 | Findings、影响、原因、建议和代码位置；已排除候选单独保留 |

安全需求与实现检查共用运行内稳定的 `SR-001` 编号。每条验收条件必须恰好提交一次。未检查、无法确认和需要外部配置的情况分别保留，不能以代码片段缺失直接判定需求未实现。

## 技术栈

- 后端：Python 3.12、FastAPI、Google ADK、LiteLLM、Pydantic。
- 前端：TypeScript、React、Vite、Ant Design。
- 数据库：SQLite、WAL、FTS5；结构化标准库、任务、原始模型返回和结果持久化。
- 文档解析：Docling；源码导航：Mantis 与本地 tree-sitter 语法资源。
- 模型：默认 DeepSeek `deepseek-flash`，通过独立模型网关调用。
- 部署：分析服务和模型网关两个容器，前端由分析服务同源提供。

默认输出语言为英文。系统设置选择中文后，后续分析的技能和 Prompt 直接要求中文生成，不对已有结果做二次翻译；每次分析冻结自己的输出语言。

## 容器部署

需要 Docker Compose。首次启动前创建本地配置并准备材料目录：

```sh
cp .env.example .env
mkdir -p inputs models standards
```

编辑 `.env`，填写 `DEEPSEEK_API_KEY`。密钥只提供给模型网关；`.env`、输入材料、标准原文、模型、数据库和报告均不进入版本控制。

```sh
docker compose --env-file .env -f deploy/compose.yaml build
docker compose --env-file .env -f deploy/compose.yaml up -d
```

访问 <http://127.0.0.1:8088>。首次启动项目列表为空。创建项目、上传设计文档并选择源码仓库后即可开始分析。

目标源码可放在 `inputs/project`，在 `.env` 中配置：

```dotenv
AUDITOR_REPOSITORIES={"project":"/inputs/project"}
```

也可使用外部仓库只读挂载模板：

```sh
mkdir -p deploy/local
cp deploy/compose.repositories.example.yaml deploy/local/repositories.yaml
```

在 `.env` 中设置 `AUDITOR_REPOSITORY_DIR` 为宿主机仓库绝对路径，并按需要编辑模板内的登记名称。启动时叠加配置：

```sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/local/repositories.yaml up -d
```

网关只转发至 `api.deepseek.com`，拒绝跨主机重定向；分析服务只有内部网络。Compose 尚不提供操作系统级域名防火墙，需要部署环境落实相应出口限制。DeepSeek 会接收分析所需的材料片段。

状态保存在 `auditor-state` 卷内。常规服务重建会保留数据；不要在需要保留分析结果时删除该卷。

## 文档模型与标准库

Docling 使用本地解析模型。可在安装准备阶段下载资源：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/docling-tools models download layout tableformer --output-dir models/docling
```

容器内启用解析模型时，在 `.env` 设置：

```dotenv
AUDITOR_DOCLING_MODELS=/models/docling
```

资源准备与分析运行分开，解析进程不在线下载资源。当前支持 Word、PPT 和带文字的 PDF；扫描页 OCR 与图形语义覆盖仍有待完善。

PCI DSS 原文及结构化标准包不随仓库分发。使用自己的 PCI DSS v4.0.1 PDF 离线导入：

```sh
.venv/bin/python -m pip install -e '.[standards]'
.venv/bin/python -m security_auditor.standards /path/PCI-DSS-v4_0_1.pdf standards/pci-dss-4.0.1
```

导入目录必须为空。可用 `--provenance /path/source.json` 附带来源记录。导入器保留原 PDF、条款原文、适用性说明、测试程序、指导与来源摘要；不调用模型改写标准。

在 `.env` 中设置 `AUDITOR_STANDARD_PACK=/standards/pci-dss-4.0.1`。结构化标准版本一次入库，各项目复用，运行冻结标准版本。需求匹配通过本地检索和完整条款读取，分别记录技术相关性与正式适用性；相关或可能相关的控制形成保留适用条件的需求。候选检索不证明全标准语义覆盖，也不产生合规认证结论。未配置标准包时，合规步骤保留缺口并跳过。

## 分析模式

- `full`：设计需求、标准匹配、代码建模、实现检查和 Findings。
- `requirements_only`：只分析设计文档和标准需求。
- `code_only`：只进行 Mantis 静态建模与 Findings 审计，不读取设计文档。
- `implementation_only`：从同项目已有运行导入成功提交的需求和来源，只检查实现情况。

页面仅选择源码、不上传文档时使用代码模式。已有需求可通过“检查已有需求”继续执行实现检查。API 的 `implementation_only` 接受 `baseline_run_id` 与 `repository_id`；沿用原需求语言，记录新旧关联，保留历史结果。

## 预算、返回与恢复

当前暂时关闭 API 调用次数、累计 token 和单任务模型调用轮数上限，默认 `AUDITOR_ENFORCE_BUDGETS=false`。此开关同时应用于分析服务、出站网关、ADK 和 Mantis 适配层；历史运行已保存的预算也不再阻断执行，原预算与累计用量保留。界面隐藏额度设置，新分析一律不限额，API 传入的旧额度也不生效。

恢复限制时，在分析服务和网关同时设置 `AUDITOR_ENFORCE_BUDGETS=true`，再配置 `AUDITOR_MAX_REQUESTS`、`AUDITOR_MAX_TOKENS` 和 `AUDITOR_AGENT_MAX_CALLS`（默认均为 `0`，表示不限额）。此时页面可调整运行额度，API 的 `budget.max_requests` 和 `budget.max_tokens` 为 `null` 表示不限额。单次回答长度、上下文容量、并发、超时和故障重试限制继续保留；它们不属于累计消费上限。

每次实际出站请求都原子预留并记账，分别显示已知供应商用量、在途预留与未知用量估算；估算不等于账单。连接、限速和临时服务错误采用有界重试与等待恢复；认证、余额、参数及结果契约问题保留明确错误。用户暂停停止自动唤醒。

自有角色使用严格函数参数协议，返回先归档、再做 Schema、任务归属、来源和验收项库存校验，成功后事务入库。程序不补写分析字段，不凭散文推断结果，不调用额外模型解释或修补最终返回。固定的任务关联由程序写入；模型回传冲突关联时拒绝提交。

安全判断只能引用当次会话实际读取的材料；搜索结果、摘要和历史读取不代替当次源码读取。支持实现、部分实现和违反需求的结论必须具备实际源码引用。Mantis 原生分析保留其调查循环和结构化结论，不恢复动态复现或目标执行权限。

结果支持 HTML、CSV、JSON 导出。报告保留需求对应关系、来源、源码位置和已排除候选。部署并不保证模型输出始终有效；遇到最终结构或来源校验失败时任务保持未完成，需要处理阻断原因后继续。

## 本地开发与验证

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip install -e '.[dev]'
cd apps/web
pnpm install --frozen-lockfile
pnpm run build
cd ../..
.venv/bin/pytest -q
.venv/bin/ruff check src tests --exclude vendor
.venv/bin/auditor doctor
```

本地启动不自动读取 `.env`，需将配置提供给对应进程。仅网关进程设置 `DEEPSEEK_API_KEY`，并通过 `AUDITOR_GATEWAY_DATABASE` 指向与分析服务相同的数据库：

```sh
AUDITOR_GATEWAY_DATABASE=./state/auditor.sqlite3 .venv/bin/auditor gateway
```

另一终端登记仓库、解析模型和标准包，再启动分析服务：

```sh
AUDITOR_REPOSITORIES='{"project":"/absolute/path/to/project"}' \
AUDITOR_DOCLING_MODELS=./models/docling \
.venv/bin/auditor serve --port 8088
```

PDF 测试需要预置模型，缺少资源时明确跳过。协议测试使用受控模型响应，不代表真实分析准确率。首次完整真实链路经过调试续跑完成，尚未验收完全无人干预稳定性及 Findings 有效性；其他限制见 [当前状态](docs/development-status.md)。

## 项目结构与来源

```text
apps/web/                  前端
src/security_auditor/      后端、数据库、模型网关与适配层
src/security_auditor/vendor/mantis/  固定版本 Mantis 核心及技能
skills/                    自有版本化分析技能
design/                    工作流和配置示例
deploy/                    容器与通用部署模板
tests/                     自动测试与最小测试夹具
docs/                      架构、解析、静态复用与状态说明
licenses/                  第三方许可证
```

Mantis 来源为 [google/mantis](https://github.com/google/mantis)，固定提交及文件摘要保存在 vendored `SOURCE.json`；保留 Apache-2.0 许可证与 [第三方声明](THIRD_PARTY_NOTICES)。内部结构名 `security-design-auditor` 用于安装包及 Compose 项目。

详细设计见 [实现方案](docs/implementation-plan.md)、[前后端与部署](docs/frontend-backend-deployment.md)、[文档解析选型](docs/document-parsing-selection.md)、[Mantis 技能核对](docs/mantis-skills-review.md)、[静态审计复用](docs/mantis-static-parity.md)。设计文档中的目标能力以当前状态和实际实现为准。
