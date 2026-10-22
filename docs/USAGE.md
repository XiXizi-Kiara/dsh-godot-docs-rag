# Godot RAG 使用手册（CLI / 生成层 / Web 服务）

> 说明：`$PY` 指 conda 环境 godotrag 的 python（`$PY = "$HOME\.conda\envs\godotrag\python.exe"`）；`<PROJECT_DIR>` 指本仓库根目录。

本文档介绍接口层的三种使用方式：命令行、Python 库问答、本地 Web 服务。
接口定义见 `docs/INTERFACES.md`，运行环境见 `docs/ENV.md`。

## 0. 环境与入口

项目统一使用 conda `godotrag` 环境（Python 3.12 + CUDA torch），**不要**使用其他
Python 解释器。依赖已安装完毕（见 [docs/ENV.md](ENV.md) 与 [requirements.txt](../requirements.txt)）：
sentence-transformers / faiss-cpu / rank_bm25 / jieba / fastapi / uvicorn / openai / tiktoken。
CUDA 可用时自动走 GPU（RTX 4060 实测嵌入约 250–500 条/秒、精排 100 条约 1 秒）。

在项目根目录执行：

```powershell
# 等价于设置 PYTHONPATH=src 后运行 python -m godot_rag
& "$PY" scripts\run.py search "add_child" --json

# 或者手动设置 PYTHONPATH（脚本 / 其他 Python 程序调用时）
$env:PYTHONPATH = "src"
& "$PY" -m godot_rag stats --json
```

所有子命令都支持 `--json`：此时 **stdout 只输出 JSON**，进度日志一律走 stderr，
方便脚本与 agent 解析。出错时除 stderr 中文提示外，`--json` 模式还会在 stdout
输出 `{"error": "..."}`（仍是合法 JSON）。

## 1. 构建索引

```powershell
# 全量构建（解析 -> 分块 -> 嵌入 -> BM25 -> 落盘到 data/index/）
... scripts\run.py build

# 试跑：只处理前 N 篇文档，产物写入 data/index_smoke/，不污染正式索引
... scripts\run.py build --limit 30

# 强制重建已有索引
... scripts\run.py build --force
```

- 构建产物位于 `data/index/`（试跑为 `data/index_smoke/`）：`meta.json`、
  `embeddings.npy`、`chunks.pkl`、`bm25.pkl`、`docs.pkl`。
- 全量构建在 CPU 上约数分钟，构建期间会打印进度。构建不是接口层职责，
  CLI 只负责调用 `godot_rag.build.build_index(limit=..., force=...)` 并展示统计。
- 已存在索引时 `build` 默认跳过；加 `--force` 重建。

## 2. 命令行子命令

```text
python -m godot_rag build  [--limit N] [--force] [--json]
python -m godot_rag search QUERY [--k 8] [--mode hybrid] [--json] [--index-dir DIR]
python -m godot_rag ask    QUERY [--k 8] [--mode hybrid] [--json] [--index-dir DIR]
python -m godot_rag serve  [--host 127.0.0.1] [--port 8765] [--backend fastapi|stdlib] [--index-dir DIR]
python -m godot_rag stats  [--json] [--index-dir DIR]
python -m godot_rag eval   [--configs hybrid,hybrid+rerank] [--json]
```

- `--index-dir` 默认 `data/index`，便于用 smoke 索引或临时索引做验证；
- `--mode` 可选 `hybrid` / `dense` / `bm25` / `hybrid+rerank` / `dense+rerank`，
  默认 `hybrid`；
- `--debug` 放在子命令前（`python -m godot_rag --debug search ...`）可在出错时
  打印完整 traceback，默认只输出友好错误。

### 2.1 search

```powershell
... scripts\run.py search "queue_free" --k 5
... scripts\run.py search "signal connect" --k 3 --mode bm25 --json
```

普通模式打印排名、得分、标题、符号、面包屑路径、片段与链接。`--json` 输出：

