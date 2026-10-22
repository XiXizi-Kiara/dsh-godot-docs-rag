# 发布清单（@xixizi-kiara/dsh-godot-docs-rag）

面向维护者：把 `dsh-plugin/` 发布为可通过 `dsh plugin add` / 插件管理器安装的 npm 包。

## 0. 一次性准备

- npm 账号需拥有 `@kiara` scope（创建名为 `kiara` 的 npm 用户或组织；scope 必须小写）；
- 建议先建独立仓库（或使用当前仓库的 `dsh-plugin/` 子目录发布）；`package.json` 的 `repository` 可后补；
- 已添加 `LICENSE`（MIT）。

## 1. 发布前检查

```powershell
cd dsh-plugin
node --check index.js
for file in test/*.test.js; do node "$file" || exit 1; done
npm pack --dry-run        # 确认 files 列表包含 index.js/lib/usage-log/locale/patch/LICENSE
```

确认 `package.json` 中：

- `name` = `@xixizi-kiara/dsh-godot-docs-rag`、`version` 递增；
- 没有 `private: true`；
- 有 `license`、`engines.dsh`、`keywords`、`dsh.bundle.patch`。

## 2. 发布

```powershell
npm login
npm publish --access public
```

> 网络受限时可配置镜像；发布到镜像需该镜像支持写操作（通常需要私有 registry）。

## 3. 验证安装

```powershell
# CLI 管理的 profile（例如 web）
dsh plugin --profile web add @xixizi-kiara/dsh-godot-docs-rag

# 或在 DSH 内（桌面版用 Settings → Plugins）
plugin_manager install_bundle(target: "@xixizi-kiara/dsh-godot-docs-rag@0.1.1")
```

安装后配置 `projectDir` / `pythonExe`（见 docs/DSH_PLUGIN_SETUP.md），再调用 `godot_rag_stats` 自检。

## 4. 上架社区市场（可选）

dshmarket 的目录来自精选仓库 [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)：
向该仓库提 PR 加一条插件条目（不要向 `dshmarket` 仓库提），站点与市场通常一天内自动收录。

## 5. 升级

1. 修改代码，递增 `version`；
2. `npm publish`；
3. 用户侧 `dsh plugin add @xixizi-kiara/dsh-godot-docs-rag@<version>`，或在市场里点更新。

## 版本兼容

`engines.dsh` 当前为 `>=0.2.1-rc.2 <0.3.0`。DSH 升级到新 minor 前先验证工具注册与配置解析，再放宽范围并发布。

## 已知限制

- 首次从 npm 安装后，`projectDir` 无法自动推导，必须显式配置；
- 用户仍需自备 conda 环境、模型与索引；
- DSH Desktop 的 profile 由应用管理，应用运行期间 CLI 不修改它。
