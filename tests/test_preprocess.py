"""C5 · 扫描件 OCR 保守自适应预处理（app._preprocess_for_ocr）。

重点验证「绝不拖垮 OCR」：正常页保留文字、异常输入一律安全回退、不崩。
"""
import numpy as np
import cv2
import app


def _black_pct(im):
    g = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)
    return float((g < 128).mean())


def _doc_page():
    """白底 + 几条黑色文字条，模拟一页扫描文档。"""
    img = np.full((400, 600, 3), 255, np.uint8)
    for y in (80, 160, 240):
        cv2.rectangle(img, (50, y), (550, y + 30), (0, 0, 0), -1)
    return img


def test_clean_page_returns_valid_rgb():
    out = app._preprocess_for_ocr(_doc_page())
    assert out.shape == (400, 600, 3)
    assert out.dtype == np.uint8
    # 文字保留：黑占比既不为 0（丢字）也不爆表（糊成片）
    assert 0.0 < _black_pct(out) < 0.5


def test_skewed_page_handled_without_losing_text():
    img = _doc_page()
    M = cv2.getRotationMatrix2D((300, 200), 5, 1.0)
    sk = cv2.warpAffine(img, M, (600, 400), borderValue=(255, 255, 255))
    out = app._preprocess_for_ocr(sk)
    assert out.shape == (400, 600, 3)
    assert 0.0 < _black_pct(out) < 0.5


def test_all_white_falls_back_no_crash():
    out = app._preprocess_for_ocr(np.full((200, 200, 3), 255, np.uint8))
    assert out.shape[2] == 3            # 不崩、仍是 3 通道供 OCR


def test_all_black_falls_back_no_crash():
    out = app._preprocess_for_ocr(np.zeros((200, 200, 3), np.uint8))
    assert out.shape[2] == 3


def test_none_input_passthrough():
    assert app._preprocess_for_ocr(None) is None


def test_non_3channel_passthrough():
    g = np.zeros((50, 50), np.uint8)
    assert app._preprocess_for_ocr(g) is g     # 非 3 通道原样返回，不处理
