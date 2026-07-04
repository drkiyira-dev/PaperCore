"""文本质量闸 _text_quality_ok：挡掉坏字体乱码，让下游走 OCR 兜底。

锁住：① (cid:N) 型坏字体乱码被判为不合格；② 正常中英文合格；③ 空文本不合格。
"""
import app


def test_normal_chinese_english_ok():
    assert app._text_quality_ok("本文提出了一种新方法，在数据集上验证有效，准确率达到 95.2%。")
    assert app._text_quality_ok("This paper proposes a novel method validated on ImageNet with 95.2% accuracy.")


def test_cid_garbage_rejected():
    """坏字体（无 ToUnicode 的 CID 字体）渲染成的 (cid:N) 记号应被判为乱码。"""
    garbage = " ".join(f"(cid:{i%50})" for i in range(400))
    assert not app._text_quality_ok(garbage)


def test_control_char_garbage_rejected():
    """控制符/箭头符号型乱码也应被拒。"""
    garbage = "".join(chr(0x2790 + (i % 20)) for i in range(400))
    assert not app._text_quality_ok(garbage)


def test_empty_rejected():
    assert not app._text_quality_ok("")
    assert not app._text_quality_ok("   \n  ")


def test_stray_cid_does_not_false_reject():
    """正常长文里偶尔混一两个 (cid:1) 不应被误判为乱码。"""
    good = "本文提出了一种新方法。" * 50 + "(cid:1)"
    assert app._text_quality_ok(good)


def _watermarked(n_content=100):
    lines = []
    for i in range(n_content):
        lines.append(f"正文第{i}行：本文提出方法{i}，在数据集上验证，准确率{i}%。")
        if i % 10 == 0:  # 模拟每页一次水印 + 页眉
            lines.append("下载于 http://chinaxiv.org 预印本")
            lines.append("JOURNAL OF TEST Vol.38 No.4")
    return "\n".join(lines)


def test_strip_removes_watermark_keeps_content():
    """默认去重应去掉跨页重复的水印/页眉，且保留全部正文。"""
    out = app._strip_repeated_lines(_watermarked(), aggressive=False)
    assert "下载于" not in out
    assert "JOURNAL OF TEST" not in out
    assert all(f"方法{i}" in out for i in range(100))


def test_strip_safety_valve_on_furniture_heavy():
    """家具占比过高（>30%）时判误伤，原样返回（不删）。"""
    raw = "\n".join(["下载于 XX", "页眉 YY", "少量正文一行"] * 10)
    assert app._strip_repeated_lines(raw, aggressive=False) == raw


def test_strip_no_false_positive_on_clean_text():
    """无重复的正常文本应原样返回。"""
    clean = "\n".join(f"这是第{i}行完全不同的正文内容。" for i in range(50))
    assert app._strip_repeated_lines(clean, aggressive=False) == clean
