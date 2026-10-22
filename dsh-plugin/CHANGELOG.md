# Changelog

## 0.1.1（2026-10-22）

- 补全 npm 元数据：`repository` / `homepage` / `bugs` / `author`（npm 页面可跳转到 GitHub）
- 许可证版权人统一为 `XiXizi-Kiara`（仍为 MIT）
- 加固 `projectDir` 推导：从 npm 安装到 `node_modules` 后不再猜测错误目录，改为明确要求配置
- 文档：安装说明改为面向使用者，新增发布清单

## 0.1.0（2026-10-22）

- 首次发布：`godot_search_docs` / `godot_find_symbol` / `godot_get_doc` / `godot_rag_stats` 四个原生工具
- 使用日志行（默认关闭）：记录检索轨迹、注册反馈命令
- 自管 Python 子进程（JSON Lines 协议），首次冷加载约 25–35 秒，后续查询 0.1–0.5 秒
