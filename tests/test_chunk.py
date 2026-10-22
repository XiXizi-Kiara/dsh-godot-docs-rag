"""chunk 单元测试：前缀、符号分块、大小上限、稳定性、tabs 去重。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godot_rag import config  # noqa: E402
from godot_rag.chunk import chunk_document  # noqa: E402
from godot_rag.parse_rst import parse_rst  # noqa: E402
from godot_rag.types import Block, Document, Section  # noqa: E402


class TestClassChunking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "godot-docs-html-stable/_sources/classes/class_node.rst.txt"
        if not path.exists():
            raise unittest.SkipTest("文档快照不存在")
        cls.doc = parse_rst(path, "classes/class_node")
        cls.chunks = chunk_document(cls.doc)

    def test_has_method_symbol_chunk(self):
        add = [c for c in self.chunks if c["symbol"] == "Node.add_child"]
        self.assertEqual(len(add), 1)
        c = add[0]
        self.assertIn("Node.add_child", c["text"])
        self.assertIn("Adds a child", c["text"])
        self.assertEqual(c["url"], "classes/class_node.html")
        self.assertEqual(c["doc_type"], "class")
        self.assertTrue(c["anchor"].startswith("class-node-method-add-child"))

    def test_all_chunks_have_prefix_and_size_cap(self):
        for c in self.chunks:
            self.assertTrue(c["text"].startswith("Godot Docs > "))
            self.assertLessEqual(c["n_tokens"], config.CHUNK_MAX_TOKENS + 40)
            self.assertTrue(c["chunk_id"])
            self.assertLessEqual(len(c["chunk_id"]), 16)

    def test_chunk_ids_stable(self):
        again = chunk_document(self.doc)
        self.assertEqual([c["chunk_id"] for c in self.chunks], [c["chunk_id"] for c in again])

    def test_reftable_section_skipped(self):
        texts = "\n".join(c["text"] for c in self.chunks)
        # 属性清单表不应产生单独的大块
        sections = {c["text"].split(" > ")[-1].split("\n")[0] for c in self.chunks}
        self.assertNotIn("Properties", sections)


class TestTutorialChunking(unittest.TestCase):
    def test_tabs_keep_only_gdscript(self):
        path = ROOT / "godot-docs-html-stable/_sources/tutorials/2d/2d_movement.rst.txt"
        if not path.exists():
            self.skipTest("文档快照不存在")
        doc = parse_rst(path, "tutorials/2d/2d_movement")
        chunks = chunk_document(doc)
        full = "\n".join(c["text"] for c in chunks)
        self.assertNotIn("C#", full)
        self.assertIn("GDScript", full)

    def test_small_document(self):
        doc = Document(
            doc_id="tutorials/tiny",
            doc_type="tutorial",
            title="Tiny",
            breadcrumbs=["Godot Docs", "Tutorials", "Tiny"],
            url="tutorials/tiny.html",
            sections=[Section(title="Tiny", level=1, blocks=[Block(kind="para", text="hello")])],
        )
        chunks = chunk_document(doc)
        self.assertEqual(len(chunks), 1)
        self.assertIn("hello", chunks[0]["text"])

    def test_long_section_split(self):
        blocks = [Block(kind="para", text=("word " * 300).strip()) for _ in range(4)]
        doc = Document(
            doc_id="tutorials/long",
            doc_type="tutorial",
            title="Long",
            breadcrumbs=["Godot Docs", "Tutorials", "Long"],
            url="tutorials/long.html",
            sections=[Section(title="Long", level=1, blocks=blocks)],
        )
        chunks = chunk_document(doc)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(c["n_tokens"], config.CHUNK_MAX_TOKENS + 40)


if __name__ == "__main__":
    unittest.main()
