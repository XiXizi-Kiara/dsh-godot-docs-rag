# Godot RAG —— 模块接口约定（冻结版 v1）

> 所有模块共享本约定。改动接口必须同步修改本文件并通知 Lead。
> 项目根目录：`C:\Users\Kiara\Desktop\GodotRag`，源码在 `src/godot_rag/`，通过
> `src` 加入 `PYTHONPATH` 后 `import godot_rag`。

## 1. 目录与产物

```
godot-docs-html-stable/_sources/   # 输入：1597 个 RST 源
data/processed/documents.jsonl     # parse_rst 输出，一行一个 Document
data/processed/chunks.jsonl        # chunk 输出，一行一个 Chunk
data/processed/qa.jsonl            # eval 的 40 条问答
data/index/meta.json               # 索引元信息（含 device / index_backend / libs 版本）
data/index/faiss.index             # faiss IndexFlatIP（N×384，向量已归一化）
data/index/chunks.pkl              # list[dict] Chunk（行号与 faiss 向量对齐）
data/index/bm25.pkl                # rank_bm25.BM25Okapi（pickle）
data/index/docs.pkl                # dict[doc_id] -> Document
data/reports/eval.md               # 评测报告
models/bge-small-en-v1.5/          # embedding 模型
models/bge-reranker-base/          # reranker（可选）
```

## 2. 数据结构（`types.py`）

```python
@dataclass
class Block:      kind: str; text: str; lang: str | None = None
                  ref_kind: str | None = None   # class 页符号类别：method/property/signal/constant/enum
                  symbol: str | None = None     # class 页符号名，如 "add_child"
@dataclass
class Section:    title: str; level: int; blocks: list[Block]; anchor: str = ""
@dataclass
class Document:   doc_id: str; doc_type: str; title: str; breadcrumbs: list[str];
                  url: str; sections: list[Section];
                  class_name: str | None = None; inherits: str | None = None;
                  meta: dict = field(default_factory=dict)

# Chunk 是 dict（直接序列化为 jsonl / pickle），字段固定：
# chunk_id: str   稳定哈希（sha1 of doc_id|symbol|anchor|text[:120]）
# doc_id, doc_type, title: str
# breadcrumbs: list[str]
# url: str        # 相对 HTML 路径，可带 #anchor
# anchor: str
# symbol: str     # 如 "Node.add_child()" / "Node.position" / ""（教程块）
# text: str       # 检索用文本（含面包屑前缀）
# raw_text: str   # 不含前缀的正文
# n_tokens: int   # 估算 token 数
```

`SearchResult`（`retrieve.py` 定义）→ dict 字段：

```python
{"rank": int, "score": float, "chunk_id": str, "doc_id": str, "doc_type": str,
 "title": str, "breadcrumbs": list[str], "url": str, "anchor": str,
 "symbol": str, "text": str, "raw_text": str,
 "score_detail": dict}   # {"dense": float|None, "bm25": float|None, "rerank": float|None}
```

`AskResult` → dict：

```python
{"query": str, "mode": str, "answer": str, "passages": list[SearchResult],
 "citations": [{"n": int, "title": str, "url": str}],
 "llm_used": bool}
```

## 3. 公共 API（`__init__.py`）

```python
from godot_rag import GodotRAG

rag = GodotRAG(index_dir: str | Path = "data/index", device: str | None = None)  # device=auto/cpu/cuda
rag.search(query: str, k: int = 8, mode: str = "hybrid") -> list[dict]   # SearchResult dict
rag.ask(query: str, k: int = 8, mode: str = "hybrid", lang: str | None = None) -> dict  # AskResult
rag.stats() -> dict      # {"n_chunks", "n_docs", "modes", "models", "dim", "device", "index_backend", ...}
rag.ready: bool          # 索引是否存在
```

`mode` ∈ `{"hybrid", "dense", "bm25", "hybrid+rerank", "dense+rerank"}`；默认 `hybrid`。

## 4. 构建（`build.py`）

```python
build_index(limit: int | None = None, force: bool = False,
            index_dir: str | Path | None = None, device: str | None = None) -> dict
```

CLI `python -m godot_rag build` 调用它。构建步骤：parse → chunk → embed（sentence-transformers）
→ BM25（rank_bm25）→ 写 faiss.index / bm25.pkl / chunks.pkl / docs.pkl / meta.json。
`--limit N` 只处理前 N 篇文档（试跑用，写入 `data/index_smoke/` 以免污染全量索引）。
`device` 透传给 Encoder（auto/cpu/cuda），实际设备写入 meta。

## 5. CLI（`cli.py`）

```
python -m godot_rag build  [--limit N] [--force]
python -m godot_rag search QUERY [--k 8] [--mode hybrid] [--json] [--index-dir DIR]
python -m godot_rag ask    QUERY [--k 8] [--mode hybrid] [--json] [--index-dir DIR]
python -m godot_rag serve  [--host 127.0.0.1] [--port 8765] [--backend fastapi|stdlib]
python -m godot_rag stats  [--json] [--index-dir DIR]
python -m godot_rag eval   [--configs hybrid,hybrid+rerank] [--json]
```

- 所有命令支持 `--json`，stdout 只输出 JSON（日志走 stderr），便于 agent 解析。
- 无索引时给出明确错误：`索引不存在，请先运行 python -m godot_rag build`，exit code 2。

## 6. HTTP 服务（`service_fastapi.py` 默认 / `service.py` 备选）

