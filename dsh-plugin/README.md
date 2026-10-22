# Godot Docs RAG（DSH 插件）

在 DSH 中提供 4 个原生工具：`godot_search_docs`、`godot_find_symbol`、`godot_get_doc`、`godot_rag_stats`。

## 安装

**从 npm（发布后）**：

```
dsh plugin --profile <profile> add @xixizi-kiara/dsh-godot-docs-rag
# 或在 DSH 里：
plugin_manager install_bundle(target: "@xixizi-kiara/dsh-godot-docs-rag@0.1.0")
```

桌面版 DSH 的 profile 由应用自身管理，请在 **Settings → Plugins** 里按包名安装，或使用上面的 `plugin_manager` 工具。

**本地开发（未发布 / 改代码时）**：指向本目录绝对路径。

```
plugin_manager install_bundle(target: "<本目录绝对路径>")
```

安装后插件管理列表出现两行：

- **Godot 文档 RAG**（工具，默认启用）
- **Godot 文档 RAG 使用日志**（默认关闭）

## 运行前提

插件只是本机 Python RAG 的壳：需要 conda 环境 `godotrag`、模型文件与构建好的索引（见仓库 `docs/DSH_PLUGIN_SETUP.md`）。

## 配置

bundle 的 patch **不携带任何机器路径**。插件按以下优先级解析：

1. 插件行的 `config`（profile 的 `~/.dsh/profiles/<profile>/cordis.patch.yml` 覆盖）
2. 环境变量 `GODOT_RAG_PYTHON` / `GODOT_RAG_PROJECT` / `GODOT_RAG_INDEX_DIR`
3. 可移植默认值：`~/.conda/envs/godotrag/...`（python）；`projectDir` 只在插件位于 GodotRag 仓库内时才可自动推导
4. 都失败 → `CONFIG_INVALID` 错误，消息内附可粘贴的配置片段

> **从 npm 安装时必须显式配置 `projectDir`**（或设置 `GODOT_RAG_PROJECT`），因为插件位于 profile 的 `node_modules`，无法推导你的仓库位置。

覆盖示例：

```yaml
- id: godot-docs-rag
  name: '@xixizi-kiara/dsh-godot-docs-rag'
  config:
    pythonExe: '<PYTHON_EXE>'
    projectDir: '<PROJECT_DIR>'
```

## 自检

`godot_rag_stats` 会返回解析后的配置来源与索引状态。若提示 `CONFIG_INVALID`，按错误里的片段补配置后重载插件。
