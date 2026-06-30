"""日语知识库：章节识别 + 评分词库扩到日语（叠加式，中英词库/正则不动）。

锁住「日语论文也能走本地规则分析」+「中文行为没被动」。
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
