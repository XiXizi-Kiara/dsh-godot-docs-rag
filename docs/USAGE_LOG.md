# 使用日志与反馈

插件里有一行独立的 **Godot 文档 RAG 使用日志**，默认关闭。它记录真实的检索轨迹与用户反馈，用于改进检索质量、工具描述与评测集。

> `$PY` 指 conda 环境 `godotrag` 的 python；`<PROJECT_DIR>` 指仓库根目录。

## 1. 开关

- 位置：**插件管理列表 → Godot 文档 RAG 使用日志**；
- 默认 `disabled: true`（bundle patch 提供），用户开关会存进 DSH profile，重装/升级不会覆盖；
- 关闭时：不监听事件、不注册反馈命令、不创建/写入任何文件；
- 打开后：出现 `/godot-rag-good`、`/godot-rag-bad` 两个反馈命令。

## 2. 日志位置与轮转

- 默认路径：`<PROJECT_DIR>/logs/rag_usage.jsonl`（`logs/` 已在 `.gitignore`）；
- 可按行配置覆盖：`logPath` / `projectDir`；
- 轮转：超过 `maxBytes`（默认 10 MB）时重命名为 `rag_usage.<timestamp>-<n>.jsonl`，保留 `maxFiles`（默认 5）份（含当前文件）。

## 3. 事件格式（`v: 1`）

### `tool_call`（每次工具调用一行，成功或失败都记）

```json
{"v":1,"kind":"tool_call","ts":"2026-10-22T20:40:12.000Z",
 "session_id":"...","call_id":"...","tool":"godot_search_docs",
 "args":{"k":2,"query":"move_and_slide 不移动","mode":"hybrid"},
 "status":"ok","error_code":null,"latency_ms":15873,
 "result_count":2,
 "results":[{"rank":1,"doc_id":"tutorials/physics/using_character_body_2d",
             "chunk_id":"...","symbol":"","score":0.03}],
 "backend":{"pid":1234,"version":"0.2.0","device":"auto",
            "index_dir":"...","index_mtime":"2026-10-22T13:28:29+08:00","n_chunks":26076,
            "python_exe":"...","project_dir":"...","config_source":{...}}}
```

| 字段 | 说明 |
|---|---|
| `args` | 模型传入的参数 + 解析后的 `query`/`k`/`mode`（`godot_get_doc` 不记录正文，只记 `doc_id/offset/limit`） |
| `status` / `error_code` | `ok` 或 `error`；错误码来自桥接层 |
| `latency_ms` | 工具执行墙钟时间（含首次冷加载） |
| `results` | 最多 20 条，仅 `rank/doc_id/chunk_id/symbol/score`，**不含正文** |
| `backend` | 子进程与索引元信息，便于判断"索引是否重建过" |

### `feedback`（用户命令提交）

```json
{"v":1,"kind":"feedback","ts":"2026-10-22T20:42:00.000Z","session_id":"...",
 "rating":"bad","note":"没命中 queue_free","command":"godot-rag-bad",
 "refs":[{"ts":"2026-10-22T20:40:12.000Z","tool":"godot_search_docs",
          "query":"queue_free","call_id":"..."}]}
```

`refs` 为该会话最近 3 次工具调用（10 分钟窗口内，时间倒序）。注意：缓冲在插件进程内存里，**DSH 重启或插件重载后 refs 会为空**，其余字段不受影响。

## 4. 反馈命令

在 DSH 输入框直接输入：

```
/godot-rag-good
/godot-rag-bad 索引没命中 queue_free
```

命令会在日志里追加一条 `feedback` 事件，并关联最近查询；本会话没有查询记录时只提示、不报错。

## 5. 分析

```powershell
& $PY scripts/analyze_usage.py                          # Markdown 报告
& $PY scripts/analyze_usage.py --json                   # JSON
& $PY scripts/analyze_usage.py --export-qa              # 导出 QA 候选
& $PY scripts/analyze_usage.py --file logs/rag_usage.20261022-1.jsonl --low-score 0.03
```

报告六节：总览、检索质量（零命中/低分）、中文查询、文档热度、工具使用、反馈。

`--export-qa` 生成 `data/processed/qa_candidates.jsonl`：

```json
{"query":"...","expected_doc_ids":["classes/class_node"],"expected_titles":["Node"],
 "source":"good|unrated","ts":"..."}
```

候选需人工筛选后并入 `data/processed/qa.jsonl`；脚本不会自动修改评测集。

建议节奏：每 1–2 周跑一次，把零命中 / bad 反馈转成 3–5 条新 QA 用例。

## 6. 隐私与清空

- 仅本地文件，不联网、不上报；默认关闭，需手动开启；
- 不记录文档正文、AI 回答正文、API key、环境变量；
- `redactQueries: true` 可只记 query 哈希与长度（隐私模式，分析能力下降）；
- 清空：停止 DSH 后删除 `logs/rag_usage*.jsonl`；
- 日志不会进入 `git status`（`logs/` 已忽略）。
