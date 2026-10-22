# 把 Godot RAG 接入 DSH（原生插件）

本插件把 GodotRag 的检索能力注册成 4 个 DSH 原生工具：

| 工具 | 作用 |
|---|---|
| `godot_search_docs` | 自然语言 / 关键词混合检索，返回标题、面包屑、`doc_id`、URL、片段 |
| `godot_find_symbol` | 精确 API 直达（`Node.add_child`、`move_and_slide`），可限定类与符号种类 |
| `godot_get_doc` | 按 `doc_id` 读取整页原文（支持 `offset`/`limit` 续读） |
| `godot_rag_stats` | 索引规模、设备、模型、索引 mtime、解析后的配置来源 |

> 说明：`$PY` 指 conda 环境 `godotrag` 的 python（`$PY = "$HOME\.conda\envs\godotrag\python.exe"`，或先 `conda activate godotrag` 后用 `python`）；`<PROJECT_DIR>` 指本仓库根目录。

## 1. 前置条件

1. 已按 [ENV.md](ENV.md) 装好 conda 环境 `godotrag`；
2. 已在项目根目录构建索引（`python scripts/run.py build`），`data/index/meta.json` 存在；
3. DSH 已安装并可打开插件管理页面。

## 2. 安装

**从 npm（推荐）**：

```powershell
dsh plugin --profile <profile> add @xixizi-kiara/dsh-godot-docs-rag
# 或在 DSH 内：
#   plugin_manager install_bundle(target: "@xixizi-kiara/dsh-godot-docs-rag@0.1.1")
```

> 桌面版 DSH 的 profile 由应用自身管理，请用 **Settings → Plugins** 按包名安装，或在会话里调用 `plugin_manager`；应用运行期间 `dsh plugin` CLI 不会修改该 profile。

**本地开发（未发布 / 改代码时）**：

```
plugin_manager install_bundle(target: <PROJECT_DIR>/dsh-plugin)
```

安装后插件管理列表出现两行：

- **Godot 文档 RAG**（工具，默认启用）
- **Godot 文档 RAG 使用日志**（默认关闭，见 [USAGE_LOG.md](USAGE_LOG.md)）

> bundle 的 `cordis.patch.yml` **不携带任何机器路径**；配置由插件按优先级解析，见下节。

## 3. 配置

解析优先级（从高到低）：

1. 插件行的 `config`（profile 覆盖）；
2. 环境变量 `GODOT_RAG_PYTHON` / `GODOT_RAG_PROJECT` / `GODOT_RAG_INDEX_DIR`；
3. 可移植默认值：`~/.conda/envs/godotrag/...`（python）；`projectDir` 仅在插件位于 GodotRag 仓库内时可自动推导；
4. 都失败 → 工具返回 `CONFIG_INVALID` 错误，消息内附可粘贴的配置片段。

> **从 npm 安装时必须显式配置 `projectDir`**（或设置 `GODOT_RAG_PROJECT`）：插件位于 profile 的 `node_modules` 下，无法推导你的仓库位置。

需要显式覆盖时，编辑 `$HOME/.dsh/profiles/<profile>/cordis.patch.yml`：

```yaml
- id: godot-docs-rag
  name: '@xixizi-kiara/dsh-godot-docs-rag'
  config:
    pythonExe: '<PYTHON_EXE>'
    projectDir: '<PROJECT_DIR>'
    # 其余可选：indexDir / defaultK / defaultMode / device / prewarm / toolTimeoutMs / docsBaseUrl
```

## 4. 使用建议

- 已知精确 API 名（如 `queue_free`）→ 先 `godot_find_symbol`，比自然语言检索更准；
- 不知道页面/API → `godot_search_docs`；结果里带 `doc_id`，需要完整上下文时再 `godot_get_doc`；
- 索引是否就绪、当前用的是 GPU 还是 CPU、配置从哪一层解析 → `godot_rag_stats`；
- 首次调用会冷加载索引与模型（本机实测约 25–35 s，已设 120 s 超时）；开启 `prewarm` 可在插件启动时后台预加载。

## 5. 排错

| 现象 | 原因与处理 |
|---|---|
| 工具不出现 | 插件行未启用，或安装后未重载；在插件管理列表确认两行状态 |
| `CONFIG_INVALID` | 按错误里的 `<PYTHON_EXE>` / `<PROJECT_DIR>` 片段补 profile 覆盖；`godot_rag_stats` 可看解析来源 |
| `INDEX_NOT_READY` | 索引未构建：`python scripts/run.py build` |
| 首次调用超时 | 冷加载较慢；确认 `toolTimeoutMs` ≥ 120000，或开启 `prewarm` |
| 宿主禁止 spawn | 若错误含 `EPERM` / `WinError 5`，说明宿主无法启动子进程；备用方案是改用 HTTP 连接层（暂未实现） |
| DSH 无法加载插件 | 查看插件管理器诊断；`node -e "import('<PROJECT_DIR>/dsh-plugin/index.js')"` 可本地自检 |

## 6. 卸载

```
plugin_manager remove_bundle(target: '@xixizi-kiara/dsh-godot-docs-rag')
```

本地开发安装同理（同一包名）；卸载只移除依赖与插件行，不会删除仓库里的代码。

（具体标识以 `plugin_manager list_bundles` 返回为准。）

## 7. 验收记录（2026-10-22，本机）

| 项 | 结果 |
|---|---|
| 插件管理器显示两行、日志行默认关闭 | ✅（Config 查询：工具行 active，日志行 inactive） |
| 4 个工具出现在工具列表 | ✅ |
| `godot_rag_stats`：26076 chunks / cuda / mtime | ✅ |
| 首次调用耗时 | ✅ 冷启动约 15.9 s（缓存热时为 24.6 s 量级，均 < 45 s） |
| 后续 `search` 延迟 | ✅ 89–158 ms（实测多次 < 1.5 s） |
| `search("add_child")` 命中 Node（k=3） | ✅ |
| `find_symbol("Node.add_child")` → method + 锚点 URL | ✅ |
| `get_doc("classes/class_node")` → 80,969 字符、可截断续读 | ✅ |
| 中文查询命中英文文档 | ✅（`节点怎么添加子节点`、`move_and_slide 不移动`） |
| 日志行默认无文件；启用后写入 tool_call（含 doc_id/mode/backend） | ✅ |
| 关闭日志行后不再增长 | ✅ |
| `analyze_usage.py` 报告 / `--export-qa` | ✅ |
| 反馈命令 `/godot-rag-good`、`/godot-rag-bad` | ✅ 用户实测（2026-10-22）：日志出现两条 `feedback`，备注 UTF-8 正常；DSH 重启后内存 refs 为空属预期 |
| 杀进程自愈 | ✅ 用户实测（2026-10-22）：结束桥进程后再次调用自动恢复 |
| DSH 重启无孤儿进程 | ✅ 用户实测（2026-10-22） |
| `CONFIG_INVALID` 实机验证 | ⚠️ 未实测（单测覆盖；未破坏性修改 profile） |
