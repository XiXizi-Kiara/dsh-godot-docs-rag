# Godot 官方文档 RAG

把 **Godot 4.x 官方文档**变成一个完全本地、可检索、可引用、能被 AI 助手直接调用的知识库。

- 覆盖 classes API 参考 + tutorials + getting_started + engine_details + about + community（1596 篇文档、26076 个检索块）；
- 中文提问也能命中英文文档，回答带官方原文与可点击链接；
- 全程离线、不上传文档；有 GPU 用 GPU，没有也能跑 CPU。

## 能帮你做什么

| 场景 | 示例 |
|---|---|
| 查 API 行为 | "`queue_free` 和 `free` 有什么区别？" |
| 定位具体方法 | "`move_and_slide` 的参数和返回值是什么？" |
| 排查问题 | "CharacterBody2D 用了 move_and_slide 为什么还是不动？" |
| 学教程 | "怎么做 2D 角色移动 / 信号连接 / 场景切换？" |
| 要出处 | 每条结果都带文档标题、小节路径和官网链接 |

支持 5 种用法，按你的习惯选一种即可：

| 用法 | 适合谁 |
|---|---|
| **DSH 插件**（推荐） | 在 DSH 里对话，AI 自动查文档 |
| **Web UI** | 想在浏览器里像搜索引擎一样查 |
| **命令行** | 写脚本、快速查一条 |
| **Python 库** | 在自己的程序里调用 |
| **MCP** | Claude Desktop 等支持 MCP 的客户端 |

## 5 分钟上手

> `$PY` 指 conda 环境 `godotrag` 的 python：`$PY = "$HOME\.conda\envs\godotrag\python.exe"`；`<PROJECT_DIR>` 指本仓库根目录。
> 环境依赖已配置好，首次使用只需下面 3 步。

### 1. 下载模型（只做一次）

```powershell
cd <PROJECT_DIR>
& $PY scripts\fetch_models.py            # 检索模型（必需，约 130 MB）
& $PY scripts\fetch_models.py --rerank   # 精排模型（可选，1.1 GB，质量更高但更慢）
```

### 2. 构建索引（只做一次；官方文档更新后重做）

```powershell
& $PY scripts\run.py build               # GPU 约 100 秒 / CPU 约 5 分钟
& $PY scripts\run.py build --limit 200   # 想先试跑可以用这个
```

### 3. 开始使用

选一种即可，最省事的是 DSH 插件（见下节）；想立刻在浏览器里搜：

```powershell
& $PY scripts\run.py serve --port 8765
# 打开 http://127.0.0.1:8765/
```

---

## 用法 A：在 DSH 里直接问（推荐）

安装只需一次，根据你用的 DSH 版本选一种：

**桌面版 DSH**：打开 DSH 的插件页（桌面版在 **Settings → Plugins**），搜索「Godot 文档 RAG」并安装。
搜不到时，在安装入口直接粘贴包名：`@xixizi-kiara/dsh-godot-docs-rag`。

**命令行版 DSH**：

```powershell
dsh plugin --profile <profile> add @xixizi-kiara/dsh-godot-docs-rag
```

安装后第一次使用，如果提示找不到项目目录，按提示填入你的 GodotRag 文件夹（或设置环境变量 `GODOT_RAG_PROJECT`）——插件需要从这里读取索引。

之后正常对话即可，AI 会在需要时自动调用 4 个工具：

| 工具 | 作用 |
|---|---|
| `godot_search_docs` | 按自然语言/关键词找文档，返回标题、`doc_id`、官网链接、片段 |
| `godot_find_symbol` | 按精确 API 名查（如 `Node.add_child`、`move_and_slide`） |
| `godot_get_doc` | 读整页原文，可分段续读 |
| `godot_rag_stats` | 查看索引是否就绪、用的 GPU 还是 CPU |

你可以直接这样问：

> "Godot 里 `queue_free` 之后节点什么时候真正释放？给我官方文档出处。"

提示：

- 第一次调用要加载索引与模型（约 25–35 秒），之后每次查询 0.1–0.5 秒；
- 不想等第一次：在插件配置里开启 `prewarm`（插件启动时后台预加载，详见安装文档）；
- 使用日志默认关闭；需要记录检索轨迹/反馈时，在插件管理列表打开"Godot 文档 RAG 使用日志"。

详细安装、配置与排错：[docs/DSH_PLUGIN_SETUP.md](docs/DSH_PLUGIN_SETUP.md)
使用日志与分析：[docs/USAGE_LOG.md](docs/USAGE_LOG.md)