```json
{"query": "...", "mode": "hybrid", "k": 8, "elapsed_ms": 12.3,
 "results": [{"rank": 1, "score": 0.87, "chunk_id": "...", "doc_id": "...",
              "doc_type": "class", "title": "...", "breadcrumbs": ["..."],
              "url": "...", "symbol": "...", "text": "...", "score_detail": {}}]}
```

### 2.2 ask

```powershell
... scripts\run.py ask "queue_free 和 free 有什么区别"
... scripts\run.py ask "how to add a child node" --k 5 --json
```

`--json` 输出 `AskResult`：

```json
{"query": "...", "mode": "hybrid", "answer": "...", "llm_used": false,
 "passages": [SearchResult, ...],
 "citations": [{"n": 1, "title": "...", "url": "..."}]}
```

回答由 `godot_rag.generate.answer()` 生成：

- **未配置 LLM**（无 `GODOT_RAG_LLM_API_KEY`）：检索式回答。取首个片段的首句作摘要，
  并列出 3–5 条来自不同片段的要点（带 `[n]` 与文档标题），明确标注
  “检索式回答…未使用生成模型”，所有内容均摘自片段，不编造。
- **配置了 LLM**：通过 openai SDK 调 OpenAI 兼容的 `/chat/completions`。默认
  `base_url=https://api.deepseek.com/v1`、`model=deepseek-chat`，
  可用环境变量覆盖：

  ```powershell
  $env:GODOT_RAG_LLM_API_KEY = "sk-..."
  $env:GODOT_RAG_LLM_BASE_URL = "https://api.deepseek.com/v1"  # 可选
  $env:GODOT_RAG_LLM_MODEL = "deepseek-chat"                    # 可选
  ```

  Prompt 强制“只依据片段、逐条 `[n]` 引用、不知道就明说、中文提问中文回答”；
  片段预算用 tiktoken 精确计数（BPE 数据文件缺失时自动回退字符估算）。
  网络/HTTP/解析失败（含超时 60s）会**自动降级**为检索式回答并在答案开头说明，
  HTTP 错误细节不会泄漏给用户。若需离线或没有 key，不设置该变量即可。

### 2.3 serve

```powershell
... scripts\run.py serve --port 8765                 # 默认 FastAPI（uvicorn）
... scripts\run.py serve --port 8765 --backend stdlib # 标准库实现，零第三方依赖
# 启动后打印：Godot RAG（FastAPI）已启动：http://127.0.0.1:8765/
```

Ctrl+C 优雅退出。索引尚未构建时服务仍可启动，UI 会提示先运行 `build`。

### 2.4 MCP 服务

```powershell
... scripts\run.py mcp                                   # stdio（默认，推荐给本地 agent）
... scripts\run.py mcp --transport streamable-http --port 8766  # 常驻 HTTP
# 等价：python -m godot_rag.mcp_server ...
```

暴露 4 个工具：`search_docs`、`ask_docs`、`get_doc`、`rag_stats`（工具名在 agent 侧为
`mcp__godot__*`）。索引在首次工具调用时懒加载。DSH 侧接入步骤见
[DSH_MCP_SETUP.md](DSH_MCP_SETUP.md)。

### 2.5 stats / eval

- `stats`：打印 `rag.stats()`（片段数、文档数、维度、模型、构建耗时等）；
- `eval`：转调 `godot_rag.eval.main`，参数原样传递（`--configs`、`--json`），
  报告写入 `data/reports/eval.md` 与 `eval.json`。

### 2.5 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 索引不存在/未就绪（`索引不存在，请先运行 python -m godot_rag build`）、依赖缺失、评测输入缺失 |
| 1 | 运行期错误（检索异常、服务端口占用等） |
| 130 | Ctrl+C 取消 |

## 3. Web 服务与 UI

`serve` 启动后浏览器打开 `http://127.0.0.1:8765/`，得到单页中文界面（内联
CSS/JS，无外链依赖，宽度 < 900px 自动转为竖排布局）：

