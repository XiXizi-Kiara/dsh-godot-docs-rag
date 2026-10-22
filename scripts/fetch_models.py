"""下载所需模型到 models/ 目录。

网络环境说明：
- huggingface.co 不可达；hf-mirror.com 可用但大文件偶尔超时；
- ModelScope API 稳定可用，本脚本优先用 ModelScope 直连下载，失败再回退 hf-mirror。

用法（项目根目录）：
    C:\\Users\\Kiara\\.conda\\envs\\godotrag\\python.exe scripts\\fetch_models.py
    ... --rerank        # 额外下载 bge-reranker-base
    ... --check         # 只检查本地是否齐全
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
UA = {"User-Agent": "Mozilla/5.0 (godot-rag-fetch)"}
MS_BASE = "https://www.modelscope.cn/api/v1/models"

TARGETS = {
    "embed": ("BAAI/bge-small-en-v1.5", MODELS / "bge-small-en-v1.5",
              ["*.json", "*.txt", "*.safetensors", "*.model"]),
    "rerank": ("BAAI/bge-reranker-base", MODELS / "bge-reranker-base",
               ["*.json", "*.txt", "*.safetensors", "*.model"]),
}

# 不必下载的大文件
_SKIP_SUFFIXES = (".bin", ".onnx", ".h5", ".msgpack", ".pt", ".ckpt")
_SKIP_PARTS = ("onnx/", "onnx_", ".git", "README")


def _ready(path: Path) -> bool:
    if not path.is_dir():
        return False
    names = {p.name for p in path.iterdir()}
    return bool({"config.json", "tokenizer.json"} <= names) and any(
        p.suffix == ".safetensors" for p in path.iterdir()
    )


def _wanted(path: str, patterns: list[str]) -> bool:
    if any(part in path for part in _SKIP_PARTS):
        return False
    if path.endswith(_SKIP_SUFFIXES):
        return False
    from fnmatch import fnmatch

    return any(fnmatch(path, pat) for pat in patterns)


def download_modelscope(repo: str, dest: Path, patterns: list[str]) -> bool:
    """从 ModelScope 直连下载模型文件，返回是否成功。"""
    try:
        url = f"{MS_BASE}/{repo}/repo/files?Revision=master&Recursive=true"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            data = json.loads(r.read())
        files = [f["Path"] for f in data.get("Data", {}).get("Files", [])]
        if not files:
            print(f"[modelscope] 未获取到 {repo} 的文件列表", file=sys.stderr)
            return False
        selected = [f for f in files if _wanted(f, patterns)]
        dest.mkdir(parents=True, exist_ok=True)
        total = 0
        for path in selected:
            out = dest / path
            out.parent.mkdir(parents=True, exist_ok=True)
            file_url = (
                f"{MS_BASE}/{repo}/repo?Revision=master&FilePath="
                + urllib.parse.quote(path)
            )
            with urllib.request.urlopen(
                urllib.request.Request(file_url, headers=UA), timeout=120
            ) as r, open(out, "wb") as f:
                shutil.copyfileobj(r, f, length=1024 * 1024)
            size = out.stat().st_size
            total += size
            print(f"[modelscope] {path} ({size / 1048576:.1f} MB)", flush=True)
        print(f"[modelscope] 完成 {repo} -> {dest} 共 {total / 1048576:.1f} MB", flush=True)
        return True
    except Exception as e:  # 网络/解析失败交给上层回退
        print(f"[modelscope] 下载失败：{e}", file=sys.stderr)
        return False


def download_hf(repo: str, dest: Path, patterns: list[str]) -> bool:
    try:
        import os

        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from huggingface_hub import snapshot_download

        dest.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo, local_dir=str(dest), allow_patterns=patterns)
        print(f"[hf-mirror] 完成 {repo} -> {dest}", flush=True)
        return True
    except Exception as e:
        print(f"[hf-mirror] 下载失败：{e}", file=sys.stderr)
        return False


def download(kind: str, force: bool = False) -> Path:
    repo, dest, patterns = TARGETS[kind]
    if _ready(dest) and not force:
        print(f"[skip] {kind}: {dest} 已就绪")
        return dest
    print(f"[fetch] {repo} -> {dest}", flush=True)
    ok = download_modelscope(repo, dest, patterns) or download_hf(repo, dest, patterns)
    if not ok:
        raise RuntimeError(f"{repo} 下载失败：ModelScope 与 hf-mirror 均不可用")
    if not _ready(dest):
        raise RuntimeError(f"{repo} 下载不完整：{dest}")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", action="store_true", help="同时下载 reranker")
    ap.add_argument("--check", action="store_true", help="只检查")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    kinds = ["embed"] + (["rerank"] if args.rerank else [])
    missing = []
    for k in kinds:
        dest = TARGETS[k][1]
        if args.check:
            ok = _ready(dest)
            print(f"{k}: {'OK' if ok else 'MISSING'} -> {dest}")
            if not ok:
                missing.append(k)
        else:
            download(k, force=args.force)
    return 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
