"""
semantic.py — 轻量语义增强（C6 / 答辩 P16 边界②：规则词库覆盖 → 语义相似度补）

用一个小型多语种句向量模型（默认 paraphrase-multilingual-MiniLM-L12-v2，~120MB），
把「换了说法、但语义贴近词库概念」的句子也算作命中，叠加在字面词库命中之外。

设计原则（与项目「本地优先 / 断网可复现 / 克制」一致）：
- 懒加载：首次用到才载入模型，不拖慢启动；
- 优雅回退：模型未安装 / 未下载 / 任何异常 → 一律返回 0，行为退化为「纯字面词库」，绝不抛错；
- 一次性联网下模型后缓存，之后离线可跑（HF_HUB_OFFLINE=1 下加载已缓存模型不受影响）。
"""
import os
import threading

_MODEL_NAME = os.environ.get("PC_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
_THRESHOLD = float(os.environ.get("PC_SEMANTIC_THRESHOLD", "0.52"))

_model = None
_load_failed = False
_lock = threading.Lock()
_anchor_cache = {}


def _get_model():
    """懒加载 SentenceTransformer；不可用 / 未下载时置 _load_failed 并返回 None。"""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is None and not _load_failed:
            try:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(_MODEL_NAME)
                print(f"[semantic] 已载入嵌入模型 {_MODEL_NAME}")
            except Exception as e:
                _load_failed = True
                print(f"[semantic] 嵌入模型不可用，语义增强跳过（回退纯词库）：{e}")
    return _model


def available():
    """模型是否就绪（供自检 / 状态显示）。"""
    return _get_model() is not None


def _anchors(keywords):
    """把一组词库词编码成「概念锚向量」（按词表缓存，避免重复编码）。"""
    key = tuple(keywords)
    cached = _anchor_cache.get(key)
    if cached is not None:
        return cached
    model = _get_model()
    if model is None:
        return None
    embs = model.encode(list(keywords), normalize_embeddings=True, convert_to_numpy=True)
    _anchor_cache[key] = embs
    return embs


def _split_sentences(text):
    import re
    parts = re.split(r'(?<=[。！？；!?;\.])\s*|\n+', text or "")
    return [s.strip() for s in parts if len(s.strip()) >= 8]


def extra_hits(text, keywords, literal_hits=None, threshold=None, cap=4, max_sentences=150):
    """语义命中数：与词库概念语义贴近、但「不含任何字面词库词」的句子数（封顶 cap）。

    叠加在字面命中之外、且只看没有字面命中的句子，避免与字面重复计。
    模型不可用或任何异常 → 返回 0（行为 = 纯字面词库，绝不影响主分析流程）。
    """
    try:
        model = _get_model()
        if model is None or not keywords:
            return 0
        anchors = _anchors(keywords)
        if anchors is None:
            return 0
        kws_lower = [k.lower() for k in keywords]
        sents = _split_sentences(text)[:max_sentences]
        cand = [s for s in sents if not any(k in s.lower() for k in kws_lower)]
        if not cand:
            return 0
        embs = model.encode(cand, normalize_embeddings=True, convert_to_numpy=True)
        sims = embs @ anchors.T            # 余弦（两侧已归一化）
        th = _THRESHOLD if threshold is None else threshold
        hits = int((sims.max(axis=1) >= th).sum())
        return min(cap, hits)
    except Exception as e:
        print(f"[semantic] extra_hits 异常，按 0 处理：{e}")
        return 0
