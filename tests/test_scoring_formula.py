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


def test_depth_layer_active():
    """两层方法维度：覆盖相同环节数时，方法词更丰富的深度分更高（锁住扁平词库确实在用，防再次孤立）。"""
    # 两篇都只覆盖「模型与架构」这一个 CS 环节，但一篇方法词多、一篇几乎没有
    thin = "2 方法：我们用了一个模型。"
    rich = ("2 方法：我们用了模型、网络、架构、卷积、注意力、编码器、解码器、嵌入、"
            "模块、神经网络、transformer 等多种结构。")
    q_thin = app.analyze_paper_quality(thin, {}, mode="teacher", subject="cs")
    q_rich = app.analyze_paper_quality(rich, {}, mode="teacher", subject="cs")
    assert q_rich["dimensions"]["method"]["score"] > q_thin["dimensions"]["method"]["score"]


def test_weak_paper_not_inflated():
    """弱论文（方法几乎空）方法维度应明显偏低，跨学科词库不应把它抬高。"""
    weak = "摘要：本文做了一项研究。\n2 方法：我们分析了数据。\n5 结论：有一些发现。"
    q = app.analyze_paper_quality(weak, app.extract_sections(weak), mode="teacher", subject="general")
    assert q["dimensions"]["method"]["score"] <= 12


# 材料/化学论文：实验型材料论文的方法结构（制备+表征+性能+机理）≠ 机械/控制。
MAT = ("摘要：本文制备了铝合金表面的钛锆转化膜并研究其耐蚀性能。\n"
       "2 方法：采用溶胶凝胶法制备转化膜，热处理后用扫描电镜（SEM）与能谱（EDS）表征表面形貌与元素分布，"
       "X射线衍射（XRD）分析晶体结构；用极化曲线与电化学阻抗（EIS）评估耐蚀性，测量腐蚀电流与腐蚀电位，"
       "并做盐雾试验；测定膜厚与附着力；分析了成膜机理与微观结构。\n"
       "5 结论：转化膜致密均匀，显著提升了耐蚀性，腐蚀电流降低一个数量级，具有工程应用价值。")


def test_materials_subject_fits_better_than_engineering():
    """材料论文在 materials 档应显著高于误用 engineering 档（engineering 的建模/控制环节材料论文命不中）。"""
    s = app.extract_sections(MAT)
    q_eng = app.analyze_paper_quality(MAT, s, mode="teacher", teacher_cap=85, subject="engineering")
    q_mat = app.analyze_paper_quality(MAT, s, mode="teacher", teacher_cap=85, subject="materials")
    assert q_mat["dimensions"]["method"]["score"] > q_eng["dimensions"]["method"]["score"]
    assert "4/4" in q_mat["dimensions"]["method"]["detail"]


def test_materials_is_valid_subject():
    assert "materials" in app.SUBJECT_RUBRICS
    assert len(app.SUBJECT_RUBRICS["materials"]["categories"]) == 4


def test_new_subjects_registered():
    """theory/electronics/civil/biology 均为合法学科档，各 4 个方法学环节。"""
    for subj in ("theory", "electronics", "civil", "biology"):
        assert subj in app.SUBJECT_RUBRICS, f"缺学科档 {subj}"
        assert len(app.SUBJECT_RUBRICS[subj]["categories"]) == 4


def test_theory_fits_math_paper():
    """纯理论论文（定理/证明/收敛）在 theory 档应显著高于误用 general（实证型环节命不中）。"""
    math = ("摘要：本文研究某算子的有界性。\n"
            "2 预备与定义：设 X 为度量空间，给出记号与假设，提出如下猜想。\n"
            "3 主要结果：定理 1 成立。引理 2 给出上界。证明：由归纳法与反证，构造映射，"
            "利用不等式与范数估计，得到收敛性与唯一性，证毕。分析其时间复杂度为多项式。\n"
            "4 结论：证明了该算子的有界性与存在唯一性，推广了已有结果。")
    s = app.extract_sections(math)
    q_gen = app.analyze_paper_quality(math, s, mode="teacher", subject="general")
    q_thy = app.analyze_paper_quality(math, s, mode="teacher", subject="theory")
    assert q_thy["dimensions"]["method"]["score"] > q_gen["dimensions"]["method"]["score"]


def test_structure_detects_experimental_method_heading():
    """实验型论文的「实验方法」标题应被识别为方法章节（此前只认「方法」）。"""
    txt = ("摘要：本文制备了某涂层并测试性能。\n"
           "2 实验方法\n采用溶胶凝胶法制备样品，用扫描电镜表征形貌，测量膜厚与附着力，并做极化曲线测试耐蚀性。\n"
           "3 结果与讨论\n涂层致密，耐蚀性显著提升。\n")
    s = app.extract_sections(txt)
    assert s.get("method"), "「实验方法」章节未被识别为方法段"
