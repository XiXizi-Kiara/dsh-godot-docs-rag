# Godot 官方文档 RAG

把 **Godot 4.x stable 官方文档**（`godot-docs-html-stable/`，1596 篇 / 1597 个 RST 源 / 31.7 MB）
做成一个可检索、可引用、可被 agent 调用的本地 RAG 系统。

- **全站收录**：classes API 参考 + tutorials + getting_started + engine_details + about + community
- **三路检索**：BGE 稠密向量 + 自研 BM25 + RRF 融合，可选 cross-encoder 精排
- **精确 API 查询**：class 参考页按 method / property / signal / constant / enum 切成符号块，
  查询 `queue_free`、`move_and_slide` 能直接命中对应方法
- **中文可用**：内置 268 条 Godot 领域术语映射，中文提问也能命中英文文档
- **三种形态**：Python 库、CLI（`--json` 供 agent 解析）、零依赖 HTTP 服务 + 中文单页 UI
- **CPU/GPU 双支持**：GPU 可用时全量嵌入约 90 秒；纯 CPU 也能构建与查询
- **可选 LLM 生成**：无 key 时返回检索式回答 + 引用；配置 DeepSeek/OpenAI 兼容 key 后生成自然语言答案

## 快速开始

环境：conda env `godotrag`（见 [docs/ENV.md](docs/ENV.md)），统一使用其解释器：

```powershell
$PY = "C:\Users\Kiara\.conda\envs\godotrag\python.exe"
cd C:\Users\Kiara\Desktop\GodotRag
```

### 1. 准备模型（首次，约 140 MB）

```powershell
& $PY scripts\fetch_models.py            # bge-small-en-v1.5
& $PY scripts\fetch_models.py --rerank   # 可选：bge-reranker-base（精排，约 1.1 GB）
```

### 2. 构建索引（全量约 90 秒 GPU / 数分钟 CPU）

```powershell
& $PY scripts\run.py build               # -> data/index/（26076 chunks）
& $PY scripts\run.py build --limit 200   # 试跑 -> data/index_smoke/
```

### 3. 检索 / 问答

```powershell
& $PY scripts\run.py search "how to free a node" --k 5
& $PY scripts\run.py search "queue_free 和 free 有什么区别" --json
& $PY scripts\run.py ask "CharacterBody2D 为什么不动？" --json
```

`ask` 在未配置 LLM key 时返回**检索式回答**（引用官方原文，不编造）；配置后走生成：

```powershell
$env:GODOT_RAG_LLM_API_KEY = "sk-..."
# 可选：$env:GODOT_RAG_LLM_BASE_URL / $env:GODOT_RAG_LLM_MODEL
```

### 4. Web UI

```powershell
& $PY scripts\run.py serve --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

搜索框、模式切换（hybrid/dense/bm25/hybrid+rerank）、结果卡片、来源链接、展开整页原文。

## 评测结果（40 条人工 QA，`data/reports/eval.md`）

| 配置 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 | 平均延迟 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **hybrid（默认）** | 0.575 | **0.850** | 0.950 | 0.694 | **0.950** | 182 ms |
| dense | 0.500 | 0.850 | 0.925 | 0.645 | 0.925 | 42 ms |
| bm25 | 0.475 | 0.700 | 0.900 | 0.590 | 0.950 | 46 ms |
| hybrid+rerank | **0.650** | 0.800 | 0.900 | **0.728** | **0.975** | 28 s（CPU） |

- 中文 12 条：hybrid Recall@5 = 0.833；英文 28 条：0.857
- 分类：class 0.90 / troubleshooting 1.00 / getting_started 1.00 / tutorial 0.75 / engine_details 0.50
- `hybrid+rerank` 在 CPU 上单查询约 28 秒（bge-reranker-base 推理），**Web 交互建议用默认
  hybrid**；它把 Recall@1 / MRR / PageHit@5 推高，但 Recall@5 略降（部分金标页被挤出前 5）。
  GPU 环境下重排会快一个数量级，可作为高质量离线模式使用。
- 复跑：`& $PY scripts\run.py eval --configs hybrid,dense,bm25,hybrid+rerank --json`

## 架构

```
_sources/ 1597 RST
      │  parse_rst.py      标题树/代码块/提示块/内联角色清洗/符号识别
      ▼
 documents.jsonl（1596 篇）
      │  chunk.py          class 按符号切；教程按 section 切（200–450 tok，带面包屑前缀）
      ▼
 26076 chunks
      ├─ embed.py      BGE-small-en-v1.5 → embeddings.npy（384 维，float32）
      ├─ bm25.py       自研倒排 BM25（k1=1.5, b=0.75，保留 snake_case API 名）
      ▼
 retrieve.py   dense top-100 + bm25 top-100 → RRF(k=60) → [bge-reranker top-50] → top-k
      ▼
 CLI / Python 库 / HTTP 服务（service.py + 内嵌 UI） → generate.py（检索式 / LLM）
```

- **分块**：class 页每个 method/property/signal/constant/enum 独立成块，块前缀如
  `Godot Docs > Class reference > Node > Method Descriptions` + `Node.add_child — method`；
  教程按 section 聚合，超长按段落窗口切分。
- **中文**：`zh_dict.py` 最长匹配翻译 Godot 术语，dense 用英文术语、BM25 用英文术语加权 + 原 query。
- **双后端**：`embed.py` 在 `auto` 模式下优先用本进程 CUDA；本进程无 CUDA 时自动把批量嵌入
  委托给带 CUDA 的 Python 解释器（`GODOT_RAG_GPU_PYTHON`，默认探测 `~/.conda/envs/pytorch`）；
  都没有则用 CPU。查询编码始终走本进程 CPU（几十毫秒）。

## Python 库

```python
from godot_rag import GodotRAG

rag = GodotRAG("data/index")
for r in rag.search("how to add a child node", k=5, mode="hybrid"):
    print(r["rank"], r["title"], r["symbol"], r["url"])
    print(r["text"][:200])

answer = rag.ask("queue_free 和 free 的区别", k=6)
print(answer["answer"], answer["citations"])
```

## HTTP API

```
GET  /                中文 Web UI
GET  /api/health      {"ok":true,"ready":true,"n_chunks":26076}
GET  /api/stats
POST /api/search      {"query":"...","k":8,"mode":"hybrid"}
POST /api/ask         {"query":"...","k":8,"mode":"hybrid"}
GET  /api/doc?doc_id=classes/class_node
```

## 目录

```
src/godot_rag/     parse_rst / chunk / embed / bm25 / index / retrieve / generate / service / cli / eval
scripts/           run.py（统一入口）、run_tests.py、fetch_models.py、embed_worker.py、validate_qa.py
data/processed/    documents.jsonl、chunks.jsonl、qa.jsonl
data/index/        meta.json、chunks.pkl、embeddings.npy、bm25.pkl、docs.pkl
data/reports/      eval.md / eval.json
docs/              ENV.md（环境）、INTERFACES.md（模块接口）、USAGE.md（使用手册）、设计稿
tests/             110 个 unittest（解析/分块/BM25/检索/CLI/服务/生成/评测）
```

## 测试

```powershell
& $PY scripts\run_tests.py -v     # 110 tests，不依赖 GPU / 全量索引 / 网络
```

## 已知限制

- 检索式回答（无 LLM key）只拼接官方原文要点，不生成连贯长答；要自然语言回答请配置 key 或本地接入。
- 中文概念查询依赖术语词典，未覆盖的长尾中文表述召回有限；agent 场景建议直接用英文 query。
- 图片/视频内容不进入索引（只收录文本）。
- 本快照为 stable 版；文档更新后需重新 `build`。