## 用法 B：浏览器里搜（Web UI）

```powershell
& $PY scripts\run.py serve --port 8765                 # FastAPI 版（默认）
& $PY scripts\run.py serve --port 8765 --backend stdlib # 零依赖标准库版
# 打开 http://127.0.0.1:8765/
```

支持搜索框、检索模式切换（hybrid / dense / bm25 / hybrid+rerank）、结果卡片、来源链接、展开整页原文。

## 用法 C：命令行

```powershell
& $PY scripts\run.py search "how to free a node" --k 5
& $PY scripts\run.py search "queue_free 和 free 有什么区别" --json
& $PY scripts\run.py ask "CharacterBody2D 为什么不动？" --json
```

`ask` 在没有配置大模型时返回**检索式回答**（只拼接官方原文，不编造）；配置后改为生成式回答：

```powershell
$env:GODOT_RAG_LLM_API_KEY = "sk-..."
# 可选：$env:GODOT_RAG_LLM_BASE_URL / $env:GODOT_RAG_LLM_MODEL
```

## 用法 D：在自己的 Python 程序里

```python
from godot_rag import GodotRAG

rag = GodotRAG("data/index")          # 有 CUDA 自动用 GPU
for r in rag.search("how to add a child node", k=5):
    print(r["rank"], r["title"], r["symbol"], r["url"])

answer = rag.ask("queue_free 和 free 的区别", k=6)
print(answer["answer"], answer["citations"])
```

## 用法 E：其他 AI 客户端（MCP）

```powershell
& $PY scripts\run.py mcp                                         # stdio（本地客户端）
& $PY scripts\run.py mcp --transport streamable-http --port 8766  # 常驻 HTTP
```

暴露 4 个工具（agent 侧名称形如 `mcp__godot__search_docs`）：`search_docs`、`ask_docs`、`get_doc`、`rag_stats`。
DSH 的 MCP 接入步骤见 [docs/DSH_MCP_SETUP.md](docs/DSH_MCP_SETUP.md)。

---

## 怎么问效果更好

- **已知 API 名**（`queue_free`、`move_and_slide`）→ 直接问 API，插件会用精确符号查询；
- **不确定页面/API** → 描述需求（"怎么让角色沿斜坡滑动"），用自然语言检索；
- **要完整上下文** → 先搜到 `doc_id`，再让 AI 读整页（插件会自动这么做）；
- **中文提问可用**，但英文术语命中率通常更高；
- **要最高质量** → 用 `hybrid+rerank` 模式（GPU 下约 1.3 秒/次）。

## 常用配置

| 环境变量 | 作用 |
|---|---|
| `GODOT_RAG_DEVICE` | `auto`（默认）/ `cuda` / `cpu` |
| `GODOT_RAG_INDEX_DIR` | 索引目录，默认 `data/index` |
| `GODOT_RAG_LLM_API_KEY` / `_BASE_URL` / `_MODEL` | 启用生成式回答（可选） |
| `GODOT_RAG_DOCS_BASE_URL` | 结果链接的文档站地址 |
| `GODOT_RAG_PYTHON` / `GODOT_RAG_PROJECT` | DSH 插件查找解释器/项目根（一般无需设置） |

DSH 插件配置（解释器、索引目录、默认条数、预热等）见 [docs/DSH_PLUGIN_SETUP.md](docs/DSH_PLUGIN_SETUP.md)。

## 效果如何

40 条人工评测题（英文 28 / 中文 12）：

| 配置 | Recall@5 | PageHit@5 | 平均延迟 |
|---|---:|---:|---:|
| **hybrid（默认）** | **0.875** | **0.975** | 532 ms |
| dense | 0.850 | 0.900 | 28 ms |
| bm25 | 0.750 | 0.900 | 125 ms |
| hybrid+rerank | 0.800 | 0.975 | 1.35 s（GPU） |

- 中文 12 条 hybrid Recall@5 = 0.917；英文 28 条 = 0.857；
- 精排（rerank）能进一步提高"第一条就命中"的比例，适合离线或追求质量时使用；
- 完整报告：[data/reports/eval.md](data/reports/eval.md)；复跑：
  `& $PY scripts\run.py eval --configs hybrid,dense,bm25,hybrid+rerank --json`

## 常见问题

