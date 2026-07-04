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
