# 文档解析开源选型

文档解析主候选采用 Docling，中文 OCR 优先验证其 RapidOCR 接入。MinerU 和 Unstructured 保留为对照候选，PaddleOCR 可用于 OCR 或复杂 PDF 增强。选择依据是多格式结构化输出、来源定位、离线运行与 Python 集成，不以尚未执行的质量排名决定最终版本。

本项目只编写调用适配器、来源映射、版本和质量管理。Office 格式读取、PDF 布局、表格识别和 OCR 直接使用开源实现，不自研解析引擎。

## 1 候选项目

| 项目 | 官方能力与接入方式 | 对本项目的选择 |
| --- | --- | --- |
| [Docling](https://github.com/docling-project/docling) | PDF、DOCX、PPTX 等统一为结构化文档，提供本地解析及多种 OCR 接入 | 主候选，优先验证三类材料和来源信息的一致性 |
| [MinerU](https://github.com/opendatalab/MinerU) | 当前官方项目列出了 PDF、图片及 Office 格式，提供本地运行与不同计算档 | 对照复杂中文 PDF 和表格；按实测质量、资源和固定版本许可证决定是否接入 |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | 本地分区函数支持 PDF、Word、PowerPoint，可按格式返回文档元素 | 对照多格式提取和 Office 特殊对象；不采用云端平台或远程分区接口 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 图片及 PDF 的 OCR、结构化文档解析，含多个本地模型方案 | OCR 和复杂文档的候选增强，不作为三类原生 Office 输入的统一入口 |

以上能力来自项目官方说明，尚未在本项目中安装或跑样例。MinerU 当前许可证含 Apache 2.0 之外的附加条件，不能直接按普通 Apache 2.0 描述。[MinerU 许可证](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)

Docling 代码许可证为 MIT，所用模型的许可证需分别记录。版本冻结同时固定代码、依赖、模型和资源的来源与摘要。[Docling 项目说明](https://github.com/docling-project/docling)

## 2 选择 Docling 的依据

它提供统一的 `DoclingDocument` 数据结构，可表达文字、表格、图片、层级、布局和来源信息。本项目可以将这些对象映射到证据库，而不是先压平成文本再重新猜结构。具体格式是否给出坐标和定位，以解析结果为准。[文档数据结构](https://docling-project.github.io/docling/concepts/docling_document/)

官方格式清单覆盖 PDF、DOCX、PPTX，旧 DOC 和 PPT 需要 LibreOffice。离线 FAQ 明确说明可在隔离环境运行，前提是提前准备模型并指定本地位置。[格式清单](https://docling-project.github.io/docling/usage/supported_formats/)、[离线说明](https://docling-project.github.io/docling/faq/)

Docling 已提供设置 RapidOCR 本地检测、识别和方向模型路径的例子。因此中文 OCR 先使用现有集成做对照测试，不自己编写识别算法。中英文效果仍以项目材料测试为准。[本地 OCR 模型示例](https://github.com/docling-project/docling/blob/main/docs/examples/rapidocr_with_custom_models.py)

Unstructured 的开源本地分区函数也是可行方案；当前选择 Docling 是基于本项目对结构与来源关系的需求，并不表示 Unstructured 的实测效果较差。[Unstructured 本地分区接口](https://docs.unstructured.io/open-source/core-functionality/partitioning)

## 3 调用边界

```text
已登记本地材料
  → Docling 原生转换或现有本地 PDF 与图片流水线
  → 保存完整 Docling JSON、图片、表格和定位信息
  → 本项目适配器映射 DocumentBlock 与 Evidence
  → 覆盖及引用校验
  → 后续需求分析 Agent
```

适配器只允许本地登记的文件引用，不接收 HTTP URL。解析库即使支持远程输入也不向 Agent 暴露。解析不通过模型网关，不持有 DeepSeek 密钥；需求推理后续由 ADK 角色调用网关。

统一适配器契约：

```text
DocumentParser.parse(asset_ref, local_profile) -> ParseArtifact
EvidenceMapper.map(parse_artifact, source_asset) -> DocumentCorpus
ExtractionValidator.check(document_corpus, extraction_inventory) -> ExtractionQuality
```

`ParseArtifact` 至少包含输入摘要、引擎与版本、模型与配置摘要、解析状态、原生结果引用、派生材料关系、警告与失败单元。缓存键包含这些版本与摘要，升级解析器或更换 OCR 模型会使相关缓存失效。

PDF 页码和坐标、Office 元素定位、正文与备注关系都来自现有开源接口的实际结果。能力不足时首先保留缺口并用其他开源后端对照，不自行开发整套格式后端。

## 4 特殊材料的处理

| 材料 | 处理策略 |
| --- | --- |
| 原生 DOCX、PPTX | 先保留原生层级和语义；正文、备注、批注、表格和图片分别记录提取状态 |
| 原生 PDF | 使用本地版面、表格与 OCR 流水线，保留页码和坐标 |
| 扫描 PDF | 使用预置 OCR 模型，单列识别质量和不可读区域 |
| Office 内嵌图片 | 显式通过现有图片流水线，或 LibreOffice 导出 PDF 再解析；保留原件与派生件关系 |
| 复杂表格 | 保存原始表格及结构；跨页合并不确定时保留候选关系，不靠模型猜测补齐 |
| 架构图 | 保存原图、OCR 文本和已有后端关系；未识别箭头与边界保持未知 |
| 旧 DOC、PPT | 使用开源转换支持及 LibreOffice；转换不可验证的位置标为派生件位置 |

不能把支持 PPTX 或 DOCX 理解为所有图片、备注和图形已完整解析。Docling FAQ 记录了部分平台下嵌入 WMF 图片的处理限制，这类对象进入样例验收。[官方格式限制说明](https://docling-project.github.io/docling/faq/)

Office 转 PDF 的结果用于补充视觉内容，不取代原生结构和备注；不同解析结果冲突时保留冲突。原生解析与图片 OCR 的覆盖分别记录，单次成功返回不代表全材料完整。

OCR 识别文字不能直接证明架构图的连接语义。后续需要视觉增强时，也优先评估现成开源视觉解析方案，再纳入统一适配器；没有验证过的图关系不能写成系统事实。

## 5 离线与容器配置

使用现有本地解析流水线，优先验收 CPU 档；实际内存、吞吐量和镜像大小通过样例测量，必要时提供独立 GPU 档。第一版不要求 GPU 才能开始工作。

准备阶段打包 Docling 布局与表格模型、OCR 权重、字体、模型运行依赖、LibreOffice 和版本清单，放进镜像或只读模型目录。设置显式本地模型位置，禁止远程解析服务与运行时资源下载；模型或资源缺失应返回不可用状态。

Docling 初次初始化、首次 PDF 解析、OCR 和文档转换全部在无外网进程中验收。不得靠事先跑过一次、缓存碰巧齐全证明离线能力，必须使用交付包建立干净环境复测。

现有双容器方案中，Docling 在分析服务的无网络处理进程运行。引擎依赖与 ADK 的组合在兼容性试验中检查；若以后吞吐量或依赖隔离要求提升，再通过同一适配接口拆成专用解析容器。

## 6 样例验收与版本冻结

对主候选与对照候选使用相同材料，至少覆盖中文和英文技术文档、扫描件、双栏 PDF、跨页表格、架构图、Word 内嵌图片、PPT 分组图形和备注。构造样例立即可用，真实代表材料可用后加入同一评估集。

评估对象为可追溯的内容单元，不能只比较生成的 Markdown 是否看起来顺畅：

- 正文阅读顺序、标题层级、表格单元格与条件注释是否保留。
- “必须”“不得”、否定词、范围条件和关键数值是否丢失或识别错误。
- 页面、幻灯片和元素来源能否定位，引用能否回到原件或明确派生件。
- 图片、备注和不可解析对象是否有明确处理记录。
- 中文 OCR 质量、跨页表格处理、耗时、峰值内存、模型资源和镜像体积。
- 干净离线环境是否首次成功运行，失败能否返回结构化原因。

选择门槛为所有输出引用真实、不可解析对象不静默消失、无未经许可的外连；标准测试样例中的安全条件与数值不得静默错漏。达不到这些门槛时先尝试更合适的现有后端或 OCR 档，再重新测试。

样例通过后固定引擎、模型、资源、依赖和配置。多个引擎不默认对每份材料全部运行；主后端正常时使用主后端，指定类型或质量不足时才进入对照或补充流程，并保留采用原因。
