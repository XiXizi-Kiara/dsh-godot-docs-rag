"""外部 GPU 嵌入 worker。

供 embed.Encoder 在"当前进程没有 CUDA、但存在带 CUDA 的 Python 解释器"时调用。
协议：读取 jsonl（每行一个待编码字符串），写出 float32 归一化向量 .npy。

用法示例：
    python embed_worker.py --input in.jsonl --output out.npy \
        --model models/bge-small-en-v1.5 --device cuda --batch-size 64
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--max-length", type=int, default=512)
    ap.add_argument("--query-instruction", default="")
    args = ap.parse_args(argv)

    with open(args.input, "r", encoding="utf-8") as f:
        texts = [json.loads(line) for line in f]
    if args.query_instruction:
        texts = [args.query_instruction + t for t in texts]
    n = len(texts)
    if n == 0:
        np.save(args.output, np.zeros((0, 384), dtype=np.float32))
        return 0

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        print("[embed-worker] 当前解释器无 CUDA，改用 CPU", file=sys.stderr)
        device = "cpu"
    torch.set_num_threads(max(1, os.cpu_count() or 1))

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModel.from_pretrained(args.model).to(device).eval()

    # 按长度排序分批，减少 padding 浪费（输出顺序在最后还原）
    order = sorted(range(n), key=lambda i: len(texts[i]))
    out: np.ndarray | None = None
    bs = args.batch_size
    t0 = time.time()
    for start in range(0, n, bs):
        idxs = order[start : start + bs]
        batch = [texts[i] for i in idxs]
        enc = tok(
            batch,
            padding=True,
            truncation=True,
            max_length=args.max_length,
            return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            hidden = model(**enc).last_hidden_state[:, 0]
            vec = torch.nn.functional.normalize(hidden, p=2, dim=-1)
        arr = vec.cpu().numpy().astype(np.float32)
        if out is None:
            out = np.zeros((n, arr.shape[1]), dtype=np.float32)
        out[idxs] = arr
        done = min(start + bs, n)
        speed = done / max(time.time() - t0, 1e-6)
        print(
            f"\r[embed-worker] {done}/{n} ({done * 100 // n}%) {speed:.0f} 条/s device={device}",
            end="",
            file=sys.stderr,
            flush=True,
        )
    print(file=sys.stderr)
    np.save(args.output, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
