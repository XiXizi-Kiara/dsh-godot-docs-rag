# Godot RAG 检索评测报告

- 生成时间：2026-10-22T12:37:51+08:00
- QA 数量：40
- 检索深度：top-10；PageHit 窗口：top-5
- 指标口径：Recall@k = top-k 中命中任一 gold 文档；MRR@10 = 首条 gold 文档排名倒数；
  PageHit@5 = top-5 命中 gold 文档，或结果正文包含任一 gold_term（大小写不敏感）。

## 总览

| 配置 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 | 平均延迟(ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid | 0.5750 | 0.8500 | 0.9500 | 0.6940 | 0.9500 | 165.80 |
| hybrid+rerank | 0.6500 | 0.8000 | 0.9000 | 0.7278 | 0.9750 | 28228.58 |

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

## 按语言（hybrid+rerank）

| 语言 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 28 | 0.6786 | 0.8571 | 0.9286 | 0.7614 | 1.0000 |
| zh | 12 | 0.5833 | 0.6667 | 0.8333 | 0.6493 | 0.9167 |

## 按文档类型（hybrid+rerank）

| 类型 | 题数 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | PageHit@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class | 20 | 0.7000 | 0.8000 | 0.9000 | 0.7639 | 0.9500 |
| engine_details | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| getting_started | 4 | 0.7500 | 1.0000 | 1.0000 | 0.8333 | 1.0000 |
| troubleshooting | 2 | 0.5000 | 1.0000 | 1.0000 | 0.7500 | 1.0000 |
| tutorial | 12 | 0.5000 | 0.6667 | 0.8333 | 0.5833 | 1.0000 |

## 失败案例

### hybrid（2 条）

- **q009** [zh/class] 怎么用 Tween 把某个节点的 position 在 1 秒内平滑移动到目标值？
  - gold：`classes/class_tween`
  - top-3：#1 `getting_started/step_by_step/scripting_first_script`，#2 `tutorials/xr/xr_room_scale`，#3 `classes/class_graphedit`
- **q038** [zh/engine_details] 在 C++ 里自定义一个新的 Object 子类并注册到引擎要怎么写？
  - gold：`engine_details/architecture/object_class`
  - top-3：#1 `engine_details/engine_api/custom_modules_in_cpp`，#2 `engine_details/engine_api/custom_modules_in_cpp`，#3 `classes/class_engine`

### hybrid+rerank（1 条）

- **q009** [zh/class] 怎么用 Tween 把某个节点的 position 在 1 秒内平滑移动到目标值？
  - gold：`classes/class_tween`
  - top-3：#1 `classes/class_visualshadernodesmoothstep`，#2 `getting_started/step_by_step/scripting_first_script`，#3 `tutorials/math/interpolation`
