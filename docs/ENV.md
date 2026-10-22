# 运行环境约定（重要）

本项目**统一使用 conda 环境 `godotrag`**，不要使用默认的 `python`（那是另一个
`pytorch` conda 环境，与本项目无关）。

## 解释器

```
C:\Users\Kiara\.conda\envs\godotrag\python.exe
```

已验证版本（2026-10-22）：

| 组件 | 版本 |
|---|---|
| Python | 3.12.15 |
| torch | 2.14.1 **CPU 版**（不启用 CUDA） |
| transformers | 5.20.2 |
| numpy | 2.5.4 |
| tokenizers / safetensors / tqdm | 0.23.3 / 0.8.0 / 4.70.1 |

- CPU 编码（bge-small-en-v1.5，14 线程）实测约 **185 条/秒**，全量约 4 分钟，可接受。
- 不安装任何新 pip 包：本项目只用标准库 + 上述已装库。
- 模型文件在 `models/` 下，离线加载（`HF_HUB_OFFLINE=1` 亦可）。

## 常用命令

```powershell
# 构建索引（解析 -> 分块 -> 嵌入 -> BM25）
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run.py build

# 试跑（只处理前 200 篇，写入 data/index_smoke）
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run.py build --limit 200

# 检索 / 问答
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run.py search "how to free a node" --json
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run.py ask "queue_free 和 free 有什么区别" --json

# Web 服务
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run.py serve --port 8765

# 测试（不使用真实索引、不联网）
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" scripts\run_tests.py -v
```

`scripts\run.py` 等价于把 `src` 加入 `PYTHONPATH` 后执行 `python -m godot_rag`。
也可以手动设置：

```powershell
$env:PYTHONPATH = "src"
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" -m godot_rag search "signals" --json
```

## 沙箱限制（已知）

- `pip install` 在 DSH 沙箱下会被拒绝（临时目录权限），因此本项目**零第三方新增依赖**是硬约束；
  也无法给 godotrag 环境安装 CUDA 版 torch（pytorch wheel 源在该网络下不可达）。
- `conda` 命令本身在此沙箱下也可能报 multiprocessing 权限错误；直接调用上面的 python
  绝对路径即可，不需要 `conda activate`。

## CPU / GPU 双支持

`embed.Encoder` 支持三种后端，通过 `device` 参数或环境变量 `GODOT_RAG_DEVICE` 控制：

| 取值 | 行为 |
|---|---|
| `auto`（默认） | 本进程 torch 有 CUDA → 本进程 GPU；否则若找到带 CUDA 的 Python 解释器 → 批量嵌入委托给其子进程（外部 GPU 后端）；否则 CPU |
| `cuda` | 强制 GPU（本进程或外部解释器），都不可用时明确报错 |
| `cpu` | 强制本进程 CPU |

- 外部 GPU 解释器默认探测 `~/.conda/envs/pytorch/python.exe`，可用 `GODOT_RAG_GPU_PYTHON` 指定。
- 实测：`bge-small-en-v1.5` 在 RTX 4060 上约 **560 条/秒**（全量 26076 chunks 约 90 秒）；
  纯 CPU（14 线程）约 **180 条/秒**（全量约 4–6 分钟）。
- GPU 与 CPU 生成的向量一致性已验证：余弦相似度 1.000000（最大分量误差 ~1e-7），
  因此"GPU 建索引、CPU 查询"的混合模式安全。
- 查询编码始终走本进程 CPU（单条约 50 ms），避免每次查询启动子进程的开销。
- LLM 生成（可选）通过环境变量配置，不依赖本地 GPU：

```powershell
$env:GODOT_RAG_LLM_API_KEY  = "sk-..."
$env:GODOT_RAG_LLM_BASE_URL = "https://api.deepseek.com/v1"   # 默认
$env:GODOT_RAG_LLM_MODEL    = "deepseek-chat"                  # 默认
```
