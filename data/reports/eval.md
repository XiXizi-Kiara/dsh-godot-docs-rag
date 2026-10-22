# Godot RAG 检索评测报告

- 生成时间：2026-10-22T12:57:30+08:00
- QA 数量：40
- 检索深度：top-10；PageHit 窗口：top-5
- 指标口径：Recall@k = top-k 中命中任一 gold 文档；MRR@10 = 首条 gold 文档排名倒数；
  PageHit@5 = top-5 命中 gold 文档，或结果正文包含任一 gold_term（大小写不敏感）。

## 总览

| 配置 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 | 平均延迟(ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid | 0.5750 | 0.8500 | 0.9500 | 0.6940 | 0.9500 | 181.95 |
| dense | 0.5000 | 0.8500 | 0.9250 | 0.6450 | 0.9250 | 42.46 |
| bm25 | 0.4750 | 0.7000 | 0.9000 | 0.5896 | 0.9500 | 46.45 |

## 按语言（hybrid）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.6071 | 0.8571 | 1.0000 | 0.7295 | 1.0000 |
| zh | 12 | 0.5000 | 0.8333 | 0.8333 | 0.6111 | 0.8333 |

## 按文档类型（hybrid）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.7000 | 0.9000 | 0.9500 | 0.7692 | 0.9500 |
| engine_details | 2 | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| getting_started | 4 | 0.5000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 |
| troubleshooting | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tutorial | 12 | 0.3333 | 0.7500 | 1.0000 | 0.5312 | 1.0000 |

## 按语言（dense）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.6071 | 0.8929 | 0.9643 | 0.7277 | 0.9643 |
| zh | 12 | 0.2500 | 0.7500 | 0.8333 | 0.4522 | 0.8333 |

## 按文档类型（dense）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.7000 | 0.8500 | 0.8500 | 0.7542 | 0.9500 |
| engine_details | 2 | 0.5000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 |
| getting_started | 4 | 0.0000 | 1.0000 | 1.0000 | 0.4583 | 1.0000 |
| troubleshooting | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tutorial | 12 | 0.2500 | 0.7500 | 1.0000 | 0.4487 | 0.8333 |

## 按语言（bm25）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.5357 | 0.7500 | 0.9643 | 0.6516 | 0.9643 |
| zh | 12 | 0.3333 | 0.5833 | 0.7500 | 0.4448 | 0.9167 |

## 按文档类型（bm25）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.5000 | 0.8000 | 0.9500 | 0.6544 | 1.0000 |
| engine_details | 2 | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| getting_started | 4 | 0.5000 | 0.7500 | 1.0000 | 0.5917 | 1.0000 |
| troubleshooting | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tutorial | 12 | 0.3333 | 0.5000 | 0.8333 | 0.4274 | 0.9167 |

## 失败案例

### hybrid（2 条）

- **q009** [zh/class] 怎么用 Tween 把某个节点的 position 在 1 秒内平滑移动到目标值？
  - gold：`classes/class_tween`
  - top-3：#1 `getting_started/step_by_step/scripting_first_script`，#2 `tutorials/xr/xr_room_scale`，#3 `classes/class_graphedit`
- **q038** [zh/engine_details] 在 C++ 里自定义一个新的 Object 子类并注册到引擎要怎么写？
  - gold：`engine_details/architecture/object_class`
  - top-3：#1 `engine_details/engine_api/custom_modules_in_cpp`，#2 `engine_details/engine_api/custom_modules_in_cpp`，#3 `classes/class_engine`

### dense（3 条）

- **q009** [zh/class] 怎么用 Tween 把某个节点的 position 在 1 秒内平滑移动到目标值？
  - gold：`classes/class_tween`
  - top-3：#1 `tutorials/math/interpolation`，#2 `classes/class_@globalscope`，#3 `getting_started/step_by_step/scripting_first_script`
- **q026** [en/tutorial] How do I expose a variable so it can be edited in the Inspector?
  - gold：`tutorials/scripting/gdscript/gdscript_exports`
  - top-3：#1 `tutorials/plugins/editor/inspector_plugins`，#2 `classes/class_editorinspector`，#3 `tutorials/plugins/editor/inspector_plugins`
- **q027** [zh/tutorial] 怎么在场景之间共享玩家分数这类全局数据？
  - gold：`tutorials/scripting/singletons_autoload`
  - top-3：#1 `tutorials/scripting/c_sharp/c_sharp_global_classes`，#2 `about/list_of_features`，#3 `classes/class_projectsettings`

### bm25（2 条）

- **q026** [en/tutorial] How do I expose a variable so it can be edited in the Inspector?
  - gold：`tutorials/scripting/gdscript/gdscript_exports`
  - top-3：#1 `classes/class_editorinspector`，#2 `getting_started/introduction/index`，#3 `classes/class_visualshadernodeparameter`
- **q038** [zh/engine_details] 在 C++ 里自定义一个新的 Object 子类并注册到引擎要怎么写？
  - gold：`engine_details/architecture/object_class`
  - top-3：#1 `engine_details/engine_api/gdextension/gdextension_c_example`，#2 `engine_details/engine_api/gdextension/gdextension_c_example`，#3 `classes/class_engine`
