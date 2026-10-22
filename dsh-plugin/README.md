# Godot Docs RAG（DSH 插件）

在 DSH 中提供 4 个原生工具：`godot_search_docs`、`godot_find_symbol`、`godot_get_doc`、`godot_rag_stats`。

## 安装

```
plugin_manager install_bundle(target: <本目录绝对路径>)
```

安装后插件管理列表出现两行：

- **Godot 文档 RAG**（工具，默认启用）
- **Godot 文档 RAG 使用日志**（默认关闭，见 `docs/USAGE_LOG.md`）

## 配置

bundle 的 patch **不携带任何机器路径**。插件按以下优先级解析：

1. 插件行的 `config`（在 profile 的 `~/.dsh/profiles/<profile>/cordis.patch.yml` 覆盖）
2. 环境变量 `GODOT_RAG_PYTHON` / `GODOT_RAG_PROJECT` / `GODOT_RAG_INDEX_DIR`
3. 可移植默认值：`~/.conda/envs/godotrag/...`（python）与仓库根目录（projectDir）
4. 都失败 → `CONFIG_INVALID` 错误，消息内附可粘贴的配置片段

覆盖示例：

```yaml
- id: godot-docs-rag
  name: '@local/godot-docs-rag'
  config:
    pythonExe: '<PYTHON_EXE>'
    projectDir: '<PROJECT_DIR>'
```

## 自检

`godot_rag_stats` 会返回解析后的配置来源与索引状态。若提示 `CONFIG_INVALID`，按错误里的片段补配置后重载插件。
