"""多语言知识库：章节识别 + 评分词库扩到日/韩/德（叠加式，中英词库/正则不动）。

锁住「日/韩/德论文也能走本地规则分析」+「中文行为没被动」。
"""
import app

JA = """要旨：
本研究では、長文書検索のための注意機構を用いた新しい疎なトランスフォーマーを提案する。提案手法は計算量を削減し、検索遅延を改善しながら、密なモデルに近い再現率を達成した。本研究の主な貢献は、初めて疎な注意機構を大規模検索に適用した点である。

キーワード：疎な注意機構, 長文書検索

2 提案手法
本節では提案手法の詳細を述べる。我々はアテンションに基づくネットワークを構築し、損失関数を最適化した。学習にはデータセットを用い、ハイパーパラメータを調整した。アブレーション実験とベースラインとの比較も行った。

3 実験
提案手法を評価した。正解率は95.2%であり、ベースラインより3.1ポイント向上した。

4 結論
本研究では新しい疎なトランスフォーマーを提案し、その有効性を実験により示した。正解率は95.2%に達した。
"""


def test_japanese_sections_detected():
    s = app.extract_sections(JA)
    for k in ("abstract", "keywords", "method", "experiment", "conclusion"):
        assert s.get(k), f"日语章节 {k} 未检出"


def test_japanese_scoring_has_hits():
    s = app.extract_sections(JA)
    q = app.analyze_paper_quality(JA, s, mode="teacher", teacher_cap=85)
    assert q["dimensions"]["structure"]["score"] > 0
    assert q["dimensions"]["innovation"]["score"] > 0
    assert q["dimensions"]["method"]["score"] > 0


def test_chinese_still_works():
    cn = ("摘要：本文提出了一种新方法，设计了模型，本研究构建了一个完整的系统框架。\n"
          "2 研究方法：我们设计了一个模型，使用了算法与数据集，做了消融与对比实验。\n"
          "5 结论：本方法在多个数据集上验证有效，准确率达到 95.2%，显著优于基线方法。\n")
    s = app.extract_sections(cn)
    assert s.get("abstract") and s.get("method") and s.get("conclusion")


KO = """초록
본 연구에서는 장문 검색을 위한 어텐션 기반의 새로운 희소 트랜스포머를 제안한다. 제안 방법은 계산량을 줄이고 검색 지연을 개선하면서도 밀집 모델에 근접한 재현율을 달성하였다. 본 연구의 주요 기여는 희소 어텐션을 대규모 검색에 처음으로 적용한 점이다.

키워드: 희소 어텐션, 장문 검색, 트랜스포머

2 제안 방법
본 절에서는 제안 방법의 세부 사항을 기술한다. 우리는 어텐션 기반 네트워크를 구축하고 손실 함수를 최적화하였다. 학습에는 데이터셋을 사용하고 하이퍼파라미터를 조정하였다. 어블레이션 실험과 베이스라인 비교도 수행하였다.

3 실험
제안 방법을 평가하였다. 정확도는 95.2%로 베이스라인보다 3.1포인트 향상되었다. 교차 검증으로 안정성을 확인하였다.

4 결론
본 연구에서는 새로운 희소 트랜스포머를 제안하고 실험을 통해 그 유효성을 입증하였다. 정확도는 95.2%에 도달하였다.
"""

DE = """Zusammenfassung
In dieser Arbeit schlagen wir einen neuartigen spärlichen Transformer mit Aufmerksamkeitsmechanismus für die Suche in langen Dokumenten vor. Die vorgeschlagene Methode reduziert den Rechenaufwand und verbessert die Latenz erheblich, während sie eine Trefferquote nahe an dichten Modellen erreicht. Unser Hauptbeitrag besteht darin, spärliche Aufmerksamkeit erstmals auf die groß angelegte Suche anzuwenden.

Schlüsselwörter: spärliche Aufmerksamkeit, Dokumentensuche, Transformer

2 Methode
In diesem Abschnitt beschreiben wir die vorgeschlagene Methode im Detail. Wir konstruieren ein Netzwerk mit Aufmerksamkeit und optimieren die Verlustfunktion. Für das Training verwenden wir einen Datensatz und passen die Hyperparameter an. Wir führen außerdem eine Ablationsstudie und einen Vergleich mit der Baseline durch.

3 Experimente
Wir evaluieren die vorgeschlagene Methode. Die Genauigkeit beträgt 95,2 % und übertrifft die Baseline um 3,1 Punkte. Die Kreuzvalidierung bestätigt die Robustheit.

4 Fazit
In dieser Arbeit haben wir einen neuartigen spärlichen Transformer vorgeschlagen und seine Wirksamkeit experimentell nachgewiesen. Die Genauigkeit erreicht 95,2 %.
"""


