# 前后端与容器部署方案

本项目采用 Python 后端和 TypeScript 前端。后端承担文档处理、Agent 调度、需求与代码分析、领域判定及报告；前端承担材料管理、运行控制、结果筛选和证据查看。浏览器界面进入 MVP，命令行作为同一系统的辅助入口。

前后端分别开发，生产部署时把前端静态产物放进分析服务镜像。独立模型网关持有 DeepSeek 凭据并控制模型出站，总体使用两个容器。

本文件描述目标方案。当前可运行版本的范围见 [开发状态](development-status.md)。当前 LiteLLM 位于分析服务的 ADK 适配层；网关只做固定供应商转发与参数兼容。浏览器入口通过网关反向代理到分析服务，使分析容器始终只连接内部网络；页面和 API 内容仍由 FastAPI 提供。扫描页 OCR、旧 Office 转换、增量模式等目标能力尚未开放。

## 1 开发语言与技术栈

| 部分 | 选型 | 用途与理由 |
| --- | --- | --- |
| 后端语言 | Python 3.12 | 与 Mantis 的 Python 实现和 ADK 生态直接衔接，统一文档解析与分析服务 |
| HTTP API | FastAPI、Uvicorn | 提供接口、请求校验与 OpenAPI 文档，接入应用服务 |
| 数据契约 | Pydantic | 校验 Agent 输出和接口数据；领域模型是权威定义 |
| Agent 运行 | Google ADK | 角色、工具、会话、流程、事件与运行状态 |
| 模型适配 | LiteLLM，封装在本项目网关内 | 转换模型协议及参数；默认接 DeepSeek |
| 文档解析 | Docling 主候选，集成本地 OCR 与 LibreOffice | 复用完整开源解析流水线，本项目仅负责适配与证据映射 |
| 数据库 | SQLite、WAL、FTS5 | 保存项目、证据、需求、任务和本地检索，第一版无需独立数据库服务 |
| 前端语言 | TypeScript | 对需求、检查状态、运行事件和证据结构做类型约束 |
| 前端界面 | React | 构建项目、矩阵、详情和运行视图 |
| 前端构建 | Vite | 开发服务和生产静态产物构建 |
| 界面组件 | Ant Design，加项目自己的布局与样式 | 表格、筛选、上传、分页、抽屉和状态组件 |
| 前后端通信 | HTTP JSON、SSE | 普通操作通过 REST；阶段与任务进度通过事件流 |
| 容器 | Docker、Compose | 统一依赖与运行环境，支持本地部署和离线镜像导入 |

