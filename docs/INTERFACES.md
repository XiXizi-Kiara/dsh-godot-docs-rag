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
data/index/meta.json               # 索引元信息
data/index/embeddings.npy          # float32 (N, 384) 归一化向量
data/index/chunks.pkl              # list[dict] Chunk（与 embeddings 行对齐）
data/index/bm25.pkl                # BM25 倒排（pickle）
data/index/docs.pkl                # dict[doc_id] -> Document
data/reports/eval.md               # 评测报告
models/bge-small-en-v1.5/          # embedding 模型
models/bge-reranker-base/          # reranker（可选）
```

## 2. 数据结构（`types.py`）

```python
@dataclass
class Block:      kind: str; text: str; lang: str | None = None
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
 "title": str, "breadcrumbs": list[str], "url": str, "symbol": str, "text": str,
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

rag = GodotRAG(index_dir: str | Path = "data/index")   # mmap 懒加载
rag.search(query: str, k: int = 8, mode: str = "hybrid") -> list[dict]   # SearchResult dict
rag.ask(query: str, k: int = 8, mode: str = "hybrid", lang: str | None = None) -> dict  # AskResult
rag.stats() -> dict      # {"n_chunks", "n_docs", "modes", "models", "dim", ...}
rag.ready: bool          # 索引是否存在
```

`mode` ∈ `{"hybrid", "dense", "bm25", "hybrid+rerank", "dense+rerank"}`；默认 `hybrid`。

## 4. 构建（`build.py`）

```python
build_index(limit: int | None = None, force: bool = False, rerank: bool = True) -> dict  # 返回统计
```

CLI `python -m godot_rag build` 调用它。构建步骤：parse → chunk → embed → bm25 → 落盘。
`--limit N` 只处理前 N 篇文档（试跑用，写入 `data/index_smoke/` 以免污染全量索引）。

## 5. CLI（`cli.py`）

```
python -m godot_rag build  [--limit N] [--force]
python -m godot_rag search QUERY [--k 8] [--mode hybrid] [--json]
python -m godot_rag ask    QUERY [--k 8] [--mode hybrid] [--json]
python -m godot_rag serve  [--host 127.0.0.1] [--port 8765]
python -m godot_rag stats  [--json]
python -m godot_rag eval   [--configs hybrid,hybrid+rerank] [--json]
```

- 所有命令支持 `--json`，stdout 只输出 JSON（日志走 stderr），便于 agent 解析。
- 无索引时给出明确错误：`索引不存在，请先运行 python -m godot_rag build`，exit code 2。

## 6. HTTP 服务（`service.py`）

标准库 `ThreadingHTTPServer`，零依赖。端点：

```
GET  /                 单页 UI（内联 HTML/CSS/JS，中文界面）
GET  /api/health       -> {"ok": true, "ready": bool, "n_chunks": int}
GET  /api/stats        -> rag.stats()
POST /api/search       body {"query": str, "k": int, "mode": str} -> {"results": [SearchResult]}
POST /api/ask          body {"query": str, "k": int, "mode": str, "lang": str|null} -> AskResult
GET  /api/doc?doc_id=  -> {"doc_id", "title", "url", "html_text", "breadcrumbs"}  # 整页原文（用 Document.sections 拼纯文本/简单 HTML）
```

校验：query 非空且 ≤ 500 字符，k ∈ [1, 20]，mode 合法，否则 400。
绑定 127.0.0.1，非法 JSON 返回 400。`serve` 打印实际 URL。

## 7. 生成（`generate.py`）

```python
def answer(query: str, passages: list[dict], lang: str | None = None) -> dict
# 返回 {"answer": str, "llm_used": bool}
# 无 LLM key（环境变量 GODOT_RAG_LLM_API_KEY）时：检索式回答，取首片段首句摘要 + 说明
# 有 key：OpenAI 兼容 chat/completions，base_url 默认 https://api.deepseek.com/v1，
#         model 默认 deepseek-chat；必须只依据 passages，答案内用 [n] 标注引用。
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
# 词典 ≥ 100 条 Godot 领域术语，最长匹配优先；无命中返回 (原 query, [])
```

## 10. 测试与验收

- 运行环境：**必须使用 conda `godotrag` 环境的解释器**（见 docs/ENV.md）：
  `C:\Users\Kiara\.conda\envs\godotrag\python.exe`
- 测试命令：`& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run_tests.py -v`
  （或 `$env:PYTHONPATH="src"; ...python.exe -m unittest discover -s tests`）
- 必须全绿（不依赖 GPU / 不依赖全量索引）。
- Lead 验收：全量 build 成功、`search`/`ask`/`serve` 冒烟、eval 达到 Recall@5 ≥ 0.85、PageHit@5 ≥ 0.95。
- 任何模块不得新增 pip 依赖；标准库 + numpy/torch/transformers 之外一律不用。
