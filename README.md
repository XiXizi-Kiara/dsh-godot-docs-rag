# Godot 官方文档 RAG

把 **Godot 4.x stable 官方文档**（`godot-docs-html-stable/`，1596 篇 / 1597 个 RST 源 / 31.7 MB）
做成一个可检索、可引用、可被 agent 调用的本地 RAG 系统。

- **全站收录**：classes API 参考 + tutorials + getting_started + engine_details + about + community
- **混合检索**：sentence-transformers（BGE-small）+ faiss 向量索引 + rank_bm25 词法检索
  + 加权 RRF 融合 + bge-reranker cross-encoder 精排
- **精确 API 查询**：class 参考页按 method / property / signal / constant / enum 切成符号块，
  查询 `queue_free`、`move_and_slide` 能直接命中对应方法
- **中文可用**：268 条 Godot 领域术语映射 + jieba 分词补充，中文提问也能命中英文文档
- **GPU 加速**：godotrag 环境已装 CUDA 版 torch（RTX 4060），全量嵌入约 100 秒、单次精排约 0.3 秒；
  无 GPU 时自动回退 CPU
- **五种形态**：Python 库、CLI（`--json` 供 agent 解析）、FastAPI 服务 + 中文单页 UI
  （保留零依赖标准库服务作为 `--backend stdlib`）、**MCP 服务**（stdio / streamable-http）、
  **DSH 原生插件**（4 个 `godot_*` 工具 + 默认关闭的使用日志行）
- **可选 LLM 生成**：openai SDK 调 DeepSeek/OpenAI 兼容接口；无 key 时返回检索式回答 + 引用

## 环境与依赖

统一使用 conda 环境 `godotrag`（Python 3.12）：

```powershell
$PY = "$HOME\.conda\envs\godotrag\python.exe"
cd <PROJECT_DIR>
```

依赖已安装完毕（清单见 [requirements.txt](requirements.txt)、说明见 [docs/ENV.md](docs/ENV.md)）：
`sentence-transformers 6.2.0`、`faiss-cpu 1.15.1`、`rank_bm25 0.2.2`、`jieba 0.42.1`、
`fastapi 0.159.1`、`uvicorn`、`openai 3.36.1`、`tiktoken 0.14.0`、`torch 2.11.0+cu128`。

## 快速开始

### 1. 准备模型（首次）

```powershell
& $PY scripts\fetch_models.py            # bge-small-en-v1.5
& $PY scripts\fetch_models.py --rerank   # bge-reranker-base（精排，1.1 GB）
```

### 2. 构建索引（GPU 约 100 秒 / CPU 约 5 分钟）

```powershell
& $PY scripts\run.py build               # -> data/index/（26076 chunks，faiss + BM25）
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

### 4. Web UI（FastAPI）

```powershell
& $PY scripts\run.py serve --port 8765                 # 默认 --backend fastapi
& $PY scripts\run.py serve --port 8765 --backend stdlib # 零依赖标准库实现
# 浏览器打开 http://127.0.0.1:8765/
```

搜索框、模式切换（hybrid/dense/bm25/hybrid+rerank）、结果卡片、来源链接、展开整页原文。

### 5. MCP 服务（给 agent 用）

```powershell
& $PY scripts\run.py mcp                                        # stdio（DSH/Claude Desktop 等本地集成）
& $PY scripts\run.py mcp --transport streamable-http --port 8766 # 常驻 HTTP
```

暴露 4 个工具：`search_docs`、`ask_docs`、`get_doc`、`rag_stats`。DSH 侧只需在
`~/.dsh/profiles/<profile>/cordis.patch.yml` 追加一条 `@deepseek-ai/dsh-mcp-client`
配置，详细步骤见 [docs/DSH_MCP_SETUP.md](docs/DSH_MCP_SETUP.md)。

## 评测结果（40 条人工 QA，`data/reports/eval.md`）

| 配置 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 | 平均延迟 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **hybrid（默认）** | 0.525 | **0.875** | 0.925 | 0.645 | **0.975** | 532 ms |
| dense | 0.500 | 0.850 | 0.925 | 0.644 | 0.900 | 28 ms |
| bm25 | 0.500 | 0.750 | 0.875 | 0.603 | 0.900 | 125 ms |
| hybrid+rerank | **0.650** | 0.800 | 0.925 | **0.725** | 0.975 | 1.35 s（GPU） |

- 英文 28 条 hybrid Recall@5 = 0.857；中文 12 条 = 0.917
- 分类：class 0.90 / getting_started 1.00 / troubleshooting 1.00 / tutorial 0.75 / engine_details 0.50
- `hybrid+rerank` 把 Recall@1 / MRR 进一步推高（重排会改变前 5 名构成，故 Recall@5 略降）；
  GPU 下单查询约 1.3 秒，可作为"高质量模式"。
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
      ├─ embed.py      SentenceTransformer(BGE-small, CUDA) → faiss.IndexFlatIP
      ├─ bm25.py        rank_bm25.BM25Okapi（保留 snake_case API 名 + 驼峰拆分）
      ▼
 retrieve.py   dense top-100 + bm25 top-100 → 加权 RRF(1.0/0.3, k=60) → [CrossEncoder top-100] → top-k
      ▼
 CLI / Python 库 / FastAPI 服务（service_fastapi.py + 内嵌 UI） → generate.py（openai SDK / 检索式）
```

- **分块**：class 页每个 method/property/signal/constant/enum 独立成块，块前缀如
  `Godot Docs > Class reference > Node > Method Descriptions` + `Node.add_child — method`；
  教程按 section 聚合，超长按段落窗口切分。
