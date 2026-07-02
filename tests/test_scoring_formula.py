"""评分公式重构：学科自适应 + 类别覆盖/饱和 + 置信度。

锁住三件事：① 堆一类词顶不满（治「堆词判高」）；② 选对学科分更高（学科尺度）；
③ 文本太短/命中过少 → 低置信（不误导学生）。
"""
import app

# 只堆「模型与架构」一类词
STUFF = ("摘要：本文提出了一种新方法。\n2 方法："
         + "模型、网络、架构、模块、神经网络、卷积、注意力、自注意力、编码器、解码器、嵌入、transformer。" * 3
         + "\n5 结论：本方法有效。")
# 覆盖 CS 四个方法学环节
BROAD = ("摘要：本文提出了一种新方法。\n2 方法："
         "我们构建了神经网络架构（编码器-解码器），用损失函数与优化器训练，学习率 0.001，批大小 64；"
         "在数据集上做了消融实验与基线对比、交叉验证；评价指标为准确率、F1、AUC，分析了泛化与鲁棒性。\n"
         "5 结论：本方法在多个数据集上验证有效，准确率 95.2%，显著优于基线方法，具有推广价值与应用前景。")


def _method(txt, subject, mode="teacher"):
    s = app.extract_sections(txt)
    q = app.analyze_paper_quality(txt, s, mode=mode, teacher_cap=85, subject=subject)
    return q


def test_stuffing_scores_lower_than_breadth():
    qs = _method(STUFF, "cs")
    qb = _method(BROAD, "cs")
    assert qb["dimensions"]["method"]["score"] > qs["dimensions"]["method"]["score"]
    # 广度应覆盖比堆词更多环节
    assert "4/4" in qb["dimensions"]["method"]["detail"]


def test_subject_match_scores_higher():
    """医学论文：选对 medical 档应显著高于误选 cs 档。"""
    med = ("摘要：本研究评估某药物对高血压的疗效。\n"
           "2 研究方法：采用随机对照试验，纳入 240 例患者，随机分为实验组与对照组，双盲；"
           "计算样本量，随访 12 个月，主要终点用生存分析（风险比），方差分析比较，报告敏感度与特异度，纳入标准明确。\n"
           "5 结论：该药物疗效确切，不良反应少，具有重要临床意义与推广价值，值得进一步开展多中心验证研究。")
    q_cs = _method(med, "cs")
    q_me = _method(med, "medical")
    assert q_me["dimensions"]["method"]["score"] > q_cs["dimensions"]["method"]["score"]


def test_confidence_low_on_short_text():
    q = app.analyze_paper_quality("摘要：本文提出一种新方法。", {}, mode="teacher", subject="general")
    assert q["confidence"]["level"] in ("低", "中")
    assert "caveat" in q["confidence"]


def test_result_has_subject_and_confidence():
    q = _method(BROAD, "cs")
    assert q["subject"]["key"] == "cs"
    assert set(q["confidence"]) >= {"score", "level", "reasons", "caveat"}


def test_professor_stricter_than_teacher():
    qt = _method(BROAD, "cs", "teacher")
    qp = _method(BROAD, "cs", "professor")
    assert qp["total"] <= qt["total"]
