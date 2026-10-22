"""嵌入与重排序模型封装（BGE small / BGE reranker），CPU/GPU 双后端。

后端选择（`device` 参数或环境变量 GODOT_RAG_DEVICE）：

- `auto`（默认）：本进程 torch 有 CUDA -> 本进程 GPU；
  否则若探测到带 CUDA 的 Python 解释器（GODOT_RAG_GPU_PYTHON / ~/.conda/envs/pytorch）
  -> 批量嵌入委托给该解释器的子进程（外部 GPU 后端）；
  再否则 -> 本进程 CPU。
- `cuda`：强制 GPU（本进程或外部解释器）。
- `cpu`：强制本进程 CPU。

查询编码始终使用本进程 CPU（单条耗时几十毫秒，避免每查询启动子进程）；
索引构建等批量任务才使用 GPU 后端。向量都经过 CLS pooling + L2 归一化。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import torch
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

from . import config

_CACHE_DIR = config.DATA_DIR / ".cache" / "embed"


def _pick_device(device: str | None) -> str:
    if device and device != "auto":
        return device
    return "cuda" if torch.cuda.is_available() else "cpu"


class Encoder:
    """BGE embedding 编码器（CPU / GPU / 外部 GPU 子进程）。"""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        device: str | None = None,
        batch_size: int = 64,
        max_length: int = 512,
    ) -> None:
        self.model_dir = Path(model_dir or config.EMBED_MODEL_DIR)
        self.requested = (device or config.DEVICE or "auto").lower()
        self.batch_size = batch_size
        self.max_length = max_length
        self._model = None
        self._tok = None
        self.backend = self._resolve_backend()
        print(f"[embed] 后端={self.backend}（请求 device={self.requested}）", file=sys.stderr)

    # -- 后端解析 ---------------------------------------------------------
    def _resolve_backend(self) -> str:
        if self.requested == "cpu":
            return "local_cpu"
        if self.requested == "cuda":
            if torch.cuda.is_available():
                return "local_cuda"
            if config.GPU_PYTHON_PATH:
                return "external_cuda"
            raise RuntimeError(
                "device=cuda，但当前解释器 torch 无 CUDA，且未找到外部 GPU 解释器。"
                "请设置 GODOT_RAG_GPU_PYTHON 或改用 device=cpu。"
            )
        # auto
        if torch.cuda.is_available():
            return "local_cuda"
        if config.GPU_PYTHON_PATH:
            return "external_cuda"
        return "local_cpu"

    @property
    def device(self) -> str:
        """用于监控/元信息的设备描述。"""
        return {
            "local_cuda": "cuda",
            "external_cuda": "cuda(external)",
            "local_cpu": "cpu",
        }.get(self.backend, self.backend)

    # -- 本地模型 ---------------------------------------------------------
    def _load(self) -> None:
        if self._model is not None:
            return
        if not (self.model_dir / "config.json").exists():
            raise RuntimeError(
                f"embedding 模型缺失：{self.model_dir}\n"
                "请先运行：python scripts/fetch_models.py（走 hf-mirror 下载）"
            )
        t0 = time.time()
        dev = "cuda" if self.backend == "local_cuda" else "cpu"
        self._tok = AutoTokenizer.from_pretrained(str(self.model_dir))
        self._model = AutoModel.from_pretrained(str(self.model_dir)).to(dev).eval()
        print(
            f"[embed] 本进程模型加载完成 device={dev} ({time.time() - t0:.1f}s)",
            file=sys.stderr,
        )

    def _encode_local(
        self,
        texts: list[str],
        is_query: bool = False,
        batch_size: int | None = None,
        show_progress: bool = True,
    ) -> np.ndarray:
        self._load()
        dev = "cuda" if self.backend == "local_cuda" else "cpu"
        if is_query:
            texts = [config.QUERY_INSTRUCTION + t for t in texts]
        bs = batch_size or self.batch_size
        n = len(texts)
        if n == 0:
            return np.zeros((0, config.EMBED_DIM), dtype=np.float32)
        out = np.zeros((n, config.EMBED_DIM), dtype=np.float32)
        # 按长度排序分批，减少 padding 浪费
        order = sorted(range(n), key=lambda i: len(texts[i]))
        t0 = time.time()
        for start in range(0, n, bs):
            idxs = order[start : start + bs]
            batch = [texts[i] for i in idxs]
            enc = self._tok(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            ).to(dev)
            with torch.inference_mode():
                hidden = self._model(**enc).last_hidden_state[:, 0]
                vec = torch.nn.functional.normalize(hidden, p=2, dim=-1)
            out[idxs] = vec.cpu().numpy().astype(np.float32)
            if show_progress:
                done = min(start + bs, n)
                speed = done / max(time.time() - t0, 1e-6)
                print(
                    f"\r[embed] {done}/{n} ({done * 100 // n}%) {speed:.0f} 条/s",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
        if show_progress:
            print(file=sys.stderr)
        return out

    # -- 外部 GPU 子进程 --------------------------------------------------
    def _encode_external(
        self,
        texts: list[str],
        is_query: bool = False,
    ) -> np.ndarray:
        py = config.GPU_PYTHON_PATH
        if py is None or not Path(py).exists():
            raise RuntimeError("外部 GPU 解释器不存在")
        worker = config.ROOT / "scripts" / "embed_worker.py"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tag = f"{os.getpid()}_{time.time_ns()}"
        inp = _CACHE_DIR / f"in_{tag}.jsonl"
        out = _CACHE_DIR / f"out_{tag}.npy"
        try:
            with open(inp, "w", encoding="utf-8") as f:
                for t in texts:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
            cmd = [
                str(py), str(worker),
                "--input", str(inp),
                "--output", str(out),
                "--model", str(self.model_dir),
                "--device", "cuda",
                "--batch-size", str(self.batch_size),
                "--max-length", str(self.max_length),
            ]
            if is_query:
                cmd += ["--query-instruction", config.QUERY_INSTRUCTION]
            print(
                f"[embed] 外部 GPU 后端：{len(texts)} 条 -> {py.name}",
                file=sys.stderr,
                flush=True,
            )
            proc = subprocess.run(cmd, timeout=None)
            if proc.returncode != 0:
                raise RuntimeError(f"GPU worker 退出码 {proc.returncode}")
            return np.load(out)
        finally:
            for p in (inp, out):
                try:
                    p.unlink()
                except OSError:
                    pass

    # -- 对外接口 ---------------------------------------------------------
    def encode(
        self,
        texts: list[str],
        is_query: bool = False,
        batch_size: int | None = None,
        show_progress: bool = True,
    ) -> np.ndarray:
        if not texts:
            return np.zeros((0, config.EMBED_DIM), dtype=np.float32)
        if self.backend == "external_cuda" and not is_query:
            try:
                return self._encode_external(texts, is_query=False)
            except Exception as e:  # 外部后端不可用时降级
                print(f"[embed] 外部 GPU 后端失败，降级 CPU：{e}", file=sys.stderr)
                self.backend = "local_cpu"
        return self._encode_local(texts, is_query, batch_size, show_progress)

    def encode_query(self, query: str) -> np.ndarray:
        return self._encode_local([query], is_query=True, show_progress=False)[0]


class Reranker:
    """BGE cross-encoder 重排序器；模型缺失时 available() 为 False。"""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        device: str | None = None,
        batch_size: int = 16,
        max_length: int = 512,
    ) -> None:
        self.model_dir = Path(model_dir or config.RERANK_MODEL_DIR)
        self.requested = (device or config.DEVICE or "auto").lower()
        self.device = _pick_device(self.requested)
        self.batch_size = batch_size
        self.max_length = max_length
        self._model = None
        self._tok = None

    def available(self) -> bool:
        return (self.model_dir / "config.json").exists()

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.available():
            raise RuntimeError(f"reranker 模型缺失：{self.model_dir}")
        self._tok = AutoTokenizer.from_pretrained(str(self.model_dir))
        self._model = (
            AutoModelForSequenceClassification.from_pretrained(str(self.model_dir))
            .to(self.device)
            .eval()
        )
        print(f"[rerank] 模型加载完成 device={self.device}", file=sys.stderr)

    def score(self, query: str, passages: list[str]) -> np.ndarray:
        if not passages:
            return np.zeros((0,), dtype=np.float32)
        self._load()
        scores: list[np.ndarray] = []
        for start in range(0, len(passages), self.batch_size):
            batch = passages[start : start + self.batch_size]
            enc = self._tok(
                [[query, p] for p in batch],
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            ).to(self.device)
            with torch.inference_mode():
                logits = self._model(**enc).logits.view(-1)
                sig = torch.sigmoid(logits)
            scores.append(sig.cpu().numpy().astype(np.float32))
        return np.concatenate(scores)
