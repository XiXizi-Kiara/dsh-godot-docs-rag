"""generate.py 单元测试：不访问网络，用 fake opener 模拟 LLM 接口。"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from godot_rag import generate  # noqa: E402

ENV_KEYS = (generate.API_KEY_ENV, generate.BASE_URL_ENV, generate.MODEL_ENV)

PASSAGES = [
    {
        "rank": 1,
        "score": 0.91,
        "chunk_id": "c1",
        "doc_id": "classes/class_node",
        "doc_type": "class",
        "title": "Node",
        "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
        "url": "classes/class_node.html#class-node-method-add-child",
        "symbol": "Node.add_child()",
        "text": "Node.add_child() adds a child node to the scene tree. "
        "The child is added at the end of the parent's children.",
    },
    {
        "rank": 2,
        "score": 0.77,
        "chunk_id": "c2",
        "doc_id": "tutorials/scripting/scene_tree",
        "doc_type": "tutorial",
        "title": "Scene tree",
        "breadcrumbs": ["Godot Docs", "Tutorials", "Scene tree"],
        "url": "tutorials/scripting/scene_tree.html",
        "symbol": "",
        "text": "The scene tree is the hierarchy of nodes in a running scene. "
        "Every node has a parent except the root.",
    },
    {
        "rank": 3,
        "score": 0.62,
        "chunk_id": "c3",
        "doc_id": "classes/class_scene_tree",
        "doc_type": "class",
        "title": "SceneTree",
        "breadcrumbs": ["Godot Docs", "Class reference", "SceneTree"],
        "url": "classes/class_scene_tree.html",
        "symbol": "SceneTree.root",
        "text": "SceneTree.root returns the root Window of the scene tree. "
        "It is only available while the tree is running.",
    },
    {
        "rank": 4,
        "score": 0.55,
        "chunk_id": "c4",
        "doc_id": "classes/class_node",
        "doc_type": "class",
        "title": "Node",
        "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
        "url": "classes/class_node.html#class-node-method-queue-free",
        "symbol": "Node.queue_free()",
        "text": "Node.queue_free() queues a node for deletion at the end of the current frame. "
        "It is safe to call multiple times.",
    },
    {
        "rank": 5,
        "score": 0.41,
        "chunk_id": "c5",
        "doc_id": "classes/class_node",
        "doc_type": "class",
        "title": "Node",
        "breadcrumbs": ["Godot Docs", "Class reference", "Node"],
        "url": "classes/class_node.html#class-node-property-name",
        "symbol": "Node.name",
        "text": "Node.name is the node's name, unique among its siblings. "
        "Renaming a node updates the scene tree.",
    },
]


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeOpener:
    """记录请求；按需返回 OpenAI 兼容响应或抛异常。"""

    def __init__(self, content="根据 [1]，使用 add_child() 添加子节点。", error=None):
        self.payload = {
            "choices": [{"message": {"role": "assistant", "content": content}}],
        }
        self.error = error
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append({"request": request, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.payload)


class GenerateRetrievalTest(unittest.TestCase):
    """无 LLM key：必须走检索式回答，内容全部来自片段。"""

    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_no_key_returns_retrieval_answer(self):
        result = generate.answer("如何添加子节点？", PASSAGES)
        self.assertFalse(result["llm_used"])
        self.assertIn("检索式回答", result["answer"])
        self.assertIn("未使用生成模型", result["answer"])
        self.assertIn("Node.add_child()", result["answer"])
        self.assertIn("Node.add_child() adds a child node to the scene tree.", result["answer"])

    def test_bullets_use_distinct_passages_with_citation_numbers(self):
        result = generate.answer("如何添加子节点？", PASSAGES)
        answer = result["answer"]
        numbered = [int(number) for number in __import__("re").findall(r"\[(\d+)\]", answer)]
        self.assertGreaterEqual(len(numbered), 3)
        self.assertLessEqual(len(numbered), 5)
        # 只取前 5 个片段，每条来自不同片段
        self.assertEqual(numbered, [1, 2, 3, 4, 5])
        for passage in PASSAGES:
            self.assertIn(passage["title"], answer)

    def test_only_first_sentence_is_used(self):
        result = generate.answer("add_child 是什么？", PASSAGES[:1])
        answer = result["answer"]
        self.assertIn("Node.add_child() adds a child node to the scene tree.", answer)
        self.assertNotIn("The child is added at the end", answer)

    def test_symbol_with_dot_not_split(self):
        passages = [
            {
                "title": "Node",
                "text": "Use Node.add_child() to add a node. Then the node is active.",
                "url": "classes/class_node.html",
            }
        ]
        result = generate.answer("add_child", passages)
        self.assertIn("Use Node.add_child() to add a node.", result["answer"])
        self.assertNotIn("Then the node is active.", result["answer"])

    def test_few_passages_do_not_fabricate(self):
        result = generate.answer("场景树是什么？", PASSAGES[:2])
        answer = result["answer"]
        self.assertIn("Scene tree", answer)
        self.assertIn("关联较弱", answer)
        self.assertFalse(result["llm_used"])

    def test_empty_passages(self):
        result = generate.answer("不存在的主题", [])
        self.assertFalse(result["llm_used"])
        self.assertIn("未检索到相关文档片段", result["answer"])

    def test_prefers_raw_text_over_breadcrumb_prefix(self):
        passages = [
            {
                "title": "Node",
                "text": "Godot Docs > Class reference > Node > add_child() Node.add_child() adds a child.",
                "raw_text": "Node.add_child() adds a child node. More details follow.",
                "url": "classes/class_node.html",
            }
        ]
        result = generate.answer("add_child", passages)
        answer = result["answer"]
        self.assertIn("Node.add_child() adds a child node.", answer)
        self.assertNotIn("Godot Docs > Class reference", answer)

    def test_strips_breadcrumb_prefix_when_raw_text_missing(self):
        passages = [
            {
                "title": "Node",
                "text": "Godot Docs > Class reference > Node > add_child()\n"
                        "Node.add_child() adds a child node. More details follow.",
                "url": "classes/class_node.html",
            }
        ]
        result = generate.answer("add_child", passages)
        answer = result["answer"]
        self.assertIn("Node.add_child() adds a child node.", answer)
        self.assertNotIn("Godot Docs >", answer)

    def test_object_passages_are_supported(self):
        class _Passage:
            title = "Node"
            text = "Node.add_child() adds a child. More text."
            url = "classes/class_node.html"
            symbol = "Node.add_child()"

        result = generate.answer("add_child", [_Passage()])
        self.assertFalse(result["llm_used"])
        self.assertIn("Node.add_child() adds a child.", result["answer"])

    def test_no_network_without_key(self):
        opener = _FakeOpener()
        result = generate.answer("add_child", PASSAGES, opener=opener)
        self.assertFalse(result["llm_used"])
        self.assertEqual(opener.calls, [])


class GenerateLLMTest(unittest.TestCase):
    """有 key：验证请求格式、成功路径与失败降级。"""

    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ[generate.API_KEY_ENV] = "test-key"

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_success_uses_llm_answer(self):
        opener = _FakeOpener(content="根据 [1]，使用 add_child() 添加子节点。")
        result = generate.answer("如何添加子节点？", PASSAGES, opener=opener)
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["answer"], "根据 [1]，使用 add_child() 添加子节点。")
        self.assertEqual(len(opener.calls), 1)

        call = opener.calls[0]
        self.assertEqual(call["timeout"], generate.DEFAULT_TIMEOUT)
        request = call["request"]
        self.assertEqual(request.full_url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertFalse(payload["stream"])
        system = payload["messages"][0]["content"]
        self.assertIn("只能依据", system)
        user = payload["messages"][1]["content"]
        self.assertIn("add_child() adds a child node", user)
        self.assertIn("[1]", user)
        self.assertIn("如何添加子节点？", user)

    def test_env_overrides_base_url_and_model(self):
        os.environ[generate.BASE_URL_ENV] = "https://example.test/v1/"
        os.environ[generate.MODEL_ENV] = "my-model"
        opener = _FakeOpener()
        result = generate.answer("add_child", PASSAGES, opener=opener)
        self.assertTrue(result["llm_used"])
        request = opener.calls[0]["request"]
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "my-model")

    def test_lang_zh_adds_chinese_rule(self):
        opener = _FakeOpener()
        generate.answer("add_child", PASSAGES, lang="zh", opener=opener)
        payload = json.loads(opener.calls[0]["request"].data.decode("utf-8"))
        self.assertIn("中文提问必须用中文回答", payload["messages"][0]["content"])

    def test_explicit_api_key(self):
        os.environ.pop(generate.API_KEY_ENV, None)
        opener = _FakeOpener()
        result = generate.answer("add_child", PASSAGES, api_key="explicit", opener=opener)
        self.assertTrue(result["llm_used"])
        self.assertEqual(
            opener.calls[0]["request"].get_header("Authorization"), "Bearer explicit"
        )

    def test_http_failure_degrades_without_leaking_error(self):
        error = RuntimeError("boom-http-500")
        opener = _FakeOpener(error=error)
        result = generate.answer("如何添加子节点？", PASSAGES, opener=opener)
        answer = result["answer"]
        self.assertFalse(result["llm_used"])
        self.assertIn("检索式回答", answer)
        self.assertIn("降级", answer)
        self.assertNotIn("boom-http-500", answer)
        self.assertNotIn("RuntimeError", answer)
        self.assertIn("Node.add_child() adds a child node", answer)

    def test_bad_response_shape_degrades(self):
        class _BadOpener:
            def __call__(self, request, timeout=None):
                class _Resp:
                    def read(self):
                        return b'{"unexpected": true}'

                    def __enter__(self):
                        return self

                    def __exit__(self, *exc_info):
                        return False

                return _Resp()

        result = generate.answer("add_child", PASSAGES, opener=_BadOpener())
        self.assertFalse(result["llm_used"])
        self.assertIn("检索式回答", result["answer"])


if __name__ == "__main__":
    unittest.main()
