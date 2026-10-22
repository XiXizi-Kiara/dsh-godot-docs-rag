# 运行环境与依赖（2026-10-22 更新）

本项目统一使用 conda 环境 **`godotrag`**（Python 3.12），**不要**使用其他 Python 解释器。

## 解释器

```
C:\Users\Kiara\.conda\envs\godotrag\python.exe
```

## 已安装依赖（实测版本）

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | 3.12.15 | 运行时 |
| torch | **2.11.0+cu128** | GPU 计算（RTX 4060 可用） |
| transformers | 5.20.2 | 模型加载 |
| sentence-transformers | 6.2.0 | BGE embedding / CrossEncoder 精排 |
| faiss-cpu | 1.15.1 | 向量索引（IndexFlatIP） |
| rank_bm25 | 0.2.2 | BM25 词法检索 |
| jieba | 0.42.1 | 中文分词（术语映射补充） |
| fastapi | 0.159.1 | HTTP 服务（默认后端） |
| uvicorn[standard] | 0.47.0 | ASGI 服务器 |
| openai | 3.36.1 | LLM 生成（OpenAI 兼容接口） |
| mcp | 1.30.0 | MCP 服务（stdio / streamable-http），供 agent 集成 |
| tiktoken | 0.14.0 | prompt token 精确计数（数据文件见下） |
| lxml / beautifulsoup4 | 6.1.3 / 4.15.0 | 备用 HTML 解析能力 |
| httpx | 0.28.1 | FastAPI TestClient（仅测试） |
| numpy | 2.5.4 | 基础数值计算 |

完整清单见项目根目录 [requirements.txt](../requirements.txt)。

## 重新安装（在新机器上）

```powershell
# 1) CUDA 版 torch（必须从 PyTorch 官方 index 安装，PyPI 的 Windows torch 是 CPU 版）
pip install --index-url https://download.pytorch.org/whl/cu128 "torch==2.11.0+cu128"

# 2) 其余依赖
pip install -r requirements.txt
```

CPU-only 机器：把第 1 步替换为 `pip install "torch==2.11.0"` 即可，代码会自动回退 CPU。

## GPU 验证

```powershell
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# 预期：2.11.0+cu128 True NVIDIA GeForce RTX 4060 Laptop GPU
```

实测性能：BGE-small 编码约 **250–500 条/秒**（GPU，批量），全量 26076 chunks 约 100 秒；
CrossEncoder 精排 100 条约 **1 秒**（GPU）。CPU 会自动回退，速度约为 GPU 的 1/3。

## tiktoken 数据文件（当前网络无法自动下载）

`tiktoken` 包已安装，但其 `cl100k_base` BPE 数据文件需要首次联网从
`openaipublic.blob.core.windows.net` 下载，当前网络连接被阻断（SSL EOF）。
缺失时 `generate.py` 自动回退为字符估算，**功能不受影响**，只是 prompt 预算精度略低。

如需精确计数，请在你自己的终端执行以下命令（任一即可）：

```powershell
# 方式 A：让 tiktoken 自行下载（你的网络若可达）
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
```

```powershell
# 方式 B：手动下载后放入 tiktoken 缓存目录
# 缓存文件名 = sha1(URL)，本文件对应：9b5ad71b2ce5302211f9c61530b329a4922fc6a4
$cacheDir = Join-Path $env:TEMP "data-gym-cache"
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
Invoke-WebRequest -Uri "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken" `
  -OutFile (Join-Path $cacheDir "9b5ad71b2ce5302211f9c61530b329a4922fc6a4")
& "C:\Users\Kiara\.conda\envs\godotrag\python.exe" -c "import tiktoken; print(len(tiktoken.get_encoding('cl100k_base').encode('test')))"
```

执行成功后 `generate.py` 会自动使用 tiktoken，无需改代码。

## 常用命令

```powershell
$PY = "C:\Users\Kiara\.conda\envs\godotrag\python.exe"
cd C:\Users\Kiara\Desktop\GodotRag

& $PY scripts\run.py build                    # 重建索引（faiss + BM25）
& $PY scripts\run.py search "add_child" --json
& $PY scripts\run.py ask "queue_free 和 free 区别" --json
& $PY scripts\run.py serve --port 8765        # FastAPI（默认）
& $PY scripts\run.py serve --port 8765 --backend stdlib
& $PY scripts\run.py mcp                      # MCP 服务（stdio，agent 集成）
& $PY scripts\run.py mcp --transport streamable-http --port 8766
& $PY scripts\run_tests.py -v                 # 122 tests
& $PY scripts\fetch_models.py --rerank        # 下载精排模型（ModelScope 优先）
```

## 备注

- `pip` 在本会话环境已可正常使用（之前的临时目录限制已解除），但**不需要**再安装新依赖。
- 模型文件在 `models/`，离线加载；下载脚本优先走 ModelScope（hf-mirror 作回退）。
- 构建产物在 `data/index/`，删除后可随时用 `build --force` 重建。
