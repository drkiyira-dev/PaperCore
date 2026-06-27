"""salience 自检脚本 —— 用真实论文文本跑出真实显著度区间。

用法：
    python salience_selfcheck.py path/to/paper.txt    # 跑你的真实测试论文
    python salience_selfcheck.py                       # 不传参，用内置示例文本

打分逻辑以项目里的 salience.py 为【唯一事实源】：本脚本优先 import 它的
compute_salience / doc_tfidf_weights / content_tokens / 权重与归一化常数；只有在
脱离项目独立运行、import 失败时，才退回脚本内置的等价实现（并会在输出里标注）。
这样改了 salience.py 的权重或公式，本脚本自动跟随，不会两边漂移。

输出每条匹配的 salience 与四维特征分解，并汇总核心句 / 背景过渡句的实际区间，
直接用这里跑出来的数字去替换《显著度方法论说明》里的示例。
"""
import re
import sys

import jieba
import jieba.analyse
jieba.setLogLevel(60)

# ---- 规则源：优先复用项目 rules.py；导入失败则用内置精简版（仅供脚本独立运行）----
try:
    from rules import RULES
except Exception:
    RULES = [
        {"name": "conclusion", "min_confidence": 0.8,
         "pattern": r"(结论|综上所述|实验结果表明|研究表明|本文提出|本文设计|本文采用|本研究|创新点|结果显示)[^\n；。！？]{3,}[；。！？\n]?"},
        {"name": "method", "min_confidence": 0.75,
         "pattern": r"(本文方法|所提方法|提出了|设计了|构建了|建立了)[^\n；。！？]{3,}[；。！？\n]?"},
        {"name": "generic_background", "min_confidence": 0.5,
         "pattern": r"(随着.*?的发展|近年来|在当今社会|众所周知).*?(。|！|？)"},
        {"name": "redundant_transition", "min_confidence": 0.4,
         "pattern": r"(首先|其次|最后|综上所述|总而言之).*?(。|！|？)"},
    ]

# ---- 打分源：优先复用项目 salience.py（唯一事实源）；导入失败才用内联回退 ----
try:
    from salience import (
        doc_tfidf_weights, compute_salience, content_tokens,
        WEIGHTS, TFIDF_OOV_FLOOR, LEN_SAT,
    )
    _SCORING_SOURCE = "salience.py（项目实现，唯一事实源）"
except Exception:
    _SCORING_SOURCE = "脚本内联回退（未找到 salience.py）"

    STOPWORDS = {'的', '了', '在', '是', '也', '就', '不', '有', '和', '与',
                 '及', '对', '为', '这', '那', '上', '中'}
    WEIGHTS = {"rule": 0.45, "tfidf": 0.30, "density": 0.15, "length": 0.10}
    TFIDF_OOV_FLOOR = 0.15
    LEN_SAT = 20.0
    MIN_TOKEN_LEN = 1

    def doc_tfidf_weights(text, top_k=100):
        """整篇只算一次的 TF-IDF 权重（jieba.analyse 即 TF-IDF 实现）。"""
        return dict(jieba.analyse.extract_tags(text, topK=top_k, withWeight=True))

    def content_tokens(snippet):
        all_tokens = jieba.lcut(snippet)
        content = [w for w in all_tokens
                   if len(w) > MIN_TOKEN_LEN and w not in STOPWORDS]
        return all_tokens, content

    def _f_tfidf(toks, tfidf):
        # 量纲无关归一：词权重 / 本文最大权重；未登录词给地板分（与 salience.py 一致）
        if toks and tfidf:
            max_w = max(tfidf.values()) or 1.0
            return min(1.0, sum((tfidf[w] / max_w) if w in tfidf else TFIDF_OOV_FLOOR
                                for w in toks) / len(toks))
        return 0.0

    def compute_salience(snippet, rule, tfidf):
        all_tokens, toks = content_tokens(snippet)
        w_rule = rule.get('min_confidence', 0.5)
        f_tfidf = _f_tfidf(toks, tfidf)
        f_density = len(toks) / max(1, len(all_tokens))
        f_len = min(1.0, len(snippet) / LEN_SAT)
        score = (WEIGHTS["rule"] * w_rule + WEIGHTS["tfidf"] * f_tfidf
                 + WEIGHTS["density"] * f_density + WEIGHTS["length"] * f_len)
        return round(min(0.95, max(0.05, score)), 2)


def feature_breakdown(snippet, rule, tfidf):
    """仅用于展示的四维特征分解（w_rule, tfidf, density, len）。

    复用与打分同一套基元（content_tokens / TFIDF_OOV_FLOOR / LEN_SAT），口径与
    salience.py 一致；权威分数仍由 compute_salience 给出，这里只为可解释性。
    """
    all_tokens, toks = content_tokens(snippet)
    w_rule = rule.get('min_confidence', 0.5)
    if toks and tfidf:
        _max_w = max(tfidf.values()) or 1.0
        f_tfidf = min(1.0, sum((tfidf[w] / _max_w) if w in tfidf else TFIDF_OOV_FLOOR
                               for w in toks) / len(toks))
    else:
        f_tfidf = 0.0
    f_density = len(toks) / max(1, len(all_tokens))
    f_len = min(1.0, len(snippet) / LEN_SAT)
    return round(w_rule, 2), round(f_tfidf, 2), round(f_density, 2), round(f_len, 2)


SAMPLE = """近年来，随着深度学习的发展，目标检测技术在工业质检中得到广泛应用。
众所周知，传统方法依赖人工设计特征，泛化能力有限。
本文提出了一种基于注意力机制的轻量级目标检测网络，显著降低了模型参数量与推理延迟。
所提方法在公开数据集上的平均精度达到百分之九十二，召回率较基线提升百分之七。
首先，我们设计了多尺度特征融合模块。其次，引入通道剪枝压缩模型。
实验结果表明，本方法在保持精度的同时将推理速度提升了一点八倍。
综上所述，本文方法在精度与效率之间取得了良好平衡。"""


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            text = f.read()
        print(f"[源] {sys.argv[1]}  （{len(text)} 字）")
    else:
        text = SAMPLE
        print("[源] 内置示例文本（传入文件路径以跑真实论文）")
    print(f"[打分实现] {_SCORING_SOURCE}\n")

    tfidf = doc_tfidf_weights(text)
    print(f"{'rule':<22}{'salience':<10}{'(w_rule, tfidf, density, len)':<36}片段")
    print("-" * 100)

    core, filler = [], []
    for rule in RULES:
        for m in re.finditer(rule['pattern'], text):
            s = compute_salience(m.group(0), rule, tfidf)
            feats = feature_breakdown(m.group(0), rule, tfidf)
            frag = m.group(0)[:22].replace("\n", "")
            print(f"{rule['name']:<22}{s:<10}{str(feats):<36}{frag}")
            (core if rule.get('min_confidence', 0.5) >= 0.75 else filler).append(s)

    print("-" * 100)
    if core:
        print(f"核心句(结论/方法)实际区间: {min(core):.2f} – {max(core):.2f}")
    if filler:
        print(f"背景/过渡句实际区间:      {min(filler):.2f} – {max(filler):.2f}")
    if core and filler:
        gap = sum(core) / len(core) - sum(filler) / len(filler)
        print(f"两类均值差距:             {gap:.2f}")


if __name__ == "__main__":
    main()
