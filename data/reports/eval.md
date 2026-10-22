# Godot RAG 检索评测报告

- 生成时间：2026-10-22T13:34:03+08:00
- QA 数量：40
- 检索深度：top-10；PageHit 窗口：top-5
- 指标口径：Recall@k = top-k 中命中任一 gold 文档；MRR@10 = 首条 gold 文档排名倒数；
  PageHit@5 = top-5 命中 gold 文档，或结果正文包含任一 gold_term（大小写不敏感）。

## 总览

| 配置 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 | 平均延迟(ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid | 0.5250 | 0.8750 | 0.9250 | 0.6447 | 0.9750 | 532.14 |
| dense | 0.5000 | 0.8500 | 0.9250 | 0.6440 | 0.9000 | 28.00 |
| bm25 | 0.5000 | 0.7500 | 0.8750 | 0.6026 | 0.9000 | 124.64 |
| hybrid+rerank | 0.6500 | 0.8000 | 0.9250 | 0.7250 | 0.9750 | 1352.43 |

## 按语言（hybrid）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.6071 | 0.9286 | 0.9286 | 0.7077 | 1.0000 |
| zh | 12 | 0.3333 | 0.7500 | 0.9167 | 0.4975 | 0.9167 |

## 按文档类型（hybrid）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.7000 | 0.8500 | 0.9000 | 0.7454 | 0.9500 |
| engine_details | 2 | 0.5000 | 1.0000 | 1.0000 | 0.6000 | 1.0000 |
| getting_started | 4 | 0.5000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 |
| troubleshooting | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tutorial | 12 | 0.1667 | 0.8333 | 0.9167 | 0.3898 | 1.0000 |

## 按语言（dense）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.6071 | 0.8929 | 0.9643 | 0.7277 | 0.9643 |
| zh | 12 | 0.2500 | 0.7500 | 0.8333 | 0.4486 | 0.7500 |

## 按文档类型（dense）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.7000 | 0.8500 | 0.9000 | 0.7592 | 0.9000 |
| engine_details | 2 | 0.5000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 |
| getting_started | 4 | 0.0000 | 1.0000 | 1.0000 | 0.4583 | 1.0000 |
| troubleshooting | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tutorial | 12 | 0.2500 | 0.7500 | 0.9167 | 0.4368 | 0.8333 |

## 按语言（bm25）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.5357 | 0.8214 | 0.9286 | 0.6381 | 0.9286 |
| zh | 12 | 0.4167 | 0.5833 | 0.7500 | 0.5197 | 0.8333 |

## 按文档类型（bm25）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.5000 | 0.8500 | 0.9000 | 0.6450 | 0.9500 |
| engine_details | 2 | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| getting_started | 4 | 0.5000 | 1.0000 | 1.0000 | 0.6125 | 1.0000 |
| troubleshooting | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tutorial | 12 | 0.4167 | 0.5000 | 0.8333 | 0.4795 | 0.8333 |

## 按语言（hybrid+rerank）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.6786 | 0.8929 | 0.9643 | 0.7710 | 1.0000 |
| zh | 12 | 0.5833 | 0.5833 | 0.8333 | 0.6176 | 0.9167 |

## 按文档类型（hybrid+rerank）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.7000 | 0.8000 | 0.9000 | 0.7618 | 0.9500 |
| engine_details | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| getting_started | 4 | 0.7500 | 1.0000 | 1.0000 | 0.8333 | 1.0000 |
| troubleshooting | 2 | 0.5000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 |
| tutorial | 12 | 0.5000 | 0.6667 | 0.9167 | 0.5774 | 1.0000 |

## 失败案例

### hybrid（1 条）

- **q009** [zh/class] 怎么用 Tween 把某个节点的 position 在 1 秒内平滑移动到目标值？
  - gold：`classes/class_tween`
  - top-3：#1 `getting_started/step_by_step/scripting_first_script`，#2 `classes/class_node2d`，#3 `classes/class_graphedit`

### dense（4 条）

- **q003** [zh/class] 场景里怎么用节点路径获取子节点？如果路径写错会报什么错？
  - gold：`classes/class_node`
  - top-3：#1 `classes/class_nodepath`，#2 `engine_details/file_formats/tscn`，#3 `engine_details/file_formats/tscn`
- **q009** [zh/class] 怎么用 Tween 把某个节点的 position 在 1 秒内平滑移动到目标值？
  - gold：`classes/class_tween`
  - top-3：#1 `tutorials/math/interpolation`，#2 `classes/class_@globalscope`，#3 `getting_started/step_by_step/scripting_first_script`
- **q026** [en/tutorial] How do I expose a variable so it can be edited in the Inspector?
  - gold：`tutorials/scripting/gdscript/gdscript_exports`
  - top-3：#1 `tutorials/plugins/editor/inspector_plugins`，#2 `classes/class_editorinspector`，#3 `tutorials/plugins/editor/inspector_plugins`
- **q027** [zh/tutorial] 怎么在场景之间共享玩家分数这类全局数据？
  - gold：`tutorials/scripting/singletons_autoload`
  - top-3：#1 `tutorials/scripting/c_sharp/c_sharp_global_classes`，#2 `classes/class_projectsettings`，#3 `classes/class_renderingserver`

### bm25（4 条）

- **q016** [en/class] How do I switch to another scene file at runtime and how do I quit the game from code?
  - gold：`classes/class_scenetree`
  - top-3：#1 `getting_started/introduction/index`，#2 `community/asset_store/faq`，#3 `tutorials/shaders/shader_reference/shading_language`
- **q026** [en/tutorial] How do I expose a variable so it can be edited in the Inspector?
  - gold：`tutorials/scripting/gdscript/gdscript_exports`
  - top-3：#1 `getting_started/introduction/index`，#2 `classes/class_editorinspector`，#3 `classes/class_editorinspector`
- **q027** [zh/tutorial] 怎么在场景之间共享玩家分数这类全局数据？
  - gold：`tutorials/scripting/singletons_autoload`
  - top-3：#1 `classes/class_configfile`，#2 `tutorials/export/exporting_for_dedicated_servers`，#3 `classes/class_editorinterface`
- **q038** [zh/engine_details] 在 C++ 里自定义一个新的 Object 子类并注册到引擎要怎么写？
  - gold：`engine_details/architecture/object_class`
  - top-3：#1 `engine_details/engine_api/custom_modules_in_cpp`，#2 `classes/class_engine`，#3 `engine_details/engine_api/gdextension/gdextension_c_example`

### hybrid+rerank（1 条）

- **q009** [zh/class] 怎么用 Tween 把某个节点的 position 在 1 秒内平滑移动到目标值？
  - gold：`classes/class_tween`
  - top-3：#1 `tutorials/xr/basic_xr_locomotion`，#2 `classes/class_projectsettings`，#3 `classes/class_visualshadernodesmoothstep`