- **默认后端**：`service_fastapi.py`（FastAPI + uvicorn），复用 `service.App` 的业务逻辑与
  单页 UI，端点/错误码与标准库实现一致。
- **备选后端**：`service.py`（标准库 `ThreadingHTTPServer`，零第三方依赖），
  `serve --backend stdlib` 启动。

```
GET  /                 单页 UI（内联 HTML/CSS/JS，中文界面）
GET  /api/health       -> {"ok": true, "ready": bool, "n_chunks": int}
GET  /api/stats        -> rag.stats()
POST /api/search       body {"query": str, "k": int, "mode": str} -> {"results": [SearchResult]}
POST /api/ask          body {"query": str, "k": int, "mode": str, "lang": str|null} -> AskResult
GET  /api/doc?doc_id=  -> {"doc_id", "title", "url", "html_text", "breadcrumbs"}
```

校验：query 非空且 ≤ 500 字符，k ∈ [1, 20]，mode 合法，否则 400；未就绪 503。
绑定 127.0.0.1，非法 JSON 返回 400。`serve` 打印实际 URL。

## 7. 生成（`generate.py`）

```python
def answer(query: str, passages: list[dict], lang: str | None = None, *,
           api_key: str | None = None, client=None, timeout: float = 60.0) -> dict
# 返回 {"answer": str, "llm_used": bool}
# 无 LLM key（GODOT_RAG_LLM_API_KEY）时：检索式回答，取首片段首句摘要 + 说明
# 有 key：openai SDK 调 OpenAI 兼容 /chat/completions（默认 DeepSeek），
#         只依据 passages，答案内用 [n] 标注引用；失败静默降级。
# prompt 片段预算用 tiktoken 精确计数（BPE 文件缺失时自动回退字符估算）。
```

## 8. 评测（`eval.py`）

- 输入 `data/processed/qa.jsonl`，每行：
  `{"qid": "q001", "query": "...", "lang": "en|zh", "doc_type": "class|tutorial|...", "gold_doc_ids": ["classes/class_node"], "gold_terms": ["add_child"], "note": "..."}`
- 指标：Recall@1/5/10、MRR@10、PageHit@5（任一结果 doc_id ∈ gold_doc_ids 或 text 含 gold_terms）。
- 输出 `data/reports/eval.md` 与 `data/reports/eval.json`。
- QA 集由 eval 负责人手工编写，覆盖：API 用法、概念、教程步骤、报错/易错点；中英各占一定比例；gold 必须是真实存在的 doc_id（用 documents.jsonl 校验，允许先用目录名核对）。

## 9. 中文术语映射（`zh_dict.py`）

```python
translate_query(query: str) -> tuple[str, list[tuple[str, str]]]
# 返回 (增强后的查询, [(中文词, 英文词), ...])
# 词典 268 条 Godot 领域术语；最长匹配 + jieba 词级补充；无命中返回 (原 query, [])
```

## 10. 测试与验收

- 运行环境：**必须使用 conda `godotrag` 环境的解释器**（见 docs/ENV.md）：
  `C:\Users\Kiara\.conda\envs\godotrag\python.exe`
- 测试命令：`& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run_tests.py -v`
  （或 `$env:PYTHONPATH="src"; ...python.exe -m unittest discover -s tests`）
- 必须全绿（不依赖 GPU / 不依赖全量索引）——当前 **116 个 unittest**。
- Lead 验收：全量 build 成功、`search`/`ask`/`serve` 冒烟、eval 达到 Recall@5 ≥ 0.85、PageHit@5 ≥ 0.95（当前 hybrid 0.875 / 0.975）。
- 运行依赖见 [docs/ENV.md](ENV.md) 与 [requirements.txt](../requirements.txt)：torch(cu128)、
  sentence-transformers、faiss-cpu、rank_bm25、jieba、fastapi、uvicorn、openai、tiktoken、mcp 等。
  新增依赖必须同步写入 requirements.txt 并保证测试可离线运行。

## 11. MCP 服务（`mcp_server.py`）

```python
python -m godot_rag.mcp_server [--transport stdio|streamable-http|sse]
                               [--host 127.0.0.1] [--port 8766] [--index-dir DIR]
# CLI 等价入口：python -m godot_rag mcp ...
```

工具（agent 侧名称 `mcp__<serverName>__<tool>`）：

| 工具 | 输入 | 输出 |
|---|---|---|
| `search_docs` | `query`, `k=5`, `mode="hybrid"` | JSON：rank/score/title/symbol/breadcrumbs/url/text |
| `ask_docs` | `query`, `k=6`, `mode="hybrid"` | Markdown：回答 + `[n]` 引用 + 完整 URL |
| `get_doc` | `doc_id`, `max_chars=6000` | Markdown：标题/doc_id/URL/正文（去 HTML、可截断） |
| `rag_stats` | — | JSON：索引规模、模型、设备 |

- 索引与模型在**首次工具调用时懒加载**，通过模块级 `_RAG` 在进程内复用；
- 索引缺失等错误以可读文本返回（不抛裸异常），并提示先运行 `scripts/run.py build`；
- 结果 URL 由相对路径补全为 `https://docs.godotengine.org/en/stable/...`；
- DSH 侧配置见 [docs/DSH_MCP_SETUP.md](DSH_MCP_SETUP.md)（`@deepseek-ai/dsh-mcp-client`，
  stdio 或 streamable-http）。
