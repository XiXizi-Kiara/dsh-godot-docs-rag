"""JSONL 桥测试。

- 协议逻辑用内存流测试（不 spawn 子进程），沙箱内可跑；
- 真实子进程往返在无法创建管道时自动 skip（沙箱限制），普通终端可完整运行。
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag import dsh_bridge  # noqa: E402

EMPTY_INDEX = ROOT / ".tmp" / "tests" / "bridge_no_index"


def _run(lines: list[dict]) -> list[dict]:
    raw = "\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n"
    out = io.BytesIO()
    state = dsh_bridge.BridgeState(index_dir=EMPTY_INDEX)
    dsh_bridge.serve(io.BytesIO(raw.encode("utf-8")), out, state)
    return [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]


class TestBridge(unittest.TestCase):
    def test_ping_reports_not_ready(self):
        (resp,) = _run([{"id": 1, "method": "ping"}])
        self.assertTrue(resp["ok"])
        self.assertFalse(resp["result"]["index_ready"])
        self.assertEqual(resp["result"]["protocol"], 1)

    def test_unknown_method_then_still_serves(self):
        replies = _run([
            {"id": 2, "method": "nope"},
            {"id": 3, "method": "ping"},
        ])
        self.assertEqual(replies[0]["error"]["code"], "UNKNOWN_METHOD")
        self.assertTrue(replies[1]["ok"])

    def test_bad_json_line_does_not_kill_loop(self):
        data = b'not-json\n{"id":1,"method":"ping"}\n'
        out = io.BytesIO()
        dsh_bridge.serve(io.BytesIO(data), out, dsh_bridge.BridgeState(index_dir=EMPTY_INDEX))
        replies = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(replies[0]["error"]["code"], "BAD_PARAMS")
        self.assertTrue(replies[1]["ok"])

    def test_search_without_index_returns_actionable_error(self):
        (resp,) = _run([{"id": 4, "method": "search", "params": {"query": "add_child"}}])
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "INDEX_NOT_READY")
        self.assertIn("scripts\\run.py build", resp["error"]["hint"])

    def test_shutdown_replies_then_stops(self):
        replies = _run([{"id": 5, "method": "shutdown"}, {"id": 6, "method": "ping"}])
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0]["ok"])


def _can_spawn_with_pipes() -> bool:
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", "print(1)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.communicate(timeout=20)
        return proc.returncode == 0
    except (PermissionError, OSError):
        return False


class TestBridgeSubprocess(unittest.TestCase):
    @unittest.skipUnless(_can_spawn_with_pipes(), "沙箱禁止子进程管道；请在普通终端运行")
    def test_end_to_end_roundtrip_utf8(self):
        code = (
            "import sys\n"
            "from godot_rag.dsh_bridge import BridgeState, serve\n"
            "state = BridgeState(index_dir=None)\n"
            "serve(sys.stdin.buffer, sys.stdout.buffer, state)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            env={"PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(
            b'{"id":1,"method":"ping"}\n{"id":2,"method":"shutdown"}\n', timeout=60
        )
        self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace"))
        replies = [json.loads(line) for line in out.decode("utf-8").splitlines()]
        self.assertTrue(replies[0]["ok"])


if __name__ == "__main__":
    unittest.main()
