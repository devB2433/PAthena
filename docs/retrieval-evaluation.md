# 中英文标准检索选型 / Bilingual standard retrieval

生产方案：Qwen3-Embedding-0.6B → 中英文控制与规范原文向量 + BM25 → RRF（k=60）→ 前 40 项 BGE-reranker-v2-m3 重排。模型在本地 CPU 运行，项目阶段不生成控制、不重新计算标准向量。选型测试在独立目录完成后才移植到应用。

Production: Qwen3-Embedding-0.6B, persisted bilingual-control and normative-source vectors, BM25, RRF (k=60), then BGE-reranker-v2-m3 over the first 40 candidates. Models run locally on CPU. Projects never regenerate controls or standard vectors. Comparison was completed in a separate workspace before migration.

## 比较结果 / Comparison

固定 PCI DSS v4.0.1 控制库 290 项。108 条查询包含 100 条有标注目标的中英文查询、8 条无关查询。同一场景的两种语言保持同一划分：开发集 26 条正例，原测试集 74 条正例。

The fixed PCI DSS v4.0.1 catalog contains 290 controls. The 108 queries comprise 100 labeled bilingual positive queries and 8 unrelated queries. Language pairs share their split: 26 development positives and 74 original test positives.

| 配置 / Pipeline | 原测试集 Recall@10 | 原测试集 Recall@30 | MRR |
| --- | ---: | ---: | ---: |
| E5-small，原英文控制索引 / original English-control index | 97.3% | 100% | 0.874 |
| Qwen embedding + 双语混合召回 / bilingual hybrid | 97.3% | 100% | 0.924 |
| Qwen embedding + 混合召回 + BGE rerank | 100% | 100% | 0.950 |
| Qwen embedding + 混合召回 + Qwen rerank | 98.6% | 98.6% | 0.949 |

按预先固定的开发集规则，Qwen 重排的 MRR 略高；但它在原测试集把一项中文目标排到前 30 项之外。工程选型采用原测试集召回更稳定、耗时更低的 BGE。因此原测试集参与了工程选型，不作为独立验收。选定方案后，另固定了 10 条此前未测过的控制查询：全部目标排名第一。移植后再用应用的真实 CPU 模型和数据库执行这 10 条查询，验证目标命中及跨项目、分页缓存。

Qwen reranking narrowly won the predefined development-set MRR rule, but placed one Chinese target outside the first 30 on the original test set. BGE was selected for observed recall and lower latency. The original test set therefore informed engineering selection and is not independent acceptance. After selection, 10 queries targeting previously untested controls were fixed; all expected targets ranked first. The port was then tested with the application's real CPU adapters and database, including cross-project and pagination caching.

## 性能与边界 / Performance and limits

本机为 Apple arm64、16 GiB 内存，CPU FP32、4 线程、批量 8。Qwen 查询向量 P50 约 0.09 秒。预先固定的 8 条代表查询中，40 项 BGE 重排 CPU P50 4.15 秒、P95 4.71 秒；Qwen 重排 P50 7.82 秒、P95 8.78 秒。全量准确度测试使用 Apple GPU FP32；两个重排模型共 16 条 CPU/GPU 对照中首位及标注目标位置一致。CPU 延迟只计重排，不包含向量生成、数据库读取或首次加载。

The host is Apple arm64 with 16 GiB RAM. CPU inference uses FP32, four threads, and batches of eight. Qwen query embedding P50 was approximately 0.09 s. On eight predefined representative queries, reranking 40 candidates took BGE CPU P50 4.15 s / P95 4.71 s and Qwen P50 7.82 s / P95 8.78 s. Full accuracy runs used Apple GPU FP32; top-one and labeled-target positions agreed in 16 CPU/GPU comparisons across both rerankers. CPU latency includes only reranking, excluding embedding, database access, and first load.

部署后另在本机 Docker 容器的临时数据库中验证了两条中英文查询，目标均排名第一。端到端未缓存查询分别为 31.46 秒（含首次模型加载）与 21.03 秒；重复查询为 0.40 秒与 0.29 秒。这是两条部署冒烟测试的实测值，不能用作 P50/P95，也不能把宿主机重排计时当作容器响应时间。未创建生产测试项目。

Two further bilingual smoke queries ran in a temporary database inside the deployed local Docker container; both targets ranked first. End-to-end uncached times were 31.46 s (including first model load) and 21.03 s; cached repeats took 0.40 s and 0.29 s. These two observations are not P50/P95 estimates. Host reranking timings should not be presented as container response times. No production test projects were created.

这些查询由本次任务依据规范来源整理，尚未经独立专家复核，不能代表全标准准确度。预期目标是必须召回的控制，未标注候选不直接视为误报。8 条无关查询不足以校准适用性阈值。测试只覆盖检索，不证明 mapper 最终判断或整条分析流程的准确度。全部本地执行，未调用付费 API。

Cases were authored for this task from normative sources and have not undergone independent expert review. Results do not establish whole-standard accuracy. Labels specify required targets, not exhaustive relevance; unlabeled candidates are not automatically false positives. Eight unrelated queries are insufficient to calibrate applicability thresholds. Retrieval tests do not establish mapper or end-to-end reasoning accuracy. All tests ran locally without paid API calls.

## 固定模型版本 / Pinned revisions

| 模型 / Model | Revision |
| --- | --- |
| [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| [BGE-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` |

模型文件摘要、推理协议、标准与控制摘要、双语索引和检索策略固定到目录身份；更新需要独立准备新目录。查询和排序缓存也绑定该目录身份。模型权重、标准原件和逐题测试数据不随仓库分发。

Model-file hashes, inference protocol, source/control hashes, bilingual inventory, and retrieval recipe are bound to the catalog identity. Updates require explicit preparation of a new catalog. Query and ranking caches are version-bound. Weights, standard originals, and per-query test data are not distributed with the repository.