| 现象 | 处理 |
|---|---|
| 提示"索引尚未构建" | 先执行第 2 步：`& $PY scripts\run.py build` |
| 首次查询很慢（几十秒） | 正常，正在加载索引与模型；DSH 插件可开 `prewarm`，后续查询毫秒级 |
| 中文问题结果不理想 | 换英文术语或更具体的 API 名；也欢迎用使用日志记录 bad 查询 |
| 没有 GPU / 显存不够 | 设置 `GODOT_RAG_DEVICE=cpu`，会自动走 CPU（更慢但可用） |
| 端口被占用 | `serve` 或 `mcp --port` 换一个端口 |
| DSH 里没有 `godot_*` 工具 | 确认插件行已启用；重载插件或重启 DSH |
| DSH 插件报配置错误（`CONFIG_INVALID`） | 按错误信息里的片段在 profile 配置里填 `pythonExe` / `projectDir` |
| 日志在哪、怎么关 | 位置与清空方式见 [docs/USAGE_LOG.md](docs/USAGE_LOG.md)；不开启就不产生任何日志 |
| 官方文档更新了 | 替换 `godot-docs-html-stable/` 后重新执行第 2 步构建索引 |

更多环境与依赖问题：[docs/ENV.md](docs/ENV.md)；完整使用手册：[docs/USAGE.md](docs/USAGE.md)。

## 已知限制

- 图片/视频内容不进索引（只收录文本）；
- 无大模型 key 时只做"检索式回答"，不会生成连贯长文；
- 中文长尾表述依赖术语词典，覆盖不到时召回有限；
- 精排模型约 1.1 GB，且 GPU 下单次约 1.3 秒，适合离线/高质量场景；
- 本项目基于 stable 快照，文档更新后需要重建索引；
- tiktoken 的 BPE 数据文件需要联网下载一次；缺失时自动回退字符估算（不影响功能），修复方法见 [docs/ENV.md](docs/ENV.md)。

---

## 开发者与维护者

### 工作原理（一句话版）

RST 解析 → 按符号/小节分块 → BGE 向量（faiss）+ BM25 双路召回 → 加权 RRF 融合 → 可选 cross-encoder 精排 → CLI / Web / Python / MCP / DSH 插件共用同一套检索与生成层。

```
_sources/*.rst → parse_rst → documents → chunk → 26076 chunks
                                   ├─ embed.py  → faiss（BGE-small）
                                   └─ bm25.py   → rank_bm25
retrieve.py：dense top-100 + bm25 top-100 → RRF → [rerank] → top-k
入口：cli / service_fastapi / mcp_server / dsh_bridge（插件子进程）
```

### 目录

```
src/godot_rag/     核心：parse_rst / chunk / embed / bm25 / index / retrieve / generate /
                   service（标准库）/ service_fastapi / mcp_server / agent_api / dsh_bridge / cli / eval
dsh-plugin/        DSH bundle：4 个原生工具 + 默认关闭的使用日志行 + 可移植性测试
scripts/           run.py（统一入口）、fetch_models.py、run_tests.py、validate_qa.py、
                   analyze_usage.py、probe_bridge.mjs
data/processed/    documents.jsonl、chunks.jsonl、qa.jsonl
data/index/        索引产物（meta.json / chunks.pkl / faiss.index / bm25.pkl / docs.pkl）
data/reports/      评测报告
docs/              ENV / INTERFACES / USAGE / DSH_PLUGIN_SETUP / USAGE_LOG / DSH_MCP_SETUP
tests/             Python unittest（147 个）
```

### 测试

```powershell
& $PY scripts\run_tests.py -v        # 147 tests，不依赖 GPU / 全量索引 / 网络
for file in dsh-plugin/test/*.test.js; do node "$file"; done   # 插件 25 个测试
```

### 约定

- 仓库内禁止出现机器相关绝对路径（`tests/test_portability.py` 会拦截）；
- 接口与数据结构以 [docs/INTERFACES.md](docs/INTERFACES.md) 为准。

---

## 许可证

本项目代码采用 **MIT License**，见 [LICENSE](LICENSE)。
DSH 插件 `dsh-plugin/` 同样为 MIT（与 npm 已发布版本一致），见 [dsh-plugin/LICENSE](dsh-plugin/LICENSE)。

> 仓库不包含 Godot 官方文档内容本身（`godot-docs-html-stable/` 已在 `.gitignore` 中）；如需再分发官方文档，请遵守 Godot 文档自身的许可与署名要求。
