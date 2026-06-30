"""C6 · 关键词语义增强（semantic.extra_hits）。

两条路都测：无模型时优雅回退 0（绝不抛）；有模型时能抓换说法、不误报无关句。
有模型的用例在未下载模型的机器上自动 skip。
"""
import pytest
import semantic


def test_extra_hits_no_model_returns_zero(monkeypatch):
    # 强制模型不可用 → 必须返回 0、绝不抛（=纯字面词库行为）
    monkeypatch.setattr(semantic, "_get_model", lambda: None)
    n = semantic.extra_hits("本文提出了一种全新的方法，构建了系统。", ["创新", "提出"], [])
    assert n == 0


def test_extra_hits_empty_keywords_returns_zero():
    assert semantic.extra_hits("任意文本，随便写点东西。", [], []) == 0


@pytest.mark.skipif(not semantic.available(), reason="嵌入模型未下载（CI/他机自动跳过）")
def test_extra_hits_catches_paraphrase():
    kws = ["创新", "提出", "贡献", "首次"]
    para = ("本研究开创性地实现了端到端全自动流程，刷新了该任务的最好成绩。"
            "我们的设计带来了显著突破。")
    assert semantic.extra_hits(para, kws, []) > 0


@pytest.mark.skipif(not semantic.available(), reason="嵌入模型未下载（CI/他机自动跳过）")
def test_extra_hits_ignores_unrelated():
    kws = ["创新", "提出", "贡献", "首次"]
    assert semantic.extra_hits("今天天气晴朗，我去公园散步喝茶看书。", kws, []) <= 1


@pytest.mark.skipif(not semantic.available(), reason="嵌入模型未下载（CI/他机自动跳过）")
def test_extra_hits_capped():
    # 大量相近句也不超过 cap（封顶防过计）
    kws = ["创新", "提出"]
    text = "。".join(["本工作首次实现了全新的突破性方案"] * 20)
    assert semantic.extra_hits(text, kws, [], cap=4) <= 4