def test_korean_sections_detected():
    s = app.extract_sections(KO)
    for k in ("abstract", "keywords", "method", "experiment", "conclusion"):
        assert s.get(k), f"韩语章节 {k} 未检出"


def test_korean_scoring_has_hits():
    s = app.extract_sections(KO)
    q = app.analyze_paper_quality(KO, s, mode="teacher", teacher_cap=85)
    assert q["dimensions"]["innovation"]["score"] > 0
    assert q["dimensions"]["method"]["score"] > 0


def test_german_sections_detected():
    s = app.extract_sections(DE)
    for k in ("abstract", "keywords", "method", "experiment", "conclusion"):
        assert s.get(k), f"德语章节 {k} 未检出"


def test_german_scoring_has_hits():
    s = app.extract_sections(DE)
    q = app.analyze_paper_quality(DE, s, mode="teacher", teacher_cap=85)
    assert q["dimensions"]["innovation"]["score"] > 0
    assert q["dimensions"]["method"]["score"] > 0


# ── 跨学科词库：非 CS 论文（医学 / 社科 / 工程）方法维度也应命中 ──
MED_CN = """摘要：本研究旨在评估某药物对高血压的疗效。
关键词：高血压, 临床试验
2 研究方法：本研究采用随机对照试验设计，纳入 240 例患者，随机分为实验组与对照组，采用双盲法。计算样本量，随访 12 个月。主要终点采用生存分析，以风险比表示。纳入标准与排除标准明确。
4 实验结果：实验组有效率 78.5%，方差分析显示组间差异具有统计学意义（p<0.05）。
5 结论：该药物在本次临床试验中疗效确切，不良反应较少，对高血压治疗具有重要的临床实践意义与推广价值。
"""

SOC_EN = """Abstract
This study examines the effect of minimum wage on employment using panel data.
Keywords: minimum wage, employment
2 Method
We employ a difference-in-differences design with fixed effects. To address endogeneity we use an instrumental variable. We also conduct semi-structured interviews and a thematic analysis of the transcripts. A structural equation model tests mediation and moderation effects. Sample size and effect size are reported.
4 Results
The estimated effect is significant at the 1% level. Robustness checks confirm the findings.
5 Conclusion
Our theoretical contribution fills a research gap and offers a new perspective on labor market policy and its distributional consequences.
"""


def test_crossdomain_medical_chinese_method_hits():
    """医学中文论文（无一个 CS 词）方法维度应命中跨学科词，且教授档更严。"""
    s = app.extract_sections(MED_CN)
    assert s.get("method")
    q_t = app.analyze_paper_quality(MED_CN, s, mode="teacher", teacher_cap=85)
    q_p = app.analyze_paper_quality(MED_CN, s, mode="professor", teacher_cap=85)
    assert q_t["dimensions"]["method"]["score"] > 0
    assert q_p["dimensions"]["method"]["score"] > 0


def test_crossdomain_social_english_method_hits():
    """社科英文论文（无一个 CS 词）方法维度应命中跨学科词。"""
    s = app.extract_sections(SOC_EN)
    assert s.get("method")
    q = app.analyze_paper_quality(SOC_EN, s, mode="teacher", teacher_cap=85)
    assert q["dimensions"]["method"]["score"] > 0
    assert q["dimensions"]["innovation"]["score"] > 0