- **中文**：`zh_dict.py` 最长匹配 + jieba 词级补充翻译 Godot 术语，dense 用英文术语、
  BM25 用英文术语加权 + 原 query。
- **设备**：`embed.py` / `rerank` 均基于 sentence-transformers，`device=auto` 时自动 CUDA，
  否则 CPU；查询编码在 GPU 上约几十毫秒。

## Python 库

```python
from godot_rag import GodotRAG

rag = GodotRAG("data/index")                  # device 默认 auto（有 CUDA 用 GPU）
for r in rag.search("how to add a child node", k=5, mode="hybrid"):
    print(r["rank"], r["title"], r["symbol"], r["url"])
    print(r["text"][:200])

answer = rag.ask("queue_free 和 free 的区别", k=6)
print(answer["answer"], answer["citations"])
```

## MCP 集成

MCP 服务把文档检索包装成 agent 可直接调用的工具（协议层由官方 `mcp` Python SDK 提供）：

| 工具（agent 侧名称） | 作用 |
|---|---|
| `mcp__godot__search_docs` | 检索文档，返回结构化 JSON（标题/面包屑/API 符号/URL/片段） |
| `mcp__godot__ask_docs` | 回答 + `[n]` 引用（无 LLM key 时为检索式回答） |
| `mcp__godot__get_doc` | 按 `doc_id` 读取整页原文（去 HTML、截断） |
| `mcp__godot__rag_stats` | 索引规模、模型与设备信息 |

- 索引与模型在首次工具调用时懒加载，进程内复用；
- 默认 `stdio` 传输（agent 本地集成最简单），也支持 `streamable-http`（常驻/远程）；
- DSH 的配置方式见 [docs/DSH_MCP_SETUP.md](docs/DSH_MCP_SETUP.md)。

## DSH 原生插件（推荐）

`dsh-plugin/` 是一个 DSH bundle，安装后提供 4 个原生工具，不经过 MCP：

| 工具 | 作用 |
|---|---|
| `godot_search_docs` | 混合检索，返回标题、面包屑、`doc_id`、URL、片段 |
| `godot_find_symbol` | 精确 API 符号直达（含方法/属性/信号/常量/枚举） |
| `godot_get_doc` | 按 `doc_id` 读整页原文，可 `offset`/`limit` 续读 |
| `godot_rag_stats` | 索引状态、设备、模型、解析后的配置来源 |

- 插件自管一个 Python 子进程（JSON Lines 协议，`godot_rag.dsh_bridge`），首次调用冷加载约 25–35 s、之后单查询 0.1–0.5 s；
- 同一 bundle 里另有**默认关闭**的"使用日志"行，可记录检索轨迹与反馈，用于改进检索；
- 安装、配置、排错见 [docs/DSH_PLUGIN_SETUP.md](docs/DSH_PLUGIN_SETUP.md)，日志说明见 [docs/USAGE_LOG.md](docs/USAGE_LOG.md)。

## HTTP API（FastAPI）

```
GET  /                中文 Web UI
GET  /api/health      {"ok":true,"ready":true,"n_chunks":26076}
GET  /api/stats       含 device / index_backend / rerank_available
POST /api/search      {"query":"...","k":8,"mode":"hybrid"}
POST /api/ask         {"query":"...","k":8,"mode":"hybrid"}
GET  /api/doc?doc_id=classes/class_node
```

## 目录

```
src/godot_rag/     parse_rst / chunk / embed / bm25 / index / retrieve / generate /
                   service（标准库）/ service_fastapi（FastAPI）/ mcp_server（MCP）/
                   agent_api（工具 payload 层）/ dsh_bridge（DSH JSONL 桥）/ cli / eval
dsh-plugin/        DSH bundle：4 个原生工具 + 默认关闭的使用日志行 + 可移植性测试
scripts/           run.py（统一入口）、run_tests.py、fetch_models.py、validate_qa.py、
                   analyze_usage.py、probe_bridge.mjs
data/processed/    documents.jsonl、chunks.jsonl、qa.jsonl
data/index/        meta.json、chunks.pkl、faiss.index、bm25.pkl、docs.pkl
data/reports/      eval.md / eval.json
docs/              ENV.md（环境与依赖）、INTERFACES.md（模块接口）、USAGE.md（使用手册）、
                   DSH_PLUGIN_SETUP.md（DSH 插件安装）、USAGE_LOG.md（使用日志）、
                   DSH_MCP_SETUP.md（MCP 接入）、设计稿与实施计划
tests/             Python unittest（解析/分块/BM25/检索/CLI/服务/生成/评测/FastAPI/MCP/
                   agent_api/bridge/日志分析/可移植性）
```

## 测试

```powershell
& $PY scripts\run_tests.py -v     # 147 tests，不依赖 GPU / 全量索引 / 网络
```

## 已知限制

- **tiktoken 的 BPE 数据文件**（cl100k_base）需联网下载一次，当前网络无法拉取；缺失时
  prompt 预算自动回退字符估算（不影响功能）。修复方法见 [docs/ENV.md](docs/ENV.md)。
- 检索式回答（无 LLM key）只拼接官方原文要点，不生成连贯长答；要自然语言回答请配置 key。
- 中文长尾表述依赖术语词典，未覆盖时召回有限；agent 场景建议直接用英文 query。
- 图片/视频内容不进入索引（只收录文本）。
- `hybrid+rerank` 在 GPU 上约 1.3 s/查询，适合离线或高质量场景；Web 交互默认用 hybrid。
- DSH 插件首次调用需冷加载索引与模型（本机约 25–35 s）；可开启 `prewarm` 在插件启动时后台预热。
- 本快照为 stable 版；文档更新后需重新 `build`。
