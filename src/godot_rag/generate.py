"""答案生成层：无 LLM 时走检索式回答，有 LLM 时调用 OpenAI 兼容接口。

本模块只依赖标准库，输入是检索层返回的 passage 列表（SearchResult dict），
不 import 项目内其他模块，因此可以完全用 fake 数据单测。

约定（见 docs/INTERFACES.md §7）：

    answer(query, passages, lang=None) -> {"answer": str, "llm_used": bool}

- 没有环境变量 ``GODOT_RAG_LLM_API_KEY``：检索式回答，明确标注“检索式回答”，
  内容全部来自片段，不编造。
- 有 key：POST ``{base_url}/chat/completions``（默认 DeepSeek），失败时静默降级
  为检索式回答，HTTP 错误细节不泄漏给用户。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

# ---- 配置（均可用环境变量覆盖） ----
API_KEY_ENV = "GODOT_RAG_LLM_API_KEY"
BASE_URL_ENV = "GODOT_RAG_LLM_BASE_URL"
MODEL_ENV = "GODOT_RAG_LLM_MODEL"

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60.0

MAX_PASSAGE_CHARS = 900  # 单个片段送入 LLM 的最大字符数
MAX_PROMPT_CHARS = 12000  # 片段总字符预算
MAX_BULLETS = 5
MIN_BULLETS = 3

RETRIEVAL_NOTE = "（检索式回答：以下依据检索到的官方文档片段，未使用生成模型）"
DEGRADED_NOTE = "（生成模型调用失败，已自动降级为检索式回答）"

# 句子结束符：中文标点直接切；英文句点仅在后面是空白或结尾时切，
# 避免把 Node.add_child() / 4.3 之类的符号切断。
_SENTENCE_END = re.compile(r"[。！？!?]|\.(?=\s|$)")

# 测试可替换为假的 urlopen（签名与 urllib.request.urlopen 一致）
_urlopen = urllib.request.urlopen


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _field(passage, key: str, default=""):
    """兼容 passage 是 dict 或对象（SearchResult 实例）两种情况。"""
    if isinstance(passage, dict):
        value = passage.get(key, default)
    else:
        value = getattr(passage, key, default)
    return default if value is None else value


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _first_sentence(text: str, max_len: int = 240) -> str:
    """取首个完整句子；没有句号时退化为截断文本。"""
    text = _clean(text)
    if not text:
        return ""
    match = _SENTENCE_END.search(text)
    sentence = text[: match.end()].strip() if match else text
    if len(sentence) > max_len:
        sentence = sentence[:max_len].rstrip() + "…"
    return sentence


def _detect_lang(query: str) -> str:
    """粗略判断提问语言，仅用于 prompt 提示。"""
    return "zh" if re.search(r"[\u4e00-\u9fff]", query or "") else "en"


def _title_of(passage) -> str:
    title = _field(passage, "title") or _field(passage, "doc_id") or "未命名文档"
    return _clean(title)


def _passage_lines(passages: list) -> str:
    """把片段拼成带 [n] 编号的 prompt 上下文，按总字符预算截断。"""
    lines: list[str] = []
    budget = MAX_PROMPT_CHARS
    for index, passage in enumerate(passages, start=1):
        text = _clean(_field(passage, "text") or _field(passage, "raw_text"))
        if not text:
            continue
        text = text[:MAX_PASSAGE_CHARS]
        block = (
            f"[{index}] 标题：{_title_of(passage)}"
            f"（URL: {_clean(_field(passage, 'url')) or '无'}）\n{text}\n"
        )
        if len(block) > budget:
            break
        lines.append(block)
        budget -= len(block)
    return "\n".join(lines)


def _build_messages(query: str, passages: list, lang: str | None) -> list[dict]:
    answer_lang = lang or _detect_lang(query)
    lang_rule = "中文提问必须用中文回答。" if answer_lang == "zh" else "Use the same language as the question."
    system = (
        "你是 Godot 官方文档助手。只能依据用户提供的文档片段回答，"
        "不得使用片段之外的知识，不得编造 API、参数、默认值或行为。\n"
        "要求：\n"
        "1. 每个结论后用 [n] 标注来源片段编号（n 是片段前的数字）。\n"
        "2. 片段不足以回答时，明确说明“根据提供的文档片段无法确定”，并指出还缺什么信息。\n"
        f"3. {lang_rule}\n"
        "4. 回答简洁：先给结论，再给必要步骤；代码用 ```gdscript 代码块。\n"
        "5. 不要复述这些规则，不要提及“片段编号规则”等元信息。"
    )
    user = (
        "文档片段：\n"
        f"{_passage_lines(passages)}\n"
        f"用户问题：{_clean(query)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 检索式回答（无 LLM 或降级时使用）
# ---------------------------------------------------------------------------
def _body(passage) -> str:
    """取片段正文：优先 raw_text；没有时去掉 text 顶部的面包屑前缀行。"""
    raw = str(_field(passage, "raw_text") or "")
    if raw.strip():
        return _clean(raw)
    text = str(_field(passage, "text") or "")
    # 检索文本约定为“面包屑前缀 + 正文”，回答里不需要重复面包屑。
    if text.lstrip().startswith("Godot Docs >"):
        lines = text.splitlines()
        if len(lines) > 1:
            text = "\n".join(lines[1:])
    return _clean(text)


def _retrieval_answer(query: str, passages: list) -> dict:
    """仅用片段内容拼出回答，明确标注未使用生成模型。"""
    usable = [p for p in passages if _body(p)]
    if not usable:
        return {
            "answer": (
                "未检索到相关文档片段，无法给出有依据的回答。\n"
                "请换用更具体的英文 API 名称（例如 add_child、queue_free）或更短的查询重试。"
            ),
            "llm_used": False,
        }

    summary = _first_sentence(_body(usable[0]))
    if not summary:
        summary = _title_of(usable[0])

    bullets: list[str] = []
    for number, passage in enumerate(usable[:MAX_BULLETS], start=1):
        sentence = _first_sentence(_body(passage))
        if not sentence:
            continue
        symbol = _clean(_field(passage, "symbol"))
        title = _title_of(passage)
        label = f"《{title}》" if not symbol else f"《{title}》{symbol}"
        bullets.append(f"- [{number}] {label}：{sentence}")
        if len(bullets) >= MAX_BULLETS:
            break

    if len(bullets) < MIN_BULLETS:
        # 片段太少时补充说明，但不编造内容。
        bullets.append("- 其余检索片段与本次问题关联较弱，未进一步归纳。")

    parts = [
        f"摘要：{summary}",
        "",
        RETRIEVAL_NOTE,
        "",
        "要点：",
        *bullets,
        "",
        "以上要点均摘自上方片段原文，完整内容请查看对应官方文档页面。",
    ]
    return {"answer": "\n".join(parts), "llm_used": False}


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------
def _llm_config() -> tuple[str, str]:
    base = (_clean(os.environ.get(BASE_URL_ENV)) or DEFAULT_BASE_URL).rstrip("/")
    model = _clean(os.environ.get(MODEL_ENV)) or DEFAULT_MODEL
    return base, model


def _call_llm(
    query: str,
    passages: list,
    lang: str | None,
    *,
    api_key: str,
    opener=None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """调用 OpenAI 兼容的 /chat/completions，返回纯文本回答；失败抛异常。"""
    base, model = _llm_config()
    payload = {
        "model": model,
        "messages": _build_messages(query, passages, lang),
        "temperature": 0.1,
        "max_tokens": 1024,
        "stream": False,
    }
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    open_func = opener or _urlopen
    with open_func(request, timeout=timeout) as response:
        raw = response.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    data = json.loads(raw.decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return str(content or "").strip()


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------
def answer(
    query: str,
    passages: list,
    lang: str | None = None,
    *,
    api_key: str | None = None,
    opener=None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """生成答案，始终返回 ``{"answer": str, "llm_used": bool}``。

    参数：
        query: 用户问题。
        passages: SearchResult dict 列表（也兼容对象）。
        lang: ``"zh"`` / ``"en"``；省略时按提问自动判断。
        api_key: 显式注入的 key（测试用），省略时读环境变量。
        opener: 可注入的 urlopen 假实现（测试用）。
        timeout: HTTP 超时秒数。
    """
    passages = list(passages or [])
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
    key = (key or "").strip()

    if not key:
        return _retrieval_answer(query, passages)

    try:
        content = _call_llm(
            query, passages, lang, api_key=key, opener=opener, timeout=timeout
        )
    except Exception:
        # 网络 / HTTP / 解析错误一律降级，不把错误细节泄漏给用户。
        degraded = _retrieval_answer(query, passages)
        degraded["answer"] = f"{DEGRADED_NOTE}\n\n{degraded['answer']}"
        degraded["llm_used"] = False
        return degraded

    if not content:
        degraded = _retrieval_answer(query, passages)
        degraded["answer"] = f"{DEGRADED_NOTE}\n\n{degraded['answer']}"
        degraded["llm_used"] = False
        return degraded

    return {"answer": content, "llm_used": True}
