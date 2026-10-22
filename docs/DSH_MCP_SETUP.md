# 把 Godot RAG 接入 DSH（MCP）

> 说明：`$PY` 指 conda 环境 godotrag 的 python（`$PY = "$HOME\.conda\envs\godotrag\python.exe"`）；`<PROJECT_DIR>` 指本仓库根目录。

DSH 内置 MCP 客户端插件 `@deepseek-ai/dsh-mcp-client`，无需额外安装 DSH 插件。
本项目的 MCP 服务 [src/godot_rag/mcp_server.py](../src/godot_rag/mcp_server.py) 提供 4 个工具：

| 工具 | 作用 |
|---|---|
| `mcp__godot__search_docs` | 检索官方文档，返回标题/面包屑/API 符号/URL/片段 |
| `mcp__godot__ask_docs` | 回答 + `[n]` 引用 |
| `mcp__godot__get_doc` | 按 doc_id 读取整页原文 |
| `mcp__godot__rag_stats` | 索引规模与设备信息 |

## 1. 找到当前 profile 的 patch 文件

DSH 按 profile 隔离配置，patch 层文件位于：

```
$HOME\.dsh\profiles\<profile>\cordis.patch.yml
```

- **以 `$env:DSH_PROFILE` 为准**（在本机 shell 里执行 `echo $env:DSH_PROFILE` 确认；当前为 `desktop`）；
- 桌面客户端与 Web GUI 都可能运行在 `desktop` profile，不要按"桌面/网页"猜 profile；
- 找不到时列出 `$HOME\.dsh\profiles\` 下的目录确认。

## 2. 追加 MCP 配置（stdio，推荐）

先备份，再在文件末尾追加一条 YAML 数组元素（保留原有条目）：

```powershell
$profileName = $env:DSH_PROFILE
if (-not $profileName) { $profileName = "desktop" }
$patch = "$HOME\.dsh\profiles\$profileName\cordis.patch.yml"
Copy-Item $patch "$patch.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"

@'

- id: mcp-godot-rag
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: godot
    transport: stdio
    command: '$PY'
    args: ['-m', 'godot_rag.mcp_server']
    cwd: '<PROJECT_DIR>'
    env:
      PYTHONPATH: '<PROJECT_DIR>\src'
    toolCallTimeoutMs: 120000
'@ | Add-Content -Encoding utf8 $patch
```

说明：

- `serverName: godot` 决定工具名前缀 `mcp__godot__*`；
- `cwd` 必须是项目根目录（索引默认读 `data/index`）；
- 首次工具调用会加载模型（约 5–10 秒），因此把 `toolCallTimeoutMs` 调大到 120 秒；
- 打印/粘贴时注意 YAML 缩进（两空格层级），`@deepseek-ai/dsh-mcp-client` 外层的引号可省略。

## 3. 重载 DSH

保存后需要**重启 DSH**（或等其配置热重载）。启动后在 agent 的工具列表里应出现：

```
mcp__godot__search_docs
mcp__godot__ask_docs
mcp__godot__get_doc
mcp__godot__rag_stats
```

验证：让 agent 执行一次 `mcp__godot__search_docs`（query 用 `add_child`），应返回 Node 类的文档片段。

## 4. 备选：streamable-http 传输

如果希望 MCP 服务常驻、被多个 agent/客户端共享，可先启动 HTTP 传输：

```powershell
cd <PROJECT_DIR>
$env:PYTHONPATH = "<PROJECT_DIR>\src"
& "$PY" -m godot_rag.mcp_server --transport streamable-http --port 8766
```

再把 patch 条目改为：

```yaml
- id: mcp-godot-rag
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: godot
    transport: streamable-http
    url: http://127.0.0.1:8766/mcp
```

## 5. 排错

| 现象 | 原因与处理 |
|---|---|
| 工具不出现 | patch YAML 缩进/语法错误；或未重启 DSH。检查 DSH 日志，或先 `python -m godot_rag.mcp_server --transport streamable-http --port 8766` 手工验证服务能启动 |
| 工具调用报“索引尚未构建” | 先在项目根目录执行 `scripts\run.py build` |
| 调用超时 | 首次调用要加载模型；把 `toolCallTimeoutMs` 设为 120000 |
| 找不到 `godot_rag` | 检查 `env.PYTHONPATH` 是否指向项目 `src`，`cwd` 是否为项目根目录 |
| 想确认服务本身可用 | 本仓库已验证：`tools/list` 返回 4 个工具，`search_docs/ask_docs/get_doc/rag_stats` 均可调用（streamable-http 端到端测试通过） |