- 搜索框（回车提交）+ 模式下拉（hybrid/dense/bm25/hybrid+rerank）+ k 选择；
- 结果卡片：排名、标题、面包屑、关键词高亮片段、相关度、来源链接（新窗口打开
  Godot 官方文档）、检索耗时；
- “查看整页原文”：调用 `/api/doc` 在该结果下方展开整页正文（段落/代码/提示块）；
- 顶部健康徽标与错误横幅；索引未构建时提示先运行 build。

### 3.1 HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 单页 UI |
| GET | `/api/health` | `{"ok": true, "ready": bool, "n_chunks": int}` |
| GET | `/api/stats` | `rag.stats()` |
| POST | `/api/search` | body `{"query", "k", "mode"}` → `{"results": [...]}` |
| POST | `/api/ask` | body `{"query", "k", "mode", "lang"}` → `AskResult` |
| GET | `/api/doc?doc_id=` | `{"doc_id","title","url","breadcrumbs","html_text"}` |

校验规则（不合法返回 400 + `{"error": "..."}`）：`query` 非空且 ≤ 500 字符；
`k` 为 1–20 的整数（默认 8）；`mode` 必须在合法集合内；非法 JSON/空 body 返回 400。
索引未就绪时检索类接口返回 503；未知文档返回 404；服务端异常返回 500（细节只写
服务端日志）。默认只绑定 `127.0.0.1`，不提供 CORS。

`/api/doc` 的整页原文优先取 `GodotRAG.get_doc()`，否则直接读取
`data/index/docs.pkl`，把 `Document.sections` 渲染为转义后的简单 HTML
（纯文本段落、代码块、提示块），不执行任何脚本。

UI 中“来源链接”默认指向 `https://docs.godotengine.org/en/stable/<url>`，如需指向
本地 HTML 快照可设置：

```powershell
$env:GODOT_RAG_DOCS_BASE_URL = "http://127.0.0.1:8000/"
```

（模型下载、数据构建等脚本不在此文档范围内。）

## 4. 测试

```powershell
# 全部单元测试（不依赖 GPU、全量索引与网络）
& "$PY" scripts\run_tests.py -v

# 只跑接口层三组测试
$env:PYTHONPATH = "src"
& "$PY" -m unittest tests.test_service tests.test_cli tests.test_generate -v
```

- `test_generate.py`：检索式回答（摘要/要点/引用编号/不编造）、LLM 成功请求格式、
  base_url/model 覆盖、失败降级且不泄漏错误；
- `test_cli.py`：fake 索引目录 + 注入假 RAG，验证 `search --json` 输出、参数透传、
  无索引退出码 2、build 参数透传、stats/ask/eval；
- `test_service.py`：随机端口启动真实 HTTP 服务，验证 health/search/ask/doc/stats/
  参数校验/404/503、UI 关键元素与 HTML 转义，测试结束自动关闭服务。

## 5. 常见问题

| 现象 | 处理 |
| --- | --- |
| `索引不存在，请先运行 python -m godot_rag build`（退出码 2） | 先执行 `scripts\run.py build`；或用 `--index-dir data/index_smoke` 指向试跑索引 |
| 启动服务后 UI 提示“索引尚未构建” | 索引目录里没有 `meta.json`；检查 `--index-dir` 是否正确 |
| `启动服务失败（端口 … 可能被占用）` | 换 `--port`，或关闭占用端口的进程 |
| `ask` 答案开头出现“生成模型调用失败，已自动降级…” | LLM 网络/Key/配额异常；`unset GODOT_RAG_LLM_API_KEY` 可明确使用检索式回答 |
| rerank 模式没有提升 | `models/bge-reranker-base/` 缺失时检索器自动降级为 RRF，属预期行为 |
| Windows 控制台中文乱码 | 接口层已把 CLI 的 stdout/stderr 切到 UTF-8；若在旧控制台仍乱码，可 `chcp 65001` |