React 有官方 TypeScript 指南，Vite 用作前端构建。FastAPI 提供 OpenAPI 契约和静态资源能力；Ant Design 表格支持筛选、排序和分页。[React TypeScript](https://react.dev/learn/typescript)、[Vite](https://vite.dev/guide/)、[FastAPI](https://fastapi.tiangolo.com/features/)、[静态资源](https://fastapi.tiangolo.com/tutorial/static-files/)、[Ant Design 表格](https://ant.design/components/table/)

具体依赖版本在 P0 联合验证后锁定。构建使用固定依赖和基础镜像摘要；部署运行不下载 npm 包、Python 包、OCR 资源或字体。

## 2 产品的四个阶段

项目主界面固定为四个阶段，依次对应四个输出物：

| 阶段 | 输出物 | 页面内容 |
| --- | --- | --- |
| 1 安全需求 | 安全需求清单 | 需求内容、模块、来源；详情显示文档片段或 PCI DSS 条款及验收要求 |
| 2 威胁建模 | 威胁模型 | 威胁、模块、攻击者、入口、信任边界、攻击前提与影响 |
| 3 需求实现情况 | 实现检查表 | 每项需求的验收条件、实现状态与检查结果 |
| 4 Findings | Findings 列表 | Findings、模块、风险、复核状态；详情显示影响、原因、修复建议、文件与代码片段 |

上传文档、选择仓库、开始或暂停分析、历史分析选择和报告导出属于项目操作，不占用输出物页面。没有独立“支持证据”“证据材料”或“分层任务”页面，不向用户展示技能版本、模型调用次数和内部复核规则。用户界面只显示完成任务所需的短标签。系统不提供演示入口或预置项目，首次启动为空白。

需求清单与需求实现情况分开，威胁模型与漏洞分开。需求来源使用文档名、页或幻灯片位置、条款号与相关原文。漏洞定位到源码文件、行范围与代码片段；无法定位时明确显示未定位到代码。内部证据库、角色权限与校验仍用于保证来源可靠。

HTML 报告按同样的四个输出物组织。阶段导航用于查看输出，不改变底层角色的依赖顺序；主控、阶段任务、专项任务和两波研究作为运行实现保留。

## 3 后端服务边界

```text
浏览器 React 界面
    ↓ HTTP API 与 SSE
FastAPI 接口层
    ↓ 类型化请求
应用服务
    ↓ 任务与领域操作
ADK 运行层 ＋ 文档处理 ＋ 代码索引 ＋ 领域判定
    ↓                         ↓
本地证据与状态                本地模型网关
                                ↓ 唯一外网出口
                              DeepSeek
```

FastAPI 只做身份与项目边界、参数校验、调用应用服务、分页和数据返回。Agent 调度、需求判定和报告逻辑放在应用与领域模块，命令行入口复用同一套实现。

API 收到启动请求后先将运行和待执行任务写入数据库，再返回 `202 Accepted` 和运行 ID。长任务由持久化任务调度器执行，不依赖请求对象存活，也不把内存中的 FastAPI 后台回调当成可恢复任务队列。

MVP 以一个 Uvicorn 工作进程承载 API 与异步任务调度，解析、转换与索引的阻塞工作放到隔离子进程。启动时检查中断任务、租约和检查点；只有有效输入与剩余预算允许时才恢复执行，其他情况显示可恢复或阻断状态。后续要扩展为多工作进程时，先实现任务抢占与租约去重，不能直接增加进程数。

## 4 主要接口契约

统一前缀为 `/api/v1`。所有资源带项目和快照关系；客户端传资源 ID，不传任意宿主机路径。代码目录由部署配置登记，Web 界面选择仓库资源引用。

| 方法与路径 | 用途 |
| --- | --- |
| `POST /projects` | 创建项目 |
| `GET /projects` | 项目列表 |
| `POST /projects/{project_id}/documents` | 上传文档并登记材料；异步提取 |
| `GET /projects/{project_id}/assets` | 材料、仓库引用、版本与处理状态 |
| `POST /projects/{project_id}/snapshots` | 固定一次分析输入 |
| `POST /projects/{project_id}/runs` | 创建完整、增量或仅需求运行，返回运行 ID |
| `GET /projects/{project_id}/runs` | 当前项目的运行列表 |
| `GET /projects/{project_id}/runs/{run_id}` | 获取状态、消耗、范围和任务计数 |
| `GET /projects/{project_id}/runs/{run_id}/events` | SSE 进度与结果更新事件 |
| `POST /projects/{project_id}/runs/{run_id}/pause` | 在安全检查点暂停 |
| `POST /projects/{project_id}/runs/{run_id}/resume` | 校验快照与预算后恢复 |
| `GET /projects/{project_id}/runs/{run_id}/requirements` | 需求与检查矩阵，分页和筛选 |
| `GET /projects/{project_id}/runs/{run_id}/threats` | 威胁模型 |
| `GET /projects/{project_id}/runs/{run_id}/findings` | 缺陷、证据缺口和复核结果 |
| `GET /projects/{project_id}/evidence/{evidence_id}` | 版本化证据详情 |
| `GET /projects/{project_id}/runs/{run_id}/reports/{format}` | 下载已生成报告 |

创建运行、恢复和上传登记支持幂等键，避免重复点击产生重复工作。列表接口采用受控筛选字段、页大小上限和稳定排序。API 响应有 `schema_version`，错误有稳定代码、描述和请求 ID，不返回密钥或原始内部异常。

SSE 使用持久化事件 ID，浏览器断线后从最后事件继续获取；过期事件回放窗口之外则重新读取运行快照。事件只通知阶段与结果变化，权威状态仍从领域库查询。浏览器关闭不会取消分析任务。

Pydantic 模型生成 OpenAPI，再生成或校验前端 TypeScript 客户端与类型。领域状态枚举不在前后端各自手工维护。构建契约检查覆盖新增字段、状态与接口变更。

## 5 容器划分

| 容器 | 内含内容 | 挂载和网络 |
| --- | --- | --- |
| `analysis-service` | FastAPI、ADK、分析业务、React 静态资源、Docling 无网络处理进程、转换与 OCR、SQLite 访问 | 仓库与标准包只读；解析与 OCR 模型预置或只读挂载；项目状态和上传对象可写；只接内部网络 |
| `model-gateway` | Python 模型网关、LiteLLM、协议兼容性与用量记录 | 持有 DeepSeek 密钥；接受内部请求；经受控出口仅访问 DeepSeek |

前端构建阶段使用 Node.js，将 `apps/web/dist` 放到分析镜像的静态资源目录。生产环境由 FastAPI 同源提供 `/`、本地资源和 `/api/v1`；不运行 Vite 开发服务器，也不需要独立 Node.js 服务。API 路径不落入单页应用的页面回退规则。

Compose 将分析服务接到内部网络，网关同时接内部网络与受控出站网络。网关拥有外网接口并不自动等于仅能访问 DeepSeek，域名出口策略仍由代理或宿主机规则落实。当前浏览器入口为网关的页面代理，浏览器不持有 DeepSeek 密钥；模型路由只接受分析容器调用。

目录安排：

```text
/inputs/repositories       已登记代码仓库，只读
/inputs/configuration      可选配置材料，只读
/standards                 PCI DSS 本地标准包，只读
/models                    Docling 与 OCR 预置模型，只读
/data/projects             SQLite、上传原件、对象库、快照、检查点
/data/reports              导出报告
/run/secrets               模型网关凭据挂载，仅网关可读
```

上传的原件进入对象库后按内容哈希固定，转换件另存，不覆盖原件。容器重建或升级使用原有数据卷；升级前备份、迁移版本记录和恢复验证进入部署流程。

默认桌面部署只映射宿主机回环地址的 Web 端口。团队部署开放访问时配置统一认证和 TLS 接入，项目访问检查仍由 API 执行。模型密钥通过容器 Secret 或部署环境注入，不写进镜像、前端资源或项目文档。

离线交付包包含两个镜像、Compose 文件、配置模板、标准包导入说明、镜像摘要和部署操作说明。目标 CPU 架构分别构建并验收；开发可在 macOS，正式镜像采用 Linux。MVP 不挂载 Docker Socket，也不执行目标程序。

解析模型、OCR 权重和对应资源带入镜像或离线模型包，固定版本与摘要。第一次启动也必须在解析进程无外网条件下成功，不能把运行时自动下载当作初始化步骤。文档解析选型与验收在 [文档解析开源选型](document-parsing-selection.md) 中定义。

## 6 页面与部署验收

前端能上传材料、创建运行、显示进度、打开需求及代码证据、筛选结果和导出报告。刷新、关闭浏览器或事件流重连不导致任务丢失，重复操作不启动重复运行。

同一验收项的设计覆盖与代码状态必须分别显示；未解析图像、未知适用性、外部证据需求和执行失败不能被隐藏在通过率中。确认结论能跳转到对应快照的真实来源。

受限网络测试同时覆盖后端与浏览器：前端资源无 CDN 请求，分析容器无远程连接，模型网关仅连接允许的 DeepSeek 端点。全程使用已导入镜像和本地标准包，容器重启后数据与累计预算保留。
