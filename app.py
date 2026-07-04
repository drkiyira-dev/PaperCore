"""
PaperCore · 本地优先的论文核心内容提取系统 - Flask 主程序
版本：2.0
作者：朱厚臻（PaperCore 团队）
日期：2026 年
"""

import os

# ── 强制离线：本地优先工具绝不能因联网而卡死 ──────────────────────────────
# docling 在 convert() 时会联网校验/拉取 HuggingFace 模型，且该调用没有超时。
# 现场会场的 captive-portal WiFi（要点“同意”才放行那种）会劫持并挂起该请求，
# 导致 docling 无限期卡住、整个上传请求挂死（答辩现场即为此症状）。
# 在导入任何重型库之前强制离线：只用本地已缓存模型；缺失则立即报错并降级到
# pdfplumber/PyPDF2，永不联网、永不挂死——同时让“断网可复现”名副其实。
# 如确需联网下载模型，在启动前显式设 HF_HUB_OFFLINE=0 即可覆盖。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import json
import time
import threading
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, redirect
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from docling.document_converter import DocumentConverter
import pdfplumber
import PyPDF2

load_dotenv()

from rules import match_rules, RULES
from ai_engines import AIEngine, CloudAPI, OllamaAPI, V4ProAPI
from utils import chunk_text
import history  # 本地分析历史（local-first 持久化，见 history.py）
import docnames  # 上传文件「原始中文名」映射（见 docnames.py）
import report   # 结构化报告生成（报告中心 / 批量导出，见 report.py）
import semantic  # 轻量语义增强（C6：词库覆盖→语义相似度补；模型不可用时优雅回退，见 semantic.py）
import usage    # v4pro 高级模式滑动配额（见 usage.py）
import experience  # 体验区（公网试用）：按访客配额/熔断/留邮箱/成本埋点（仅 EXPERIENCE_MODE=1 启用，见 experience.py）

# docling 可选依赖，失败时降级到 pdfplumber。
# 关键：关掉 docling 自带 OCR（do_ocr=False）——它对中文扫描件会吐乱码且非空，
# 反而会挡住下游更准的 RapidOCR 兜底。这里让 docling 只负责「文字层结构化抽取」，
# 扫描件交给后面的 RapidOCR（中文识别更可靠）。配置 API 不可用时回退默认构造。
try:
    from docling.document_converter import DocumentConverter as _DocConverter
    try:
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        _po = PdfPipelineOptions(do_ocr=False)
        _converter = _DocConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_po)})
    except Exception as _e2:
        print(f"[docling] do_ocr=False 配置不可用，使用默认构造: {_e2}")
        _converter = _DocConverter()
    DOCLING_OK = True
except Exception as _e:
    print(f"[docling] 初始化失败，已跳过: {_e}")
    _converter = None
    DOCLING_OK = False

# RapidOCR 可选依赖：用于扫描件 / 无文字层 PDF 的本地 OCR 兜底。
# 纯本地 ONNX 推理，自带中英文模型，不联网、不调用云端——契合 local-first。
# 引擎首次初始化较慢，故懒加载：仅在前几种文字层抽取全失败时才触发。
_ocr_engine = None
try:
    import pypdfium2 as pdfium  # 把 PDF 页渲染成位图喂给 OCR（Apache/BSD 许可，替代 AGPL 的 PyMuPDF）
    from rapidocr_onnxruntime import RapidOCR as _RapidOCR
    OCR_OK = True
except Exception as _e:
    print(f"[OCR] RapidOCR/pypdfium2 不可用，扫描件 OCR 兜底已跳过: {_e}")
    OCR_OK = False


def _get_ocr_engine():
    """懒加载 RapidOCR 引擎（首次调用才初始化，避免拖慢启动）。"""
    global _ocr_engine
    if _ocr_engine is None and OCR_OK:
        print("[OCR] 首次初始化 RapidOCR 引擎...")
        _ocr_engine = _RapidOCR()
    return _ocr_engine


def _order_ocr_lines(result, page_width, min_score=0.5):
    """
    把 RapidOCR 的原始结果整理成「正确阅读顺序」的文本行。

    RapidOCR 每条返回 [box, text, score]：box 是 4 个角点、score 是置信度。
    旧实现只取 text、按识别顺序拼接——双栏论文会被读成「左一行右一行」的乱序，
    低置信度噪声也混进正文。这里把 box 和 score 用起来：
      1) 先按 score 过滤掉低置信度噪声；
      2) 估计版面是单栏还是双栏，双栏则「先整列左、再整列右」；
      3) 同列内按 y（行）→ x（行内左右）排序。
    任何异常都回退到原始识别顺序，绝不让排序逻辑本身弄丢文本。
    """
    try:
        items = []
        for line in result:
            box, text, score = line[0], line[1], line[2]
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 1.0
            if not text or score < min_score:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            items.append({
                "text": text,
                "x_left": min(xs),
                "x_center": (min(xs) + max(xs)) / 2.0,
                "y_top": min(ys),
                "height": max(ys) - min(ys),
            })
        if not items:
            return []

        # 估计是否双栏：看落在页面左半 / 右半的文本块各有多少。
        mid = page_width / 2.0
        left_cnt = sum(1 for it in items if it["x_center"] < mid)
        right_cnt = len(items) - left_cnt
        two_col = (left_cnt >= len(items) * 0.25 and right_cnt >= len(items) * 0.25)

        # 行容差：用中位字高的一半，把同一行的碎块归进同一「行带」，避免抖动乱序。
        heights = sorted(it["height"] for it in items if it["height"] > 0)
        row_tol = (heights[len(heights) // 2] / 2.0) if heights else 8.0
        if row_tol <= 0:
            row_tol = 8.0

        def sort_key(it):
            col = 0 if (not two_col or it["x_center"] < mid) else 1
            row_band = round(it["y_top"] / row_tol)
            return (col, row_band, it["x_left"])

        items.sort(key=sort_key)
        return [it["text"] for it in items]
    except Exception as e:
        print(f"[OCR] 阅读顺序整理失败，回退原始顺序：{e}")
        return [line[1] for line in result if len(line) > 1 and line[1]]


def _preprocess_for_ocr(img):
    """扫描件 OCR 前的保守自适应预处理（C5 / 答辩 P16 边界①·重档 B）。

    灰度 → 仅在检测到明显倾斜(0.5°~15°)时纠偏 deskew → 自适应二值化；
    任一步异常、或二值化后前景占比异常（过低=丢字 / 过高=糊成片）一律回退，
    确保「只帮低质扫描件，绝不拖累清晰或彩色扫描件」。返回 3 通道 ndarray 供 RapidOCR。
    """
    try:
        import cv2
        import numpy as np
        if img is None or getattr(img, "ndim", 0) != 3:
            return img
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # —— deskew：用 Otsu 前景估计整页倾斜角，仅在 0.5°~15° 之间才纠（避免误转好图）——
        try:
            inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            coords = np.column_stack(np.where(inv > 0))
            if coords.shape[0] > 50:
                angle = cv2.minAreaRect(coords)[-1]
                if angle < -45:
                    angle = 90.0 + angle
                if 0.5 < abs(angle) < 15:
                    h, w = gray.shape
                    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
                    gray = cv2.warpAffine(gray, M, (w, h),
                                          flags=cv2.INTER_CUBIC,
                                          borderMode=cv2.BORDER_REPLICATE)
        except Exception:
            pass  # deskew 失败不影响后续步骤

        # —— 自适应二值化（应对不均匀光照）+ 过度二值化回退 ——
        binimg = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 31, 15)
        black_ratio = float((binimg < 128).mean())
        if black_ratio < 0.002 or black_ratio > 0.5:
            # 前景过少（几乎全白=丢字）/过多（几乎全黑=糊片）→ 二值化大概率帮倒忙，只用灰度
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        return cv2.cvtColor(binimg, cv2.COLOR_GRAY2RGB)
    except Exception as e:
        print(f"[OCR] 预处理跳过，回退原图：{e}")
        return img


def ocr_pdf(file_path, dpi=300, max_pages=30, min_score=0.5):
    """
    对扫描件 / 无文字层 PDF 做本地 OCR。
    逐页用 pypdfium2 渲染为位图 → RapidOCR 识别 → 按版面阅读顺序拼接为纯文本。
    全程本地 ONNX 推理，不出网；为控制时延，默认最多处理前 max_pages 页。
    """
    if not OCR_OK:
        return ""
    import numpy as np
    engine = _get_ocr_engine()
    if engine is None:
        return ""
    parts = []
    try:
        pdf = pdfium.PdfDocument(file_path)
    except Exception as e:
        print(f"[OCR] 打开 PDF 失败：{e}")
        return ""
    try:
        n_pages = len(pdf)
        for i in range(n_pages):
            if i >= max_pages:
                print(f"[OCR] 已达页数上限 {max_pages}，停止")
                break
            try:
                page = pdf.get_page(i)
                # scale = dpi/72（72 为 PDF 基准分辨率）。300 DPI 比旧的 200 更清晰，利于小字/公式。
                bitmap = page.render(scale=dpi / 72.0)
                img = np.array(bitmap.to_pil().convert("RGB"))  # 复制一份，随后即可释放渲染对象
                bitmap.close()
                page.close()
                img = _preprocess_for_ocr(img)   # C5·保守自适应预处理（灰度 / 限角 deskew / 自适应二值化，异常回退原图）
                result, _elapse = engine(img)
                if result:
                    lines = _order_ocr_lines(result, img.shape[1], min_score=min_score)
                    if lines:
                        parts.append("\n".join(lines))
            except Exception as e:
                print(f"[OCR] 第 {i+1} 页识别失败：{e}")
    finally:
        pdf.close()
    text = "\n".join(parts)
    print(f"[OCR] 识别完成，共 {len(parts)} 页有文本，总长度 {len(text)}")
    return text

# ==================== 分析模式 Prompt ====================

ANALYSIS_PROMPTS = {
    "quick": (
        "你是专业的学术论文分析助手。请阅读以下论文内容，提取最关键的三要素。\n"
        "严格返回JSON格式，不要添加任何markdown代码块或额外解释，直接输出JSON：\n"
        '{{"research_question": "研究问题（2-3句）", "core_method": "核心方法（2-3句）", "conclusion": "主要结论（2-3句）"}}\n\n'
        "论文内容：\n{text}"
    ),
    "structured": (
        "你是专业的学术论文分析助手，擅长工科论文结构化理解。请深度分析以下论文，提取八维结构化信息。\n"
        "严格返回JSON格式，不要添加任何markdown代码块或额外解释，直接输出JSON：\n"
        '{{"research_question": "研究问题", "core_method": "核心方法（含方法路线）", '
        '"key_formulas": ["公式描述1", "公式描述2"], '
        '"experimental_data": "关键实验数据与对比结果", '
        '"conclusion": "主要结论", "innovations": ["创新点1", "创新点2"], '
        '"potential_risks": ["潜在局限或风险1", "潜在局限或风险2"], '
        '"improvement_suggestions": ["建议修改方向1", "建议修改方向2"]}}\n\n'
        "论文内容：\n{text}"
    ),
    "formula": (
        "你是工科论文技术内容提取专家。请精确提取以下论文中的所有关键技术内容，保持数值精确性。\n"
        "严格返回JSON格式，不要添加任何markdown代码块或额外解释，直接输出JSON：\n"
        '{{"formulas": [{{"name": "公式名称", "expression": "公式表达式", "meaning": "物理/数学含义"}}], '
        '"variables": [{{"symbol": "符号", "definition": "定义"}}], '
        '"experiment_setup": "实验设置与参数", '
        '"key_results": ["关键结果1（含数值）", "关键结果2（含数值）"]}}\n\n'
        "论文内容：\n{text}"
    ),
    "defense": (
        "你是学术答辩辅导专家。请为以下论文生成完整的答辩汇报材料，帮助答辩人做好准备。\n"
        "严格返回JSON格式，不要添加任何markdown代码块或额外解释，直接输出JSON：\n"
        '{{"background": "研究背景与动机（3-4句）", '
        '"innovations": ["核心创新点1", "核心创新点2", "核心创新点3"], '
        '"highlights": "实验亮点（含关键数据支撑）", '
        '"qa_pairs": [{{"q": "可能被问到的问题", "a": "简要回答"}}]}}\n\n'
        "论文内容：\n{text}"
    ),
}

# ==================== 配置区 ====================

app = Flask(__name__, static_url_path='/static')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB上传限制
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# 配置上传文件夹路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')

# 确保目录存在
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
    os.makedirs(folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt', 'md'}

# 初始化组件
ai_engine = AIEngine()


# ==================== 工具函数 ====================

def extract_sections(text):
    """从论文文本中提取摘要/方法/结论等主要章节，返回 dict。"""
    import re
    sections = {}

    # 章节标题前缀：允许"2 "/"2."/"2、"/"二、"/"III." 等编号（含 IEEE 罗马数字）
    _NUM = r'(?:(?:[\d一二三四五六七八九十]+|[IVXLC]{1,6})\s*[\.、\s]?\s*)?'

    def _grab(text, patterns, stop_patterns):
        """匹配第一个命中的章节，截断到下一个大节。"""
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if m:
                chunk = m.group(1).strip()
                if stop_patterns:
                    stop_re = r'\n[ \t]*(?:' + '|'.join(stop_patterns) + r')'
                    chunk = re.split(stop_re, chunk, maxsplit=1)[0]
                return chunk.strip()
        return None

    # ── 摘要 ──────────────────────────────────────────────────────────────
    v = _grab(text, [
        r'(?:^|\n)[ \t]*' + _NUM + r'摘\s*要[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:要\s*旨|概\s*要|抄\s*録|アブストラクト)[ \t]*[\n：:]([\s\S]{30,})',  # 日本語
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:초\s*록|요\s*약|개\s*요)[ \t]*[\n：:]([\s\S]{30,})',  # 한국어
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Zusammenfassung|Kurzfassung|Abstrakt)[ \t]*[\n：:]([\s\S]{30,})',  # Deutsch
        r'(?:^|\n)[ \t]*' + _NUM + r'Abstract[ \t]*\n([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'Abstract\s*[—–\-][ \t]*([\s\S]{30,})',  # IEEE "Abstract—..."
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:项目概述|研究背景|背景与意义)[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)#{1,3}\s*(?:Abstract|摘要|要旨)[^\n]*\n([\s\S]{30,})',
    ], [r'关键词', r'Keywords?', r'Index\s*Terms?', r'引言', r'Introduction', r'キーワード', r'はじめに', r'序論', r'키워드', r'서\s*론', r'Schl(?:ü|ue)sselw(?:ö|oe)rter', r'Stichw(?:ö|oe)rter', r'Einleitung', r'\d+\s*[\.、]', r'一、'])
    if v: sections['abstract'] = v[:600]

    # ── 关键词（单行；含 IEEE "Index Terms—"）──────────────────────────────
    m = re.search(r'(?:关键词|关键字|Keywords?|Index\s*Terms?|キーワード|키워드|주제어|색인어|Schl(?:ü|ue)sselw(?:ö|oe)rter|Stichw(?:ö|oe)rter|Schlagw(?:ö|oe)rter)\s*[：:—–\-]\s*([^\n]{5,300})', text, re.IGNORECASE)
    if m: sections['keywords'] = m.group(1).strip()

    # ── 研究方法 ───────────────────────────────────────────────────────────
    v = _grab(text, [
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:研究方法|本文方法|所提方法|资料与方法|材料与方法|对象与方法|方\s*法)[ \t]*[\n：:]([\s\S]{30,})',
        # 实验型论文（材料/化学/生物）方法段常叫「实验方法/实验部分/样品制备」等，不叫「方法」
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:实验\s*方法|试验\s*方法|实验部分|实验设计|实验过程|实验步骤|实验材料|样品制备|材料制备|制备\s*(?:工艺|方法|与表征))[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:提案手法|提案法|提案する手法|手\s*法|システム構成|問題設定|準備)[ \t]*[\n：:]([\s\S]{30,})',  # 日本語
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:제안\s*(?:방법|기법)|연구\s*방법|방\s*법|접근법|시스템\s*구성|문제\s*정의)[ \t]*[\n：:]([\s\S]{30,})',  # 한국어
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Methodik|Methoden|Methode|Vorgeschlagene\s+\w+|Ansatz|Systemmodell|Problemstellung|Vorgehensweise)[ \t]*[\n：:]([\s\S]{30,})',  # Deutsch
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Method(?:ology)?|Approach|Algorithm)[ \t]*\n([\s\S]{30,})',
        # 英文实验型论文：Materials and Methods / Experimental Section 等（≠ 结果段 Experiments/Results）
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Materials?\s+and\s+Methods?|Experimental\s+(?:Section|Details|Setup|Procedures?|Methods?|Methodology|Techniques?)|Sample\s+Preparation|Synthesis(?:\s+and\s+Characterization)?)[ \t]*[\n：:]([\s\S]{30,})',
        # IEEE/工科英文论文的方法章节常不叫 Method，而是 System Model / Problem Formulation / Proposed … 等
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:(?:System|Signal|Channel|Network)\s+(?:Model|Architecture)|Problem\s+(?:Formulation|Statement|Definition)|Proposed\s+[A-Za-z][\w\- ]{0,40}|Preliminaries|Design\s+of\s+\w+)[ \t]*\n([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:技术方案|系统设计|实现方案|系统模型|问题建模|算法设计)[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)#{1,3}\s*(?:\d+\.?\s*)?(?:Method|Research Method|研究方法)[^\n]*\n([\s\S]{30,})',
    ], [r'实验', r'仿真', r'结论', r'总结', r'Experiment', r'Result', r'Conclusion', r'実験', r'評価', r'結論', r'おわりに', r'실\s*험', r'평\s*가', r'결\s*과', r'결\s*론', r'Ergebnisse', r'Evaluierung', r'Auswertung', r'Fazit', r'Schlussfolgerung', r'\d+\s*[\.、]'])
    if v: sections['method'] = v[:500]

    # ── 实验/结果 ──────────────────────────────────────────────────────────
    v = _grab(text, [
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:实\s*验(?:结果|与分析)?|仿\s*真(?:实验)?|结\s*果(?:与分析|与讨论)?)[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:実\s*験(?:結果)?|評\s*価(?:実験)?|結\s*果(?:と考察)?)[ \t]*[\n：:]([\s\S]{30,})',  # 日本語
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:실\s*험(?:\s*결과)?|평\s*가|결\s*과(?:\s*및\s*분석)?)[ \t]*[\n：:]([\s\S]{30,})',  # 한국어
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Experimente|Experiment|Evaluierung|Auswertung|Ergebnisse)[ \t]*[\n：:]([\s\S]{30,})',  # Deutsch
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Experiment(?:al\s*Results?)?|Simulation(?:\s*Results?)?|Results?)[ \t]*\n([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:测试结果|验证实验|效果评估)[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)#{1,3}\s*(?:\d+\.?\s*)?(?:Experiment|Results|实验结果|実験)[^\n]*\n([\s\S]{30,})',
    ], [r'结论', r'总结', r'讨论', r'Conclusion', r'Discussion', r'結論', r'おわりに', r'まとめ', r'결\s*론', r'맺음말', r'Fazit', r'Schlussfolgerung', r'\d+\s*[\.、]'])
    if v: sections['experiment'] = v[:500]

    # ── 结论 ───────────────────────────────────────────────────────────────
    v = _grab(text, [
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:结\s*论|结\s*语|结\s*束\s*语|总\s*结|小\s*结)[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:結\s*論|結\s*言|おわりに|まとめ|結びに)[ \t]*[\n：:]([\s\S]{30,})',  # 日本語
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:결\s*론|맺음말|요약\s*및\s*결론)[ \t]*[\n：:]([\s\S]{30,})',  # 한국어
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Fazit|Schlussfolgerung(?:en)?|Schluss|Zusammenfassung\s+und\s+Ausblick)[ \t]*[\n：:]([\s\S]{30,})',  # Deutsch
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:Conclusion|CONCLUSION|Summary)[ \t]*\n([\s\S]{30,})',
        r'(?:^|\n)[ \t]*' + _NUM + r'(?:总结与展望|项目总结|预期成果)[ \t]*[\n：:]([\s\S]{30,})',
        r'(?:^|\n)#{1,3}\s*(?:\d+\.?\s*)?(?:Conclusion|结论|結論)[^\n]*\n([\s\S]{30,})',
    ], [r'参考文献', r'致\s*谢', r'References?', r'Acknowledg', r'謝\s*辞', r'참고\s*문헌', r'감사의\s*글', r'Literatur(?:verzeichnis)?', r'Danksagung'])
    if v: sections['conclusion'] = v[:600]

    return sections


# ==================== 四维评分档案：温和 / 严格两套，各自独立维护 ====================
# 评分尺度由用户在分析时选择（score_mode）。三套词库刻意分开列、互不为子集关系：
#   老师档——词全、系数松，内容扎实就容易拿高分（鼓励为主）；总分上限由前端滑块定（70~100，默认 85）
#   专家档——去掉最泛的词、留实义信号，系数中等（同行视角，封顶 72）
#   教授档——只认「强信号」硬词、系数最低、数据只认带单位/小数的定量（审稿视角，封顶 65）
# 三档可各自迭代，互不影响。数据维度按「量化值个数」分级给分（不再“有数字就满分”，治虚高）。
# 结构维度（章节识别）是客观的，三档共用、不分松紧；最终总分按各档上限裁剪。

_INNOVATION_KW_TEACHER = [
    # 中文：提出/设计/贡献类
    "本文提出", "本文设计", "本文构建", "本文实现", "本文开发", "本研究提出",
    "提出了一种", "设计了一种", "首次", "本文首次", "创新", "创新性", "创造性",
    "新型", "新方法", "新框架", "新模型", "全新", "改进了", "优于", "领先",
    "超越", "最优", "突破", "主要贡献", "核心贡献", "关键贡献", "核心创新",
    "关键创新", "创新点", "贡献如下", "主要工作", "独特", "有效改善",
    "显著提升", "大幅提升", "填补空白",
    "原创", "原创性", "开创性", "开创了", "前所未有", "首创", "率先",
    "引领", "新颖", "弥补了不足", "优势明显",
    # 英文：propose / contribution / SOTA 类
    "we propose", "we present", "we introduce", "we develop", "we design",
    "propose a novel", "novel", "contribution", "key contribution",
    "main contribution", "our contributions", "key innovation",
    "outperform", "significantly outperform", "surpass", "superior to",
    "state-of-the-art", "sota", "achieve state-of-the-art",
    "to the best of our knowledge", "first to", "improvement over",
    "breakthrough", "our method",
    "pioneering", "unprecedented", "cutting-edge", "first of its kind",
    "we are the first to", "advance the state", "novel approach",
    "novel framework", "novel method",
]

_METHOD_KW_TEACHER = [
    # 中文：流程/结构
    "步骤", "流程", "算法", "框架", "模型", "网络", "模块", "结构", "架构",
    "参数", "超参数", "训练", "优化", "损失", "损失函数", "函数", "公式",
    "实验设置", "数据集", "对比", "消融", "基线",
    # 中文：深度学习/工程术语
    "卷积", "池化", "注意力", "多头注意力", "自注意力", "编码器", "解码器",
    "嵌入", "词向量", "正则化", "梯度", "反向传播", "梯度下降", "学习率",
    "批大小", "迭代", "收敛", "激活函数", "归一化", "残差", "预训练", "微调",
    "评价指标", "准确率", "召回率", "精确率", "交叉验证", "端到端",
    "特征提取", "特征融合", "采样", "权重", "阈值", "相似度",
    "迁移学习", "强化学习", "自监督", "半监督", "无监督", "对比学习",
    "知识蒸馏", "蒸馏", "剪枝", "量化", "泛化", "鲁棒性", "生成对抗",
    "图神经网络", "扩散模型", "零样本", "少样本", "数据增强", "特征工程",
    "集成学习", "聚类", "分类器", "回归", "F1值", "AUC", "混淆矩阵",
    # 英文等价词
    "architecture", "framework", "model", "network", "layer", "module",
    "parameter", "hyperparameter", "training", "optimizer", "loss",
    "function", "dataset", "ablation", "comparison", "epoch", "accuracy",
    "proposed", "approach", "algorithm", "attention", "convolution",
    "pooling", "transformer", "self-attention", "multi-head", "encoder",
    "decoder", "embedding", "regularization", "gradient", "backpropagation",
    "learning rate", "batch", "normalization", "residual", "pretrain",
    "fine-tune", "metric", "precision", "recall", "benchmark",
    "convergence", "activation", "softmax", "dropout", "feature",
    "representation", "end-to-end",
    "transfer learning", "reinforcement learning", "self-supervised",
    "semi-supervised", "unsupervised", "contrastive learning",
    "knowledge distillation", "distillation", "pruning", "quantization",
    "generalization", "robustness", "generative adversarial", "gan",
    "graph neural network", "gnn", "diffusion model", "zero-shot",
    "few-shot", "data augmentation", "ensemble", "clustering",
    "classifier", "regression", "f1 score", "auc", "confusion matrix",
]

# 教授档·创新：只认「明确、可验证的强创新声明」，剔除 propose/present/novel 这类谁都写得出的泛词
_INNOVATION_KW_PROFESSOR = [
    "本文提出", "提出了一种", "本文首次", "首次提出", "首次实现", "国内外首次",
    "主要贡献", "核心贡献", "关键贡献", "核心创新", "关键创新", "创新点",
    "突破", "重大突破", "填补空白", "填补了空白", "显著优于", "大幅领先", "远超",
    "we propose", "we propose a novel", "novel", "main contribution",
    "key contribution", "key innovation", "our contribution", "our contributions",
    "outperform", "significantly outperform", "surpass", "superior to",
    "state-of-the-art", "to the best of our knowledge", "first to",
    "for the first time",
    "首创", "前所未有", "开创性",
    "pioneering", "unprecedented", "we are the first to",
]

# 教授档·方法：只认「方法完整性的实质标志」——算法/超参/数据集/消融/基线/复杂度/收敛等，
# 剔除 model/network/layer/function/feature 这类任何技术论文都会出现的通用术语
_METHOD_KW_PROFESSOR = [
    "算法", "算法流程", "伪代码", "框架", "训练", "优化", "损失函数", "目标函数",
    "梯度", "学习率", "批大小", "超参数", "参数设置", "数据集", "消融", "消融实验",
    "基线", "对比实验", "评价指标", "准确率", "召回率", "精确率",
    "时间复杂度", "空间复杂度", "复杂度", "收敛", "收敛性", "交叉验证",
    "实验设置", "正则化", "归一化",
    "algorithm", "framework", "training", "optimizer", "loss function",
    "objective function", "gradient", "learning rate", "batch size",
    "hyperparameter", "dataset", "ablation", "baseline", "evaluation metric",
    "accuracy", "precision", "recall", "complexity", "convergence",
    "cross-validation", "experimental setup",
    "泛化性", "鲁棒性", "消融研究", "统计显著", "显著性检验", "标准差", "置信区间",
    "generalization", "robustness", "ablation study", "statistical significance",
    "standard deviation", "confidence interval", "p-value", "f1 score", "auc",
]

# 专家档·创新（中档）：比老师收掉最泛的词（创造性/全新/独特…），比教授宽——保留 propose/novel 等中强信号
_INNOVATION_KW_EXPERT = [
    "本文提出", "本文设计", "本文构建", "提出了一种", "设计了一种", "首次", "本文首次",
    "创新点", "主要贡献", "核心贡献", "关键贡献", "核心创新", "关键创新",
    "新方法", "新框架", "新模型", "显著提升", "大幅提升", "优于", "超越", "突破", "填补空白",
    "原创性", "开创性", "前所未有", "首创", "率先", "新颖",
    "we propose", "we present", "we introduce", "we design", "propose a novel", "novel",
    "contribution", "key contribution", "main contribution", "our contributions",
    "key innovation", "outperform", "significantly outperform", "surpass", "superior to",
    "state-of-the-art", "sota", "first to", "improvement over", "our method",
    "pioneering", "unprecedented", "cutting-edge", "first of its kind",
    "we are the first to", "novel approach",
]

# 专家档·方法（中档）：去掉 步骤/结构/函数/公式/层/权重/激活 这类最通用的，保留实义方法术语
_METHOD_KW_EXPERT = [
    "算法", "流程", "框架", "模型", "网络", "模块", "架构", "超参数", "参数",
    "训练", "优化", "损失函数", "实验设置", "数据集", "对比", "消融", "基线",
    "卷积", "注意力", "自注意力", "编码器", "解码器", "正则化", "梯度下降", "学习率",
    "批大小", "收敛", "预训练", "微调", "评价指标", "准确率", "召回率", "精确率",
    "交叉验证", "端到端", "特征提取", "特征融合",
    "迁移学习", "强化学习", "自监督", "对比学习", "知识蒸馏", "泛化", "鲁棒性",
    "数据增强", "F1值", "AUC",
    "architecture", "framework", "model", "network", "module", "parameter",
    "hyperparameter", "training", "optimizer", "loss", "dataset", "ablation",
    "comparison", "accuracy", "algorithm", "attention", "convolution", "transformer",
    "encoder", "decoder", "regularization", "gradient", "learning rate", "batch",
    "normalization", "pretrain", "fine-tune", "metric", "precision", "recall",
    "benchmark", "convergence", "end-to-end",
    "transfer learning", "reinforcement learning", "self-supervised",
    "contrastive learning", "knowledge distillation", "generalization",
    "robustness", "data augmentation", "f1 score", "auc",
]

# ── 日本語キーワード（AI 初译，待日语母语校对；叠加进各档，完全不动上面的中英词库）──────
# 各子库呼应所在档严苛度：teacher 最宽 / professor 只认强信号 / expert 居中。匹配为子串，日语无需分词。
_INNOVATION_KW_JA_TEACHER = [
    "提案", "本研究では", "本稿では", "提案する", "提案手法", "新しい", "新規",
    "新たな", "初めて", "世界初", "独自", "独自の", "貢献", "主な貢献",
    "本研究の貢献", "改善", "向上", "大幅に向上", "大幅に改善", "上回る",
    "優れた", "優位", "最先端", "最高性能", "達成", "実現", "構築", "開発",
    "設計", "革新", "革新的", "ブレークスルー", "従来手法より",
    "独創的", "先駆的", "前例のない", "世界に先駆けて", "これまでにない",
    "有効性", "性能向上", "精度向上", "上回った", "凌駕", "優位性",
    "最高水準", "従来研究より",
]
_METHOD_KW_JA_TEACHER = [
    "手法", "提案手法", "アルゴリズム", "モデル", "ネットワーク", "フレームワーク",
    "アーキテクチャ", "モジュール", "パラメータ", "ハイパーパラメータ", "学習",
    "訓練", "最適化", "損失関数", "目的関数", "データセット", "実験設定", "比較",
    "アブレーション", "ベースライン", "評価指標", "正解率", "精度", "再現率",
    "適合率", "畳み込み", "プーリング", "注意機構", "自己注意", "エンコーダ",
    "デコーダ", "埋め込み", "正則化", "勾配", "誤差逆伝播", "学習率",
    "バッチサイズ", "収束", "活性化関数", "正規化", "事前学習",
    "ファインチューニング", "交差検証", "エンドツーエンド", "特徴抽出",
    "転移学習", "強化学習", "自己教師あり学習", "半教師あり学習", "教師なし学習",
    "対照学習", "知識蒸留", "蒸留", "枝刈り", "量子化", "汎化", "頑健性",
    "敵対的生成", "グラフニューラルネットワーク", "拡散モデル", "ゼロショット",
    "データ拡張", "アンサンブル学習", "クラスタリング", "分類器", "F1スコア",
]
_INNOVATION_KW_JA_EXPERT = [
    "提案手法", "本研究では", "本稿では", "初めて", "世界初", "独自", "新規",
    "新たな", "主な貢献", "本研究の貢献", "大幅に向上", "大幅に改善", "上回る",
    "優れた", "最先端", "最高性能", "革新的", "ブレークスルー", "従来手法より",
    "独創的", "先駆的", "前例のない", "これまでにない", "性能向上", "精度向上",
    "上回った", "凌駕", "優位性", "最高水準",
]
_METHOD_KW_JA_EXPERT = [
    "手法", "提案手法", "アルゴリズム", "モデル", "ネットワーク", "フレームワーク",
    "アーキテクチャ", "ハイパーパラメータ", "パラメータ", "学習", "最適化",
    "損失関数", "実験設定", "データセット", "比較", "アブレーション", "ベースライン",
    "評価指標", "精度", "再現率", "適合率", "畳み込み", "注意機構", "エンコーダ",
    "デコーダ", "正則化", "学習率", "バッチサイズ", "収束", "事前学習",
    "ファインチューニング", "交差検証", "エンドツーエンド", "特徴抽出",
    "転移学習", "強化学習", "自己教師あり学習", "対照学習", "知識蒸留",
    "汎化", "頑健性", "データ拡張", "F1スコア", "アンサンブル学習",
]
_INNOVATION_KW_JA_PROFESSOR = [
    "提案手法", "初めて", "世界初", "本研究の貢献", "主な貢献", "大幅に向上",
    "大幅に上回る", "従来手法を上回る", "最先端を上回る", "最高性能",
    "既存手法を上回る", "革新的", "ブレークスルー",
    "前例のない", "世界に先駆けて", "凌駕", "従来手法を凌駕", "最高水準",
]
_METHOD_KW_JA_PROFESSOR = [
    "アルゴリズム", "擬似コード", "学習", "最適化", "損失関数", "目的関数", "勾配",
    "学習率", "バッチサイズ", "ハイパーパラメータ", "パラメータ設定", "データセット",
    "アブレーション", "アブレーション実験", "ベースライン", "比較実験", "評価指標",
    "正解率", "精度", "再現率", "計算量", "時間計算量", "空間計算量", "収束",
    "収束性", "交差検証", "実験設定",
    "汎化性", "頑健性", "統計的有意性", "標準偏差", "信頼区間", "F1スコア",
]

# ── 한국어 키워드（AI 初译，待韩语母语校对；叠加进各档，完全不动上面的中英日词库）──────
# 与日语同思路：teacher 最宽 / professor 只认强信号 / expert 居中。子串匹配，韩语无需分词。
_INNOVATION_KW_KO_TEACHER = [
    "제안", "본 연구에서는", "본 논문에서는", "제안한다", "제안하는", "제안 방법",
    "제안하는 방법", "새로운", "신규", "참신한", "새롭게", "처음으로", "최초로",
    "세계 최초", "독창적", "독자적", "기여", "주요 기여", "본 연구의 기여",
    "개선", "향상", "크게 향상", "대폭 개선", "능가", "우수한", "우위",
    "최첨단", "최고 성능", "달성", "실현", "구축", "개발", "설계", "혁신",
    "혁신적", "획기적", "기존 방법보다", "기존 연구보다",
]
_METHOD_KW_KO_TEACHER = [
    "방법", "제안 방법", "알고리즘", "모델", "네트워크", "프레임워크", "아키텍처",
    "구조", "모듈", "파라미터", "하이퍼파라미터", "학습", "훈련", "최적화",
    "손실 함수", "목적 함수", "데이터셋", "실험 설정", "비교", "어블레이션",
    "절제 연구", "베이스라인", "기준선", "평가 지표", "정확도", "정밀도",
    "재현율", "합성곱", "컨볼루션", "풀링", "어텐션", "셀프 어텐션", "인코더",
    "디코더", "임베딩", "정규화", "그래디언트", "역전파", "학습률", "배치 크기",
    "수렴", "활성화 함수", "사전 학습", "파인 튜닝", "미세 조정", "교차 검증",
    "종단간", "특징 추출", "특징", "가중치",
]
_INNOVATION_KW_KO_EXPERT = [
    "제안 방법", "제안하는 방법", "본 연구에서는", "본 논문에서는", "처음으로",
    "최초로", "세계 최초", "독창적", "신규", "참신한", "주요 기여", "본 연구의 기여",
    "크게 향상", "대폭 개선", "능가", "우수한", "최첨단", "최고 성능", "혁신적",
    "획기적", "기존 방법보다",
]
_METHOD_KW_KO_EXPERT = [
    "알고리즘", "제안 방법", "프레임워크", "아키텍처", "하이퍼파라미터", "학습",
    "최적화", "손실 함수", "실험 설정", "데이터셋", "비교", "어블레이션",
    "베이스라인", "평가 지표", "정확도", "재현율", "정밀도", "합성곱", "어텐션",
    "인코더", "디코더", "정규화", "학습률", "배치 크기", "수렴", "사전 학습",
    "파인 튜닝", "교차 검증", "종단간", "특징 추출",
]
_INNOVATION_KW_KO_PROFESSOR = [
    "제안 방법", "최초로", "세계 최초", "본 연구의 기여", "주요 기여", "크게 향상",
    "크게 능가", "기존 방법을 능가", "기존 연구를 능가", "최첨단을 능가",
    "최고 성능", "혁신적", "획기적",
]
_METHOD_KW_KO_PROFESSOR = [
    "알고리즘", "의사 코드", "학습", "최적화", "손실 함수", "목적 함수", "그래디언트",
    "학습률", "배치 크기", "하이퍼파라미터", "파라미터 설정", "데이터셋", "어블레이션",
    "절제 연구", "베이스라인", "비교 실험", "평가 지표", "정확도", "재현율", "정밀도",
    "계산 복잡도", "시간 복잡도", "공간 복잡도", "수렴", "수렴성", "교차 검증", "실험 설정",
]

# ── Deutsche Schlüsselwörter（AI 初译，待德语母语校对；叠加进各档，完全不动上面的词库）──
# 匹配为大小写不敏感子串（analyze 里两侧都 .lower()），故名词首字母大写不影响命中。
_INNOVATION_KW_DE_TEACHER = [
    "wir schlagen vor", "vorgeschlagene", "vorgeschlagenen", "vorschlagen",
    "wir stellen vor", "wir präsentieren", "wir führen ein", "in dieser arbeit",
    "in dieser studie", "in diesem beitrag", "neuartig", "neuartige",
    "neuartigen", "erstmals", "zum ersten mal", "erstmalig", "originell",
    "einzigartig", "beitrag", "hauptbeitrag", "unser beitrag", "verbesserung",
    "verbessert", "deutlich verbessert", "erheblich verbessert", "übertrifft",
    "übertreffen", "überlegen", "überlegene", "stand der technik",
    "beste leistung", "höchste leistung", "erreicht", "realisiert",
    "entwickelt", "entworfen", "innovativ", "innovative", "durchbruch",
    "bahnbrechend", "besser als bestehende", "im vergleich zu bestehenden",
]
_METHOD_KW_DE_TEACHER = [
    "methode", "vorgeschlagene methode", "ansatz", "algorithmus", "modell",
    "netzwerk", "framework", "architektur", "struktur", "modul", "parameter",
    "hyperparameter", "training", "optimierung", "verlustfunktion",
    "zielfunktion", "datensatz", "versuchsaufbau", "vergleich", "ablation",
    "ablationsstudie", "baseline", "basislinie", "bewertungsmetrik",
    "evaluationsmetrik", "genauigkeit", "präzision", "trefferquote",
    "faltung", "pooling", "aufmerksamkeit", "attention", "self-attention",
    "encoder", "decoder", "kodierer", "dekodierer", "einbettung", "embedding",
    "regularisierung", "gradient", "backpropagation", "lernrate",
    "batch-größe", "konvergenz", "aktivierungsfunktion", "normalisierung",
    "vortraining", "feinabstimmung", "kreuzvalidierung", "ende-zu-ende",
    "merkmalsextraktion", "merkmal", "gewicht",
]
_INNOVATION_KW_DE_EXPERT = [
    "wir schlagen vor", "vorgeschlagene", "vorgeschlagenen", "in dieser arbeit",
    "in dieser studie", "neuartig", "neuartige", "erstmals", "zum ersten mal",
    "originell", "hauptbeitrag", "unser beitrag", "deutlich verbessert",
    "erheblich verbessert", "übertrifft", "übertreffen", "überlegen",
    "stand der technik", "beste leistung", "innovativ", "bahnbrechend",
    "besser als bestehende",
]
_METHOD_KW_DE_EXPERT = [
    "algorithmus", "vorgeschlagene methode", "framework", "architektur",
    "hyperparameter", "training", "optimierung", "verlustfunktion",
    "versuchsaufbau", "datensatz", "vergleich", "ablation", "ablationsstudie",
    "baseline", "bewertungsmetrik", "genauigkeit", "trefferquote", "präzision",
    "faltung", "attention", "encoder", "decoder", "regularisierung", "lernrate",
    "batch-größe", "konvergenz", "vortraining", "feinabstimmung",
    "kreuzvalidierung", "ende-zu-ende", "merkmalsextraktion",
]
_INNOVATION_KW_DE_PROFESSOR = [
    "vorgeschlagene", "vorgeschlagenen", "erstmals", "zum ersten mal",
    "hauptbeitrag", "unser beitrag", "deutlich übertrifft",
    "übertrifft bestehende", "übertrifft den stand der technik",
    "beste leistung", "höchste leistung", "bahnbrechend",
    "erheblicher fortschritt",
]
_METHOD_KW_DE_PROFESSOR = [
    "algorithmus", "pseudocode", "training", "optimierung", "verlustfunktion",
    "zielfunktion", "gradient", "lernrate", "batch-größe", "hyperparameter",
    "parametereinstellung", "datensatz", "ablation", "ablationsstudie",
    "baseline", "vergleichsexperiment", "bewertungsmetrik", "genauigkeit",
    "trefferquote", "präzision", "komplexität", "zeitkomplexität",
    "raumkomplexität", "konvergenz", "kreuzvalidierung", "versuchsaufbau",
]

# ════════════════════════════════════════════════════════════════════════════
# 跨学科方法词库（通用科研方法 / 医学 / 工程·物理·材料 / 社科·经管·人文）
# 原词库偏 CS/ML，非计算机论文方法维度命中少 → 补齐跨学科术语。5 语言全覆盖。
#   FULL = 老师 + 专家档用（这些词本身就具方法学实义，够格 peer 视角）
#   PRO  = 教授档用（只留「定量严谨 / 强设计」硬词：统计推断、RCT/队列/生存、有限元、
#          面板/工具变量/双重差分/结构方程等；剔除访谈、问卷、建模这类较软的）
#   非中英均 AI 初译，待母语校对（同 brand / 日韩德词库）。命中统计已去重，重叠无害。
# ════════════════════════════════════════════════════════════════════════════
_XDOM_METHOD_ZHEN_FULL = [
    # 通用科研方法（中）
    "假设检验", "显著性检验", "方差分析", "回归分析", "相关分析", "卡方检验",
    "t检验", "秩和检验", "效应量", "样本量", "抽样", "随机抽样", "分层抽样",
    "问卷", "问卷调查", "量表", "信度", "效度", "对照组", "实验组", "随机对照",
    "双盲", "元分析", "系统综述", "描述性统计", "中位数", "正态分布", "显著性水平",
    # 医学 / 生命科学（中）
    "临床试验", "随机对照试验", "队列研究", "病例对照", "横断面研究", "前瞻性",
    "回顾性研究", "敏感度", "特异度", "生存分析", "风险比", "比值比", "相对危险度",
    "生物标志物", "基因表达", "免疫组化", "细胞培养", "动物模型", "疗效",
    "不良反应", "纳入标准", "排除标准", "随访",
    # 工程 / 物理 / 材料（中）
    "有限元", "有限元分析", "数值模拟", "数值仿真", "表征", "扫描电镜", "透射电镜",
    "光谱分析", "衍射", "X射线衍射", "应力", "应变", "弹性模量", "热处理",
    "反馈控制", "传递函数", "信噪比", "频域", "时域", "边界条件", "网格划分",
    "材料性能",
    # 社科 / 经管 / 人文（中）
    "质性研究", "定性研究", "定量研究", "混合方法", "深度访谈", "半结构化访谈",
    "焦点小组", "案例研究", "扎根理论", "内容分析", "话语分析", "民族志",
    "参与观察", "计量经济", "面板数据", "时间序列", "工具变量", "固定效应",
    "随机效应", "双重差分", "断点回归", "中介效应", "调节效应", "结构方程",
    "主题分析",
    # 英文（跨学科）
    "hypothesis testing", "significance test", "analysis of variance", "anova",
    "regression analysis", "correlation analysis", "chi-square", "t-test",
    "effect size", "sample size", "random sampling", "stratified sampling",
    "questionnaire", "survey", "reliability", "validity", "control group",
    "randomized controlled", "double-blind", "meta-analysis",
    "systematic review", "descriptive statistics", "clinical trial",
    "cohort study", "case-control", "cross-sectional", "prospective",
    "retrospective", "sensitivity", "specificity", "survival analysis",
    "hazard ratio", "odds ratio", "relative risk", "biomarker",
    "gene expression", "immunohistochemistry", "cell culture", "animal model",
    "efficacy", "adverse effect", "inclusion criteria", "exclusion criteria",
    "follow-up", "finite element", "numerical simulation", "characterization",
    "scanning electron microscopy", "transmission electron microscopy",
    "spectroscopy", "x-ray diffraction", "stress", "strain", "elastic modulus",
    "heat treatment", "feedback control", "transfer function",
    "signal-to-noise", "frequency domain", "time domain", "boundary condition",
    "qualitative research", "quantitative research", "mixed methods",
    "in-depth interview", "focus group", "case study", "grounded theory",
    "content analysis", "discourse analysis", "ethnography", "econometric",
    "panel data", "time series", "instrumental variable", "fixed effects",
    "difference-in-differences", "regression discontinuity", "mediation",
    "moderation", "structural equation", "thematic analysis",
]
_XDOM_METHOD_ZHEN_PRO = [
    "假设检验", "方差分析", "回归分析", "卡方检验", "效应量", "样本量",
    "随机对照", "双盲", "元分析", "随机对照试验", "队列研究", "病例对照",
    "生存分析", "风险比", "比值比", "有限元分析", "数值模拟", "面板数据",
    "工具变量", "固定效应", "双重差分", "断点回归", "结构方程", "中介效应",
    "调节效应",
    "hypothesis testing", "analysis of variance", "anova", "regression analysis",
    "effect size", "sample size", "randomized controlled", "double-blind",
    "meta-analysis", "randomized controlled trial", "cohort study",
    "case-control", "survival analysis", "hazard ratio", "odds ratio",
    "finite element analysis", "numerical simulation", "panel data",
    "instrumental variable", "fixed effects", "difference-in-differences",
    "regression discontinuity", "structural equation", "mediation", "moderation",
]
_XDOM_INNOV_ZHEN = [
    "理论贡献", "实践意义", "实践价值", "研究空白", "填补研究空白", "首次系统",
    "具有重要意义", "拓展了", "丰富了", "新视角", "新范式",
    "theoretical contribution", "practical implication", "practical significance",
    "research gap", "fills a gap", "first systematic", "new perspective",
    "new paradigm", "sheds light on", "novel insight",
]

_XDOM_METHOD_JA_FULL = [
    # 通用
    "仮説検定", "有意差検定", "分散分析", "回帰分析", "相関分析", "カイ二乗検定",
    "t検定", "効果量", "サンプルサイズ", "標本サイズ", "無作為抽出", "層化抽出",
    "アンケート", "質問紙", "尺度", "信頼性", "妥当性", "対照群", "無作為化比較",
    "二重盲検", "メタ分析", "システマティックレビュー", "記述統計", "有意水準",
    # 医学
    "臨床試験", "ランダム化比較試験", "コホート研究", "症例対照研究", "横断研究",
    "前向き", "後ろ向き", "感度", "特異度", "生存分析", "ハザード比", "オッズ比",
    "相対リスク", "バイオマーカー", "遺伝子発現", "免疫染色", "細胞培養",
    "動物モデル", "有効性", "有害事象", "選択基準", "除外基準", "追跡調査",
    # 工程
    "有限要素", "有限要素法", "数値シミュレーション", "数値解析",
    "キャラクタリゼーション", "走査電子顕微鏡", "透過電子顕微鏡", "分光分析",
    "回折", "X線回折", "応力", "ひずみ", "弾性率", "熱処理", "フィードバック制御",
    "伝達関数", "信号対雑音比", "周波数領域", "時間領域", "境界条件",
    "メッシュ分割", "材料特性",
    # 社科
    "質的研究", "量的研究", "混合研究法", "インタビュー", "半構造化インタビュー",
    "フォーカスグループ", "事例研究", "グラウンデッドセオリー", "内容分析",
    "談話分析", "エスノグラフィー", "参与観察", "計量経済", "パネルデータ",
    "時系列", "操作変数", "固定効果", "ランダム効果", "差分の差分",
    "回帰不連続", "媒介効果", "調整効果", "構造方程式", "主題分析",
]
_XDOM_METHOD_JA_PRO = [
    "仮説検定", "分散分析", "回帰分析", "カイ二乗検定", "効果量", "サンプルサイズ",
    "無作為化比較", "二重盲検", "メタ分析", "ランダム化比較試験", "コホート研究",
    "症例対照研究", "生存分析", "ハザード比", "オッズ比", "有限要素法",
    "数値シミュレーション", "パネルデータ", "操作変数", "固定効果", "差分の差分",
    "回帰不連続", "構造方程式", "媒介効果", "調整効果",
]
_XDOM_INNOV_JA = [
    "理論的貢献", "実践的意義", "研究の空白", "空白を埋める", "初めて体系的に",
    "新たな視点", "新たなパラダイム", "重要な意義",
]

_XDOM_METHOD_KO_FULL = [
    # 通用
    "가설 검정", "유의성 검정", "분산 분석", "회귀 분석", "상관 분석",
    "카이제곱 검정", "t검정", "효과 크기", "표본 크기", "무작위 추출",
    "층화 추출", "설문", "설문지", "척도", "신뢰도", "타당도", "대조군",
    "무작위 대조", "이중 맹검", "메타 분석", "체계적 문헌 고찰", "기술 통계",
    "유의 수준",
    # 医学
    "임상 시험", "무작위 대조 시험", "코호트 연구", "환자 대조군 연구",
    "단면 연구", "전향적", "후향적", "민감도", "특이도", "생존 분석", "위험비",
    "오즈비", "상대 위험도", "바이오마커", "유전자 발현", "면역 조직 화학",
    "세포 배양", "동물 모델", "유효성", "이상 반응", "선정 기준", "제외 기준",
    "추적 관찰",
    # 工程
    "유한 요소", "유한 요소 해석", "수치 시뮬레이션", "수치 해석", "특성 분석",
    "주사 전자 현미경", "투과 전자 현미경", "분광 분석", "회절", "X선 회절",
    "응력", "변형", "탄성 계수", "열처리", "피드백 제어", "전달 함수",
    "신호 대 잡음비", "주파수 영역", "시간 영역", "경계 조건", "격자 분할",
    "재료 특성",
    # 社科
    "질적 연구", "양적 연구", "혼합 연구", "심층 면접", "반구조화 면접",
    "포커스 그룹", "사례 연구", "근거 이론", "내용 분석", "담화 분석",
    "문화기술지", "참여 관찰", "계량 경제", "패널 데이터", "시계열",
    "도구 변수", "고정 효과", "확률 효과", "이중차분", "회귀 불연속",
    "매개 효과", "조절 효과", "구조 방정식", "주제 분석",
]
_XDOM_METHOD_KO_PRO = [
    "가설 검정", "분산 분석", "회귀 분석", "카이제곱 검정", "효과 크기",
    "표본 크기", "무작위 대조", "이중 맹검", "메타 분석", "무작위 대조 시험",
    "코호트 연구", "환자 대조군 연구", "생존 분석", "위험비", "오즈비",
    "유한 요소 해석", "수치 시뮬레이션", "패널 데이터", "도구 변수",
    "고정 효과", "이중차분", "회귀 불연속", "구조 방정식", "매개 효과",
    "조절 효과",
]
_XDOM_INNOV_KO = [
    "이론적 기여", "실천적 의의", "실용적 가치", "연구 공백", "공백을 메우",
    "처음으로 체계적으로", "새로운 관점", "새로운 패러다임",
]

_XDOM_METHOD_DE_FULL = [
    # 通用
    "hypothesentest", "signifikanztest", "varianzanalyse", "anova",
    "regressionsanalyse", "korrelationsanalyse", "chi-quadrat-test", "t-test",
    "effektstärke", "stichprobengröße", "zufallsstichprobe",
    "geschichtete stichprobe", "fragebogen", "umfrage", "skala",
    "reliabilität", "validität", "kontrollgruppe", "randomisiert kontrolliert",
    "doppelblind", "metaanalyse", "systematische übersichtsarbeit",
    "deskriptive statistik", "signifikanzniveau",
    # 医学
    "klinische studie", "randomisierte kontrollierte studie", "kohortenstudie",
    "fall-kontroll-studie", "querschnittstudie", "prospektiv", "retrospektiv",
    "sensitivität", "spezifität", "überlebensanalyse", "hazard ratio",
    "odds ratio", "relatives risiko", "biomarker", "genexpression",
    "immunhistochemie", "zellkultur", "tiermodell", "wirksamkeit",
    "unerwünschte wirkung", "einschlusskriterien", "ausschlusskriterien",
    "nachbeobachtung",
    # 工程
    "finite elemente", "finite-elemente-methode", "numerische simulation",
    "numerische analyse", "charakterisierung", "rasterelektronenmikroskop",
    "transmissionselektronenmikroskop", "spektroskopie", "beugung",
    "röntgenbeugung", "spannung", "dehnung", "elastizitätsmodul",
    "wärmebehandlung", "rückkopplungsregelung", "übertragungsfunktion",
    "signal-rausch-verhältnis", "frequenzbereich", "zeitbereich",
    "randbedingung", "netzgenerierung", "materialeigenschaften",
    # 社科
    "qualitative forschung", "quantitative forschung", "mixed methods",
    "interview", "halbstrukturiertes interview", "fokusgruppe", "fallstudie",
    "grounded theory", "inhaltsanalyse", "diskursanalyse", "ethnographie",
    "teilnehmende beobachtung", "ökonometrie", "paneldaten", "zeitreihe",
    "instrumentvariable", "feste effekte", "zufallseffekte",
    "differenz-von-differenzen", "regressionsdiskontinuität", "mediation",
    "moderation", "strukturgleichungsmodell", "thematische analyse",
]
_XDOM_METHOD_DE_PRO = [
    "hypothesentest", "varianzanalyse", "regressionsanalyse", "chi-quadrat-test",
    "effektstärke", "stichprobengröße", "randomisiert kontrolliert",
    "doppelblind", "metaanalyse", "randomisierte kontrollierte studie",
    "kohortenstudie", "fall-kontroll-studie", "überlebensanalyse",
    "hazard ratio", "odds ratio", "finite-elemente-methode",
    "numerische simulation", "paneldaten", "instrumentvariable",
    "feste effekte", "differenz-von-differenzen", "regressionsdiskontinuität",
    "strukturgleichungsmodell", "mediation", "moderation",
]
_XDOM_INNOV_DE = [
    "theoretischer beitrag", "praktische implikation", "praktische bedeutung",
    "forschungslücke", "schließt eine lücke", "erstmals systematisch",
    "neue perspektive", "neues paradigma",
]

# 便捷聚合：跨学科词库按用途拼好，供 SCORE_PROFILES 直接引用
_XDOM_METHOD_FULL = (_XDOM_METHOD_ZHEN_FULL + _XDOM_METHOD_JA_FULL
                     + _XDOM_METHOD_KO_FULL + _XDOM_METHOD_DE_FULL)
_XDOM_METHOD_PRO = (_XDOM_METHOD_ZHEN_PRO + _XDOM_METHOD_JA_PRO
                    + _XDOM_METHOD_KO_PRO + _XDOM_METHOD_DE_PRO)
_XDOM_INNOV_ALL = (_XDOM_INNOV_ZHEN + _XDOM_INNOV_JA
                   + _XDOM_INNOV_KO + _XDOM_INNOV_DE)


# 三档评分尺度。cap = 总分上限（老师默认 85，前端滑块可在 70~100 调；专家 72；教授 65）。
# data_* 为「数据支撑」三个分量的上限，实际得分按量化值个数线性升到上限（见 analyze_paper_quality）。
SCORE_PROFILES = {
    "teacher": {
        "label": "老师", "cap": 85,
        "innovation_kw": _INNOVATION_KW_TEACHER + _INNOVATION_KW_JA_TEACHER + _INNOVATION_KW_KO_TEACHER + _INNOVATION_KW_DE_TEACHER + _XDOM_INNOV_ALL, "innovation_coef": 2.5,
        "method_kw": _METHOD_KW_TEACHER + _METHOD_KW_JA_TEACHER + _METHOD_KW_KO_TEACHER + _METHOD_KW_DE_TEACHER + _XDOM_METHOD_FULL, "method_coef": 1.5,
        "data_expr": 12, "data_conc": 8, "data_cross": 5,
        "num_strict": False,
    },
    "expert": {
        "label": "专家", "cap": 72,
        "innovation_kw": _INNOVATION_KW_EXPERT + _INNOVATION_KW_JA_EXPERT + _INNOVATION_KW_KO_EXPERT + _INNOVATION_KW_DE_EXPERT + _XDOM_INNOV_ALL, "innovation_coef": 2.2,
        "method_kw": _METHOD_KW_EXPERT + _METHOD_KW_JA_EXPERT + _METHOD_KW_KO_EXPERT + _METHOD_KW_DE_EXPERT + _XDOM_METHOD_FULL, "method_coef": 1.3,
        "data_expr": 10, "data_conc": 7, "data_cross": 5,
        "num_strict": False,
    },
    "professor": {
        "label": "教授", "cap": 65,
        # 压低系数 + 数据上限：让扎实论文从挤在 59~65 摊到 ~50~65，有短板的(创新弱/数据少)掉得下去、拉开区分度
        "innovation_kw": _INNOVATION_KW_PROFESSOR + _INNOVATION_KW_JA_PROFESSOR + _INNOVATION_KW_KO_PROFESSOR + _INNOVATION_KW_DE_PROFESSOR, "innovation_coef": 1.7,
        "method_kw": _METHOD_KW_PROFESSOR + _METHOD_KW_JA_PROFESSOR + _METHOD_KW_KO_PROFESSOR + _METHOD_KW_DE_PROFESSOR + _XDOM_METHOD_PRO, "method_coef": 1.0,
        "data_expr": 7, "data_conc": 6, "data_cross": 4,
        "num_strict": True,  # 数据只认带单位/百分比/小数，裸整数不算量化指标
    },
}

# 各档「饱和率」：命中深度→分数走凹曲线 1-(1-rate)^n，杜绝堆词顶格（越严的档 rate 越低，要更多命中才给分）
# _SAT_RATE 用于创新维度（命中数少，rate 高，几处即接近满）
_SAT_RATE = {"teacher": 0.55, "expert": 0.46, "professor": 0.38}
# _SAT_RATE_DEPTH 用于方法「深度分」：扁平词库词多，rate 刻意低 → 需十几个词才接近满，
# 让「方法表达丰富度」保留真实区分度（弱论文低、扎实的才高），避免上千词把所有论文都抬满。
_SAT_RATE_DEPTH = {"teacher": 0.20, "expert": 0.17, "professor": 0.14}


# ── 通用层（Universal）：跨学科共有的方法学词，抽成单一来源供各学科类别引用（去重复维护）──
# 三层词库思路（Universal → 学科增量）的第一块：先抽「统计推断」这一最清晰的通用簇。
# 各学科的统计类别 = _UNIVERSAL_STATS + 本学科增量。中英全 + 日/韩/德关键词。
_UNIVERSAL_STATS = [
    "方差分析", "回归分析", "卡方检验", "t检验", "显著性", "效应量",
    "样本量", "置信区间", "p值", "统计",
    "analysis of variance", "anova", "regression", "chi-square", "t-test",
    "significance", "effect size", "sample size", "confidence interval",
    "p-value", "statistical",
    "分散分析", "回帰分析", "有意", "サンプルサイズ",
    "분산 분석", "회귀 분석", "유의", "표본 크기",
    "varianzanalyse", "regressionsanalyse", "signifikanz", "stichprobengröße",
]


# ════════════════════════════════════════════════════════════════════════════
# 学科评分档（subject）：用户手选论文所属学科 → 用该学科的「方法学功能类别」评方法维度。
# 动机：词库扩到上千后，"命中词数×系数"会天花板 + 奖励堆词（写得烂也判高，误导学生）。
# 改为「类别覆盖为主(0.6) + 命中深度·饱和为辅(0.4)」：必须跨多个方法学环节才能高分，
# 堆某一类词顶不满 → 直接治「堆词判高」。category = 该学科期望具备的方法学环节，命中≥1即算覆盖。
# 各类别：中英全 + 日/韩/德关键词（AI 初译·待母语校对）。命中判定为大小写不敏感子串。
# 统计类别统一引用 _UNIVERSAL_STATS（通用层）+ 本学科增量，见 general/medical/social。
# ════════════════════════════════════════════════════════════════════════════
SUBJECT_RUBRICS = {
    "cs": {
        "label": "计算机 / 人工智能",
        "categories": {
            "模型与架构": [
                "模型", "网络", "架构", "模块", "神经网络", "卷积", "注意力",
                "自注意力", "多头注意力", "编码器", "解码器", "嵌入",
                "model", "network", "architecture", "module", "neural network",
                "convolution", "attention", "self-attention", "transformer",
                "encoder", "decoder", "embedding",
                "モデル", "ネットワーク", "アーキテクチャ", "注意機構", "エンコーダ",
                "모델", "네트워크", "아키텍처", "어텐션", "인코더",
                "modell", "netzwerk", "architektur", "aufmerksamkeit", "encoder",
            ],
            "训练与优化": [
                "训练", "优化", "损失函数", "目标函数", "梯度", "学习率", "批大小",
                "正则化", "反向传播", "微调", "预训练",
                "training", "optimizer", "loss function", "gradient",
                "learning rate", "batch size", "regularization",
                "backpropagation", "fine-tune", "pretrain",
                "学習", "最適化", "損失関数", "学習率", "事前学習",
                "학습", "최적화", "손실 함수", "학습률", "사전 학습",
                "optimierung", "verlustfunktion", "lernrate", "vortraining",
            ],
            "数据与实验设计": [
                "数据集", "实验设置", "消融", "消融实验", "基线", "对比实验",
                "交叉验证", "数据增强",
                "dataset", "experimental setup", "ablation", "baseline",
                "comparison", "cross-validation", "data augmentation",
                "データセット", "アブレーション", "ベースライン", "交差検証",
                "데이터셋", "어블레이션", "베이스라인", "교차 검증",
                "datensatz", "ablation", "baseline", "kreuzvalidierung",
            ],
            "评估与指标": [
                "准确率", "召回率", "精确率", "评价指标", "混淆矩阵", "泛化",
                "鲁棒性", "f1值", "auc",
                "accuracy", "precision", "recall", "f1 score", "evaluation metric",
                "confusion matrix", "generalization", "robustness",
                "正解率", "適合率", "再現率", "評価指標", "f1スコア",
                "정확도", "정밀도", "재현율", "평가 지표",
                "genauigkeit", "präzision", "trefferquote", "bewertungsmetrik",
            ],
        },
    },
    "medical": {
        "label": "医学 / 生命科学",
        "categories": {
            "研究设计": [
                "临床试验", "随机对照试验", "队列研究", "病例对照", "横断面研究",
                "前瞻性", "回顾性", "对照组", "双盲", "随机分组",
                "clinical trial", "randomized controlled trial", "cohort study",
                "case-control", "cross-sectional", "prospective", "retrospective",
                "control group", "double-blind", "randomization",
                "臨床試験", "ランダム化比較試験", "コホート研究", "二重盲検",
                "임상 시험", "무작위 대조 시험", "코호트 연구", "이중 맹검",
                "klinische studie", "randomisierte kontrollierte studie",
                "kohortenstudie", "doppelblind",
            ],
            "统计推断": _UNIVERSAL_STATS + [
                # 医学统计增量（生存分析/风险比/比值比等）
                "生存分析", "风险比", "比值比",
                "survival analysis", "hazard ratio", "odds ratio",
                "生存分析", "ハザード比",
                "생존 분석", "위험비",
                "überlebensanalyse",
            ],
            "指标与结局": [
                "敏感度", "特异度", "有效率", "疗效", "生物标志物", "主要终点",
                "不良反应",
                "sensitivity", "specificity", "efficacy", "biomarker",
                "primary endpoint", "adverse effect",
                "感度", "特異度", "有効性", "バイオマーカー", "有害事象",
                "민감도", "특이도", "유효성", "바이오마커",
                "sensitivität", "spezifität", "wirksamkeit", "biomarker",
            ],
            "样本与流程": [
                "纳入标准", "排除标准", "随访", "受试者", "病例", "患者", "队列",
                "inclusion criteria", "exclusion criteria", "follow-up",
                "subjects", "patients",
                "選択基準", "除外基準", "追跡調査", "被験者",
                "선정 기준", "제외 기준", "추적 관찰", "피험자",
                "einschlusskriterien", "ausschlusskriterien", "nachbeobachtung",
                "probanden",
            ],
        },
    },
    "engineering": {
        "label": "工程 / 物理 / 材料",
        "categories": {
            "建模与仿真": [
                "有限元", "有限元分析", "数值模拟", "数值仿真", "建模", "边界条件",
                "网格划分", "求解",
                "finite element", "numerical simulation", "modeling",
                "boundary condition", "mesh", "solver",
                "有限要素法", "数値シミュレーション", "境界条件", "メッシュ分割",
                "유한 요소 해석", "수치 시뮬레이션", "경계 조건", "격자 분할",
                "finite-elemente-methode", "numerische simulation",
                "randbedingung", "netzgenerierung",
            ],
            "表征与测量": [
                "扫描电镜", "透射电镜", "光谱", "光谱分析", "衍射", "x射线衍射",
                "表征", "显微镜",
                "scanning electron microscopy", "transmission electron microscopy",
                "spectroscopy", "diffraction", "x-ray diffraction",
                "characterization", "microscopy",
                "走査電子顕微鏡", "透過電子顕微鏡", "分光分析", "x線回折",
                "주사 전자 현미경", "분광 분석", "x선 회절", "특성 분석",
                "rasterelektronenmikroskop", "spektroskopie", "röntgenbeugung",
                "charakterisierung",
            ],
            "物理量与性能": [
                "应力", "应变", "弹性模量", "强度", "信噪比", "频率", "效率",
                "材料性能",
                "stress", "strain", "elastic modulus", "strength",
                "signal-to-noise", "frequency", "efficiency", "material properties",
                "応力", "ひずみ", "弾性率", "信号対雑音比", "材料特性",
                "응력", "변형", "탄성 계수", "신호 대 잡음비", "재료 특성",
                "spannung", "dehnung", "elastizitätsmodul",
                "signal-rausch-verhältnis", "materialeigenschaften",
            ],
            "控制与系统": [
                "控制系统", "反馈控制", "传递函数", "频域", "时域", "系统模型",
                "稳定性",
                "control system", "feedback control", "transfer function",
                "frequency domain", "time domain", "system model", "stability",
                "フィードバック制御", "伝達関数", "周波数領域", "時間領域",
                "피드백 제어", "전달 함수", "주파수 영역", "시간 영역",
                "rückkopplungsregelung", "übertragungsfunktion", "frequenzbereich",
                "zeitbereich",
            ],
        },
    },
    # 材料/化学：从 engineering 拆出（实验型材料论文的方法结构≠机械/控制，样本 202303.10524 验证）。
    # 中英足量；日韩德暂不造（母语校对成本，用户手上多为中文材料论文）。
    "materials": {
        "label": "材料 / 化学",
        "categories": {
            "制备与合成工艺": [
                "制备", "合成", "溶胶凝胶", "溶胶-凝胶", "沉积", "电沉积", "化学沉积",
                "磁控溅射", "溅射", "镀膜", "涂层", "转化膜", "薄膜", "热处理", "退火",
                "淬火", "烧结", "熔炼", "铸造", "水热", "前驱体", "配比", "反应条件",
                "工艺参数", "成膜",
                "preparation", "synthesis", "sol-gel", "deposition",
                "electrodeposition", "sputtering", "magnetron sputtering",
                "coating", "conversion coating", "thin film", "heat treatment",
                "annealing", "quenching", "sintering", "casting", "hydrothermal",
                "precursor", "process parameter",
            ],
            "表征与测量": [
                "扫描电镜", "扫描电子显微镜", "sem", "透射电镜", "tem", "能谱",
                "能谱分析", "eds", "edx", "x射线衍射", "xrd", "光电子能谱", "xps",
                "拉曼", "拉曼光谱", "红外光谱", "傅里叶", "ftir", "原子力显微镜",
                "afm", "接触角", "极化曲线", "动电位极化", "电化学阻抗", "阻抗谱",
                "eis", "塔菲尔", "tafel", "循环伏安", "表征", "显微镜", "光谱",
                "衍射", "元素分析",
                "scanning electron microscopy", "transmission electron microscopy",
                "energy dispersive spectroscopy", "x-ray diffraction",
                "x-ray photoelectron spectroscopy", "raman", "raman spectroscopy",
                "infrared spectroscopy", "atomic force microscopy", "contact angle",
                "polarization curve", "potentiodynamic", "electrochemical impedance",
                "impedance spectroscopy", "cyclic voltammetry", "characterization",
                "microscopy", "spectroscopy", "diffraction",
            ],
            "性能测试": [
                "耐蚀性", "耐腐蚀性", "腐蚀速率", "腐蚀电流", "腐蚀电位",
                "自腐蚀电位", "极化电阻", "盐雾试验", "中性盐雾", "膜厚", "附着力",
                "结合力", "硬度", "显微硬度", "耐磨", "磨损", "摩擦系数",
                "拉伸强度", "屈服强度", "断裂", "韧性", "弹性模量", "疲劳",
                "热稳定性", "抗氧化", "导电率", "电导率",
                "corrosion resistance", "corrosion rate", "corrosion current",
                "corrosion potential", "polarization resistance", "salt spray",
                "film thickness", "adhesion", "hardness", "microhardness", "wear",
                "friction coefficient", "tensile strength", "yield strength",
                "fracture", "toughness", "elastic modulus", "fatigue",
                "thermal stability", "oxidation resistance", "conductivity",
            ],
            "微观结构与机理": [
                "微观结构", "微观形貌", "表面形貌", "晶粒", "晶粒尺寸", "晶界",
                "相组成", "物相", "晶体结构", "界面", "成膜机理", "腐蚀机理",
                "反应机理", "生长机理", "择优取向", "缺陷", "孔隙", "致密",
                "均匀性", "元素分布", "化学组成", "价态", "机理",
                "microstructure", "morphology", "surface morphology", "grain",
                "grain size", "grain boundary", "phase composition",
                "crystal structure", "interface", "formation mechanism",
                "corrosion mechanism", "reaction mechanism", "growth mechanism",
                "defect", "porosity", "dense", "uniformity",
                "elemental distribution", "chemical composition", "mechanism",
            ],
        },
    },
    # 数学/纯理论：定理-证明结构，无数据集/实验/表征。批量诊断中 6 篇数学论文在实证型档只 1~2/4。
    "theory": {
        "label": "数学 / 理论",
        "categories": {
            # 刻意只留「理论专属」硬信号，剔除 模型/算法/优化/分析/稳定性/误差 等谁都写得出的泛词，
            # 否则 theory 会在任何技术论文上覆盖 4/4（批量诊断实测过度命中）。
            "定义与假设": [
                "定义", "记号", "假设", "前提", "命题", "猜想", "公理", "设为",
                "definition", "notation", "assumption", "proposition",
                "conjecture", "axiom", "let us define",
            ],
            "定理与证明": [
                "定理", "引理", "推论", "证明", "反证法", "归纳法", "构造性证明",
                "证毕", "当且仅当", "充要条件",
                "theorem", "lemma", "corollary", "proof", "by contradiction",
                "by induction", "if and only if", "q.e.d", "qed",
            ],
            "界与复杂度": [
                "上界", "下界", "紧致", "复杂度", "时间复杂度", "空间复杂度",
                "多项式时间", "np-hard", "np完全", "归约", "最优性",
                "upper bound", "lower bound", "complexity", "polynomial time",
                "np-hard", "np-complete", "reduction", "tight bound",
            ],
            "收敛与存在性": [
                "收敛性", "存在性", "唯一性", "有界性", "渐近", "范数", "测度",
                "不等式", "连续性", "紧性", "解的存在",
                "convergence", "existence", "uniqueness", "boundedness",
                "asymptotic", "norm", "measure", "inequality", "well-posed",
            ],
        },
    },
    # 电子/电气 EE：批量诊断中 OFDM/波束成形/功率传输/MIMO 等靠 cs/eng 蒙到 3/4，缺专属档。
    "electronics": {
        "label": "电子 / 电气",
        "categories": {
            "电路与器件": [
                "电路", "放大器", "滤波器", "晶体管", "集成电路", "芯片", "器件",
                "阻抗", "电容", "电感", "半导体", "电压", "电流",
                "circuit", "amplifier", "filter", "transistor",
                "integrated circuit", "chip", "impedance", "capacitor",
                "inductor", "voltage",
            ],
            "信号处理": [
                "信号处理", "采样", "频谱", "傅里叶", "滤波", "调制", "解调",
                "频域", "时域", "信噪比", "卷积",
                "signal processing", "sampling", "spectrum", "fourier",
                "modulation", "demodulation", "frequency domain", "snr",
                "convolution",
            ],
            "通信系统": [
                "通信", "信道", "信道估计", "编码", "波束成形", "天线", "mimo",
                "ofdm", "误码率", "吞吐", "频率复用", "多址", "衰落",
                "communication", "channel", "channel estimation", "coding",
                "beamforming", "antenna", "bit error rate", "throughput",
                "fading",
            ],
            "电力与控制": [
                "功率", "电源", "变换器", "逆变", "整流", "控制", "反馈",
                "稳压", "效率", "传递函数", "谐振",
                "power", "converter", "inverter", "rectifier", "control",
                "feedback", "transfer function", "efficiency", "regulation",
                "resonant",
            ],
        },
    },
    # 土木/建筑：批量诊断中 抗震结构/混凝土损伤 靠 general 兜底、engineering 只 3/4。
    "civil": {
        "label": "土木 / 建筑",
        "categories": {
            "结构分析": [
                "结构", "荷载", "应力", "应变", "承载力", "变形", "挠度",
                "有限元", "内力", "弯矩", "剪力",
                "structural", "load", "stress", "strain", "bearing capacity",
                "deformation", "deflection", "finite element", "bending moment",
                "shear",
            ],
            "材料与构件": [
                "混凝土", "钢筋", "钢结构", "梁", "柱", "板", "节点", "配筋",
                "强度等级", "预应力", "砌体",
                "concrete", "reinforcement", "steel", "beam", "column", "slab",
                "joint", "prestressed", "masonry",
            ],
            "抗震与动力": [
                "抗震", "地震", "地震动", "反应谱", "时程分析", "振动", "阻尼",
                "位移", "延性", "隔震",
                "seismic", "earthquake", "ground motion", "response spectrum",
                "time history", "vibration", "damping", "displacement",
                "ductility",
            ],
            "岩土与施工": [
                "岩土", "地基", "基础", "土体", "边坡", "隧道", "桥梁", "施工",
                "沉降", "渗流", "支护",
                "geotechnical", "foundation", "soil", "slope", "tunnel",
                "bridge", "construction", "settlement", "seepage",
            ],
        },
    },
    # 生物（非临床）：批量诊断中 系统生物物理/基因表达 medical 只 1/4，缺分子/细胞档。
    "biology": {
        "label": "生物 / 生命科学",
        "categories": {
            "实验与技术": [
                "pcr", "测序", "电泳", "免疫印迹", "western", "转染", "敲除",
                "敲低", "克隆", "培养", "染色", "流式",
                "sequencing", "electrophoresis", "western blot", "transfection",
                "knockout", "knockdown", "cloning", "culture", "staining",
                "flow cytometry", "crispr",
            ],
            "分子与细胞": [
                "基因", "表达", "蛋白", "酶", "通路", "信号通路", "细胞",
                "受体", "转录", "翻译", "突变", "结合",
                "gene", "expression", "protein", "enzyme", "pathway", "cell",
                "receptor", "transcription", "translation", "mutation",
                "binding",
            ],
            # 剔除 样本/对照/模型/机制/显著 等通用词（否则任何实证论文都蒙 3/4），只留生物专属
            "表型与生信分析": [
                "表型", "生物信息", "差异表达", "富集分析", "基因型",
                "转录组", "测序数据", "通路富集",
                "phenotype", "bioinformatics", "differential expression",
                "enrichment", "genotype", "transcriptome", "rna-seq",
            ],
            "模型与机制": [
                "动物模型", "小鼠模型", "体外", "体内", "表观遗传", "代谢",
                "信号转导", "分子机制", "调控网络", "进化",
                "animal model", "in vitro", "in vivo", "epigenetic",
                "metabolism", "signal transduction", "regulatory network",
                "evolution",
            ],
        },
    },
    "social": {
        "label": "社科 / 经管 / 人文",
        "categories": {
            "研究设计": [
                "质性研究", "定性研究", "定量研究", "混合方法", "案例研究",
                "实验设计", "准实验",
                "qualitative research", "quantitative research", "mixed methods",
                "case study", "experimental design", "quasi-experiment",
                "質的研究", "量的研究", "混合研究法", "事例研究",
                "질적 연구", "양적 연구", "혼합 연구", "사례 연구",
                "qualitative forschung", "quantitative forschung", "mixed methods",
                "fallstudie",
            ],
            "数据收集": [
                "访谈", "深度访谈", "半结构化访谈", "问卷", "问卷调查", "焦点小组",
                "参与观察", "量表",
                "interview", "in-depth interview", "semi-structured interview",
                "questionnaire", "survey", "focus group", "participant observation",
                "scale",
                "インタビュー", "質問紙", "フォーカスグループ", "参与観察",
                "면접", "설문지", "포커스 그룹", "참여 관찰",
                "fragebogen", "fokusgruppe", "teilnehmende beobachtung",
            ],
            "分析方法": [
                "内容分析", "话语分析", "主题分析", "扎根理论", "民族志", "编码",
                # 管理/运营增量（供应链/运营/决策/仿真）
                "运营管理", "供应链", "库存管理", "决策分析", "仿真建模",
                "案例分析", "流程优化",
                "content analysis", "discourse analysis", "thematic analysis",
                "grounded theory", "ethnography", "coding",
                "operations management", "supply chain", "inventory",
                "decision analysis", "simulation", "process optimization",
                "内容分析", "談話分析", "グラウンデッドセオリー", "主題分析",
                "내용 분석", "담화 분석", "근거 이론", "주제 분석",
                "inhaltsanalyse", "diskursanalyse", "grounded theory",
                "thematische analyse",
            ],
            "计量与统计": _UNIVERSAL_STATS + [
                # 社科/经管计量增量（计量经济/面板/工具变量/双重差分/结构方程等）
                "计量经济", "面板数据", "时间序列", "工具变量", "固定效应",
                "双重差分", "断点回归", "中介效应", "调节效应", "结构方程",
                # 运筹/管理科学增量（优化/规划/博弈/排队）
                "运筹", "优化", "线性规划", "整数规划", "博弈论", "排队论",
                "启发式", "鲁棒优化",
                "econometric", "panel data", "time series", "instrumental variable",
                "fixed effects", "difference-in-differences",
                "regression discontinuity", "mediation", "moderation",
                "structural equation",
                "operations research", "optimization", "linear programming",
                "game theory", "queueing", "heuristic",
                "計量経済", "パネルデータ", "操作変数", "固定効果", "構造方程式",
                "계량 경제", "패널 데이터", "도구 변수", "고정 효과", "구조 방정식",
                "ökonometrie", "paneldaten", "instrumentvariable", "feste effekte",
                "strukturgleichungsmodell",
            ],
        },
    },
    "general": {
        "label": "通用 / 不确定",
        "categories": {
            "研究设计与流程": [
                "实验设计", "对照组", "随机", "案例研究", "研究方法", "系统设计",
                "技术方案", "临床试验", "仿真", "建模",
                "experimental design", "control group", "randomized", "case study",
                "research method", "system design", "simulation", "modeling",
                "clinical trial",
                "実験設定", "対照群", "研究方法", "提案手法",
                "실험 설정", "대조군", "연구 방법", "제안 방법",
                "versuchsaufbau", "kontrollgruppe", "methode", "ansatz",
            ],
            "定量与统计": list(_UNIVERSAL_STATS),
            "数据与样本": [
                "数据集", "样本", "数据", "问卷", "面板数据", "时间序列",
                "纳入标准", "随访",
                "dataset", "sample", "data", "questionnaire", "panel data",
                "time series", "inclusion criteria", "follow-up",
                "データセット", "標本", "データ", "質問紙",
                "데이터셋", "표본", "데이터", "설문지",
                "datensatz", "stichprobe", "daten", "fragebogen",
            ],
            "评估与分析": [
                "评价指标", "准确率", "敏感度", "有效率", "内容分析", "主题分析",
                "消融", "基线", "对比",
                "evaluation metric", "accuracy", "sensitivity", "efficacy",
                "content analysis", "thematic analysis", "ablation", "baseline",
                "comparison",
                "評価指標", "正解率", "内容分析", "ベースライン",
                "평가 지표", "정확도", "내용 분석", "베이스라인",
                "bewertungsmetrik", "genauigkeit", "inhaltsanalyse", "baseline",
            ],
        },
    },
}


def _saturate(n, rate=0.5):
    """命中深度 n → [0,1) 凹曲线：1-(1-rate)^n。前几个命中给分快，之后衰减，杜绝堆词顶格。"""
    if n <= 0:
        return 0.0
    return 1.0 - (1.0 - rate) ** n


def _score_confidence(text_len, subject, covered, ncats, depth_hits, innov_count):
    """规则分「置信度」：文本太短 / 疑似堆词（命中集中单一环节）/ 命中过少 / 覆盖不足 → 降。
    返回 {score:0~1, level:'高|中|低', reasons:[...]}。用于提示学生「何时别太信这个数」。"""
    score = 1.0
    reasons = []
    if text_len < 800:
        score -= 0.35
        reasons.append("正文偏短，可判据不足")
    if depth_hits >= 8 and covered <= 1:
        score -= 0.30
        reasons.append("命中集中在单一方法环节，疑似术语堆砌")
    if innov_count == 0 and depth_hits <= 1:
        score -= 0.30
        reasons.append("方法/创新关键词命中很少，规则分参考性低")
    if subject != "general" and ncats and covered <= 1:
        score -= 0.15
        reasons.append("方法学环节覆盖不足")
    score = max(0.0, min(1.0, score))
    level = "高" if score >= 0.75 else ("中" if score >= 0.45 else "低")
    return {"score": round(score, 2), "level": level, "reasons": reasons}


def analyze_paper_quality(text, sections, mode='teacher', teacher_cap=85, subject='general'):
    """
    本地自研算法：四维论文质量评分。
    不依赖任何外部 API，全程本地计算。

    返回格式：
    {
      "total": 0-100,
      "dimensions": {
        "structure":   {"score": 0-25, "max": 25, "label": "...", "detail": "...", "suggestions": [...]},
        "innovation":  {...},
        "data_support":{...},
        "method":      {...},
      },
      "suggestions": ["全局建议1", ...]
    }
    """
    import re

    prof = SCORE_PROFILES.get(mode, SCORE_PROFILES["teacher"])
    result = {"total": 0, "dimensions": {}, "suggestions": []}
    _no_sections = not any(sections.values())

    # ── 维度1：结构完整度（满分 25）──────────────────────────────────────
    struct_fields = {
        "abstract":   ("摘要",   8),
        "keywords":   ("关键词", 4),
        "method":     ("研究方法", 6),
        "experiment": ("实验结果", 4),
        "conclusion": ("结论",   3),
    }
    struct_score = 0
    struct_missing = []
    for field, (label, pts) in struct_fields.items():
        if sections.get(field):
            struct_score += pts
        else:
            struct_missing.append(label)
    struct_suggestions = []
    if struct_missing:
        struct_suggestions.append(f"未检测到以下章节：{'、'.join(struct_missing)}，建议补充或规范章节标题（如'2 研究方法'、'4 实验结果'）")
    if not sections.get("keywords"):
        struct_suggestions.append("摘要后缺少关键词行，建议添加 3~6 个关键词")
    struct_detail = ("章节结构未识别，基于全文评分" if _no_sections
                     else f"检测到 {5 - len(struct_missing)}/5 个标准章节")

    result["dimensions"]["structure"] = {
        "score": struct_score, "max": 25,
        "label": "结构完整度",
        "detail": struct_detail,
        "suggestions": struct_suggestions,
    }

    # ── 维度2：创新声明密度（满分 25）──────────────────────────────────
    # 去重：多语言/跨学科词库叠加可能出现重复词，去重避免同一词被重复计数
    innovation_kw = list(dict.fromkeys(prof["innovation_kw"]))
    innov_src = (sections.get("abstract", "") + " " + text)
    innov_text = innov_src.lower()
    innov_hits = [kw for kw in innovation_kw if kw.lower() in innov_text]
    sem_innov = semantic.extra_hits(innov_src, innovation_kw, innov_hits)  # C6 语义命中（模型不可用则为 0）
    innov_count = len(innov_hits) + sem_innov
    # 饱和曲线替代「命中数×系数」：前几处创新声明给分快，之后衰减，防堆词顶格（严苛度由各档 rate + 词库共同决定）
    _sat_rate = _SAT_RATE.get(mode, 0.5)
    innov_score = round(25 * _saturate(innov_count, _sat_rate))
    innov_suggestions = []
    if innov_count == 0:
        innov_suggestions.append("摘要和正文中未检测到明确的创新声明，建议在摘要中加入\"本文提出/设计/构建了...\"类句式")
    elif innov_count < 2:
        innov_suggestions.append("创新声明表述较少，建议在摘要及引言中至少出现 2 处明确的创新贡献描述")
    innov_detail = f"检测到 {len(innov_hits)} 处创新声明关键词" + (f"（+{sem_innov} 处语义相近）" if sem_innov else "")

    result["dimensions"]["innovation"] = {
        "score": innov_score, "max": 25,
        "label": "创新声明密度",
        "detail": innov_detail,
        "suggestions": innov_suggestions,
    }

    # ── 维度3：数据支撑度（满分 25）──────────────────────────────────────
    # 判断结论段落是否含有数值/百分比与实验段落的数值是否有交叉
    # 放宽数字判定：带单位的实测值（15 ms / 3 dB / 1.2 Gbps）、小数、2 位以上整数都算量化数据
    _num_units = r'\d+\.?\d*\s*(?:%|ms|μs|ns|dB|dBm|bps|Mbps|Gbps|Hz|kHz|MHz|GHz|GB|MB|KB|fps|W|mW|x|×|倍|个百分点)|\d+\.\d+'
    num_pattern = re.compile(_num_units if prof.get("num_strict") else _num_units + r'|\b\d{2,}\b')
    conclusion_text = sections.get("conclusion", "") or text
    experiment_text = text  # 量化数据常分散全文（含图表说明/各小节），不限"实验"那一段

    conc_nums = set(num_pattern.findall(conclusion_text))
    expr_nums = set(num_pattern.findall(experiment_text))
    cross_nums = conc_nums & expr_nums

    # 按「量化值个数」分级给分（不再“有数字就给满”，治虚高）：
    # 不同数值达到 full_at 个即给满该分量上限，线性升、超出封顶。
    def _graded(count, full_at, cap):
        return min(cap, cap * count / full_at) if count > 0 else 0
    data_score = (_graded(len(expr_nums), 6, prof["data_expr"])      # 正文/实验的量化数据
                  + _graded(len(conc_nums), 3, prof["data_conc"])    # 结论引用具体数据
                  + _graded(len(cross_nums), 2, prof["data_cross"]))  # 结论数据与实验交叉
    data_score = round(data_score)
    data_suggestions = []

    if not conc_nums:
        data_suggestions.append("结论段落未检测到具体数值，建议用实验数据（如准确率、性能提升百分比）支撑结论")
    if not expr_nums:
        data_suggestions.append("实验章节未检测到量化数据，建议补充对比实验的具体指标数值")
    if conc_nums and expr_nums and not cross_nums:
        data_suggestions.append("结论中的数据与实验章节数据无交叉，建议确认结论是否直接引用了实验结果")

    data_detail = f"正文检出 {len(expr_nums)} 处量化数据；结论含 {len(conc_nums)} 处（与实验交叉 {len(cross_nums)} 处）"

    result["dimensions"]["data_support"] = {
        "score": min(25, data_score), "max": 25,
        "label": "数据支撑度",
        "detail": data_detail,
        "suggestions": data_suggestions,
    }

    # ── 维度4：方法描述完整性（满分 25）· 两层 ───────────────────────────
    #   覆盖分(0.6)＝学科 4 个方法学环节命中几个 → 研究流程「结构完整性」（防堆一类词）
    #   深度分(0.4)＝扁平方法词库(中英日韩德+跨学科)去重命中 → 「方法表达丰富度」，走慢饱和防堆词刷分
    #   两层结合：环节负责结构、扁平词负责丰富度，既防堆词又不浪费词库。
    rubric = SUBJECT_RUBRICS.get(subject, SUBJECT_RUBRICS["general"])
    cats = rubric["categories"]
    method_text = text
    m_low = method_text.lower()

    # 覆盖分：学科方法学环节（用户手选 subject 决定用哪套）
    covered = 0
    cat_detail = []
    for cname, kws in cats.items():
        if any(k.lower() in m_low for k in kws):
            covered += 1
            cat_detail.append(cname)
    ncats = max(1, len(cats))
    coverage = covered / ncats

    # 深度分：池子 = 扁平方法词库（各语言+跨学科·按档）+ 当前学科自己的类别词，去重后慢饱和。
    # 加本学科类别词是为了让 materials 等"扁平库覆盖不到"的学科，深度层也能起来（不然只修覆盖分没用）。
    _subj_words = [k for kws in cats.values() for k in kws]
    method_kw = list(dict.fromkeys(prof["method_kw"] + _subj_words))
    m_hits = [kw for kw in method_kw if kw.lower() in m_low]
    sem_method = semantic.extra_hits(method_text, method_kw, m_hits)  # C6 语义命中（模型不可用则为 0）
    depth_hits = len(m_hits) + sem_method
    depth = _saturate(depth_hits, _SAT_RATE_DEPTH.get(mode, 0.2))

    method_ratio = 0.6 * coverage + 0.4 * depth
    method_score = round(25 * method_ratio)
    method_suggestions = []
    if covered < ncats:
        _missing = [c for c in cats if c not in cat_detail]
        method_suggestions.append(
            f"「{rubric['label']}」方法学环节未覆盖全：缺 {('、'.join(_missing)) or '无'}；"
            f"建议补充这些环节的描述（覆盖 {covered}/{ncats}）")
    if depth_hits < 3:
        method_suggestions.append("方法描述较简略，建议补充：研究设计/关键步骤/评估方式等具体内容")
    method_detail = (f"覆盖 {covered}/{ncats} 个方法学环节"
                     + (f"（{'、'.join(cat_detail)}）" if cat_detail else "")
                     + f"；方法词命中 {len(m_hits)}" + (f"（+{sem_method} 处语义相近）" if sem_method else ""))

    result["dimensions"]["method"] = {
        "score": method_score, "max": 25,
        "label": "方法描述完整性",
        "detail": method_detail,
        "suggestions": method_suggestions,
    }

    # ── 汇总 ─────────────────────────────────────────────────────────────
    total = (result["dimensions"]["structure"]["score"]
             + result["dimensions"]["innovation"]["score"]
             + result["dimensions"]["data_support"]["score"]
             + result["dimensions"]["method"]["score"])
    # 总分按各档上限裁剪：老师用 teacher_cap（前端滑块 70~100），专家/教授用 profile 固定值。
    # 上限只“封顶”不“托底”——平庸论文靠各维度自然落到中段，扎实的才会顶到上限。
    if mode == "teacher":
        cap = max(70, min(100, teacher_cap))
    else:
        cap = prof.get("cap", 100)
    result["total"] = min(total, cap)
    result["cap"] = cap
    result["subject"] = {"key": subject, "label": rubric["label"]}

    # 置信度：规则分只反映结构/方法完整度，不等于内容质量。文本短/疑似堆词/命中过少 → 低置信。
    conf = _score_confidence(len(text or ""), subject, covered, ncats, depth_hits, innov_count)
    conf["caveat"] = "本地规则分只反映结构与方法完整度，不代表内容质量或写作水平；深度质量判断请以 AI 分析为准。"
    result["confidence"] = conf

    # 全局建议（按分数最低维度优先）
    dims_sorted = sorted(result["dimensions"].values(), key=lambda d: d["score"] / d["max"])
    for d in dims_sorted:
        if d["suggestions"]:
            result["suggestions"].extend(d["suggestions"][:1])  # 每维度最多取首条
    if conf["level"] == "低":
        result["suggestions"].insert(0, "⚠️ 规则分置信度低（" + "；".join(conf["reasons"]) + "），请以 AI 深度分析为准")

    return result


def build_overview(quality_score, matches):
    """生成「综合点评」——本地拼装的一段自然语言总评 + 重点建议（不调云）。

    依据：四维体检分（总分/档位/最强最弱维度）+ 规则命中情况（核心条数/平均显著度）。
    返回 {score, level, text, suggestions}，供结果页「综合点评」区块渲染。
    """
    total = quality_score.get("total", 0)
    dims = quality_score.get("dimensions", {}) or {}

    if total >= 85:
        level = "优秀"
    elif total >= 70:
        level = "良好"
    elif total >= 55:
        level = "中等"
    else:
        level = "待改进"

    # 最强 / 最弱维度（按得分率）
    dim_list = [d for d in dims.values() if d.get("max")]
    strongest = max(dim_list, key=lambda d: d["score"] / d["max"], default=None)
    weakest = min(dim_list, key=lambda d: d["score"] / d["max"], default=None)

    # 核心命中 + 平均显著度
    keeps = [m for m in matches if m.get("action") == "keep"]
    sal = [m.get("salience") for m in keeps if isinstance(m.get("salience"), (int, float))]
    avg_sal = round(sum(sal) / len(sal) * 100) if sal else None

    parts = [f"本文综合质量评分 {total}/100，整体评级「{level}」。"]
    if strongest and weakest and strongest is not weakest:
        parts.append(
            f"四维体检中，「{strongest['label']}」表现最好（{strongest['score']}/{strongest['max']}），"
            f"「{weakest['label']}」相对薄弱（{weakest['score']}/{weakest['max']}）。"
        )
    if keeps:
        seg = f"系统共提取 {len(keeps)} 条核心内容"
        if avg_sal is not None:
            seg += f"，平均显著度 {avg_sal}%"
        parts.append(seg + "。")
    else:
        parts.append("未提取到规则命中的核心内容，已用关键词密度兜底补充候选片段。")

    text = "".join(parts)

    # 重点建议：从最弱的两个维度各取首条建议
    suggestions = []
    for d in sorted(dim_list, key=lambda d: d["score"] / d["max"])[:2]:
        for s in d.get("suggestions", [])[:1]:
            suggestions.append(s)

    return {"score": total, "level": level, "text": text, "suggestions": suggestions}


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# 本地模型有时不严格遵守提示词的英文键名，改用中文键。这里把常见中文别名归一化为
# 规范英文键，使结果页（按英文键渲染）无论模型用哪种键名都能正确展示。
_AI_KEY_ALIASES = {
    "研究问题": "research_question", "核心方法": "core_method",
    "关键公式": "key_formulas", "公式": "key_formulas", "公式描述": "key_formulas",
    "实验数据": "experimental_data", "关键实验数据": "experimental_data", "实验结果": "experimental_data",
    "结论": "conclusion", "主要结论": "conclusion",
    "创新点": "innovations", "创新贡献": "innovations",
    "潜在风险": "potential_risks", "潜在局限": "potential_risks", "局限": "potential_risks", "风险": "potential_risks",
    "改进建议": "improvement_suggestions", "建议修改方向": "improvement_suggestions", "修改建议": "improvement_suggestions",
    "研究背景": "background", "背景": "background",
    "实验亮点": "highlights", "亮点": "highlights",
    "变量": "variables", "实验设置": "experiment_setup", "关键结果": "key_results",
}


def _normalize_ai_key(k):
    """单个键名归一化：先精确匹配，再按子串包含兜底（应对「潜在风险或局限」这类复合键）。"""
    if k in _AI_KEY_ALIASES:
        return _AI_KEY_ALIASES[k]
    # 子串兜底：键里包含某中文别名即归一（长别名优先，避免「风险」先于「潜在风险」命中）
    for alias in sorted((a for a in _AI_KEY_ALIASES if any('一' <= c <= '鿿' for c in a)),
                        key=len, reverse=True):
        if alias in k:
            return _AI_KEY_ALIASES[alias]
    return k


def _normalize_ai_keys(obj):
    """把 AI 结果里的中文键名归一化为规范英文键（已是英文或未知键则原样保留）。"""
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        nk = _normalize_ai_key(k)
        # 不覆盖已存在的规范键（优先保留先出现的）
        if nk not in out:
            out[nk] = v
    return out


def _extract_ai_json(raw):
    """
    从 LLM 输出里稳健地提取 JSON：先去 markdown 围栏直接解析，失败则从任意位置
    抓出最外层 {...} 再解析。推理模型（如 deepseek-r1）常在 JSON 前后带散文，
    单纯剥围栏不够，故加一层「抓对象」兜底。全失败时退回 {"raw": ...} 由前端原样展示。
    """
    import re as _re
    s = raw.strip()
    s = _re.sub(r'^```(?:json)?\s*', '', s)
    s = _re.sub(r'\s*```$', '', s)
    # 1) 严格解析（云端规整输出走这条，最快）
    try:
        return _normalize_ai_keys(json.loads(s))
    except Exception:
        pass
    # 2) 从任意位置抓最外层 {...} 再严格解析（应对 JSON 前后夹带散文）
    m = _re.search(r'\{.*\}', s, _re.DOTALL)
    candidate = m.group(0) if m else s
    try:
        return _normalize_ai_keys(json.loads(candidate))
    except Exception:
        pass
    # 3) json_repair 兜底：修本地推理模型常见的烂 JSON（裸百分号 63.8%、尾逗号、
    #    LaTeX 反斜杠、缺右括号等）。云端规整输出走不到这里。
    try:
        import json_repair
        repaired = json_repair.loads(candidate)
        if isinstance(repaired, (dict, list)) and repaired:
            return _normalize_ai_keys(repaired)
    except Exception:
        pass
    return {"raw": raw}


def _text_quality_ok(text, min_ratio=0.6):
    """
    判断抽取出的文本是否「像正常文字」，用于挡掉 OCR 乱码（如西里尔/拉丁怪符、
    mojibake）。统计有效字符占比：中日韩文字 + 英文字母数字 + 常见标点/空白。
    占比过低视为乱码，应继续尝试下游抽取方式。空文本直接判为不合格。
    """
    if not text or not text.strip():
        return False
    # 坏字体（无 ToUnicode 映射的 CID 字体）会被 pdfplumber/PyPDF2 渲染成大量 "(cid:12)" 记号，
    # 这些记号全是 ASCII 词字符，会骗过下面的占比统计（误判为正常文字）→ 先单独识别并否决。
    n_cid = text.count("(cid:")
    if n_cid and n_cid * 8 > len(text) * 0.15:
        return False
    good = 0
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        code = ord(ch)
        is_cjk = 0x4E00 <= code <= 0x9FFF
        is_ascii_word = ch.isascii() and (ch.isalnum() or ch in ".,;:!?()[]{}'\"-+/=%@#&*<>|_~$")
        is_cjk_punct = ch in "，。、；：！？（）【】「」『』“”‘’《》—…·"
        if is_cjk or is_ascii_word or is_cjk_punct:
            good += 1
    if total == 0:
        return False
    return (good / total) >= min_ratio


def _page_text_reading_order(page):
    """pdfplumber 单页文本抽取（直接用稳定的 extract_text）。

    注（实测后的决定）：曾尝试用词级 bbox 做『双栏阅读顺序重建』来修两栏交错，但量过数据后
    放弃——真正会落到 pdfplumber 兜底的 PDF，往往字间空格在字符级就已丢失，extract_words
    把整行连成超宽 token，几何判栏因此失效（实测单栏/双栏的『跨中缝词占比』都在 ~19%，无法区分）；
    而把单栏误判成双栏会『劈行重排』造成灾难性乱序。故不做几何重排，只保留安全的 _reflow_text
    去连字符 / 并软换行。双栏交错列为兜底路径的已知局限——docling 主路径版面已理顺，不受影响。"""
    return page.extract_text() or ""


def _reflow_text(text):
    """把『按版面折行』的文本里『句中软换行 + 连字符断词』规整成正常句子/段落。
    只用于会折行的来源（pdfplumber/PyPDF2/OCR）；docling 输出已规整，不经过这里。
    保守优先：句末标点结尾、或下一行像新段落/标题/编号/参考条目，则保留换行——拿不准不并。"""
    if not text:
        return text
    import re
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # 英文连字符断词： "wor-\nd" → "word"（连字符 + 换行 + 行首小写字母）
    text = re.sub(r'([A-Za-z])-\n[ \t]*([a-z])', r'\1\2', text)

    def ends_sentence(s):
        s = s.rstrip()
        return bool(s) and s[-1] in '。！？.!?；;:：'

    def is_new_block(s):
        s = s.strip()
        return (not s) or bool(re.match(
            r'^(\d+(\.\d+)*[\.\)、]?\s|[•·\-\*]\s|\[\d+\]|第[一二三四五六七八九十\d]+[章节]|'
            r'(Abstract|Introduction|Related Work|Method|Conclusion|References|Acknowledg|'
            r'图|表|Fig|Table|Algorithm)\b)', s))

    out, buf = [], ''
    for ln in text.split('\n'):
        cur = ln.strip()
        if not cur:
            if buf:
                out.append(buf)
                buf = ''
            continue
        if not buf:
            buf = cur
        elif ends_sentence(buf) or is_new_block(ln):
            out.append(buf)
            buf = cur
        else:
            # 句中软换行 → 并接：中文衔接不加空格，其余加空格
            joiner = '' if ('一' <= buf[-1] <= '鿿' and '一' <= cur[0] <= '鿿') else ' '
            buf = buf + joiner + cur
    if buf:
        out.append(buf)
    return '\n'.join(out)


def extract_text(file_path):
    """
    使用多种方式提取PDF/文档文本，提高解析成功率
    优先级：docling（文字层结构化）> pdfplumber > PyPDF2 > 本地 OCR（扫描件兜底）
    每一步都过文本质量闸，乱码（如 docling 对中文扫描件的 OCR 噪声）会被跳过。
    """
    text = ""
    # 只取文件名的扩展名：用 splitext 而非对全路径 rsplit('.')，
    # 避免目录名含点（如 "26.03..."）导致取错、或无扩展名时 IndexError。
    file_ext = os.path.splitext(file_path)[1].lstrip('.').lower()

    # 方式1: docling（首选，支持结构化解析；已关 do_ocr，只读文字层）
    if DOCLING_OK and _converter:
        try:
            result = _converter.convert(file_path)
            text = result.document.export_to_text()
            if text and text.strip() and _text_quality_ok(text):
                print(f"[docling] 解析成功，文本长度：{len(text)}")
                return text
            elif text and text.strip():
                print("[docling] 输出疑似乱码，跳过并尝试下游方式")
        except Exception as e:
            print(f"[docling] 解析失败：{e}")

    # 方式2: pdfplumber（备选，PDF专用）
    # 每步用独立 text 累加（reset），避免上一步的乱码残留被 += 拼进来；输出同样过质量闸。
    if file_ext == 'pdf':
        try:
            text = ""
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = _page_text_reading_order(page)
                    if page_text:
                        text += page_text + '\n'
            text = _reflow_text(text)
            if text and text.strip() and _text_quality_ok(text):
                print(f"[pdfplumber] 解析成功，文本长度：{len(text)}")
                return text
            elif text and text.strip():
                print("[pdfplumber] 输出疑似乱码（坏字体/水印层），跳过并尝试下游方式")
        except Exception as e:
            print(f"[pdfplumber] 解析失败：{e}")

    # 方式3: PyPDF2（备选，兼容性最好）
    if file_ext == 'pdf':
        try:
            text = ""
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + '\n'
            text = _reflow_text(text)
            if text and text.strip() and _text_quality_ok(text):
                print(f"[PyPDF2] 解析成功，文本长度：{len(text)}")
                return text
            elif text and text.strip():
                print("[PyPDF2] 输出疑似乱码（坏字体/水印层），跳过并尝试 OCR")
        except Exception as e:
            print(f"[PyPDF2] 解析失败：{e}")

    # 方式4: 本地 OCR 兜底（扫描件 / 无文字层 / 坏字体乱码 PDF）
    # 触发条件：前面所有文字层抽取都拿不到文本或输出乱码（未过质量闸）——正常文字版 PDF
    # 不会走到这里，故对常规论文零额外开销；OCR 较慢，作为最后一道防线保证也能解析。
    if file_ext == 'pdf' and OCR_OK:
        try:
            print("[OCR] 文字层抽取为空或乱码，尝试本地 OCR 兜底...")
            text = _reflow_text(ocr_pdf(file_path))
            if text and text.strip():
                print(f"[OCR] 兜底解析成功，文本长度：{len(text)}")
                return text
        except Exception as e:
            print(f"[OCR] 兜底解析失败：{e}")

    # 方式5: 直接读取文本文件
    if file_ext in {'txt', 'md'}:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            print(f"[直接读取] 解析成功，文本长度：{len(text)}")
            return text
        except Exception as e:
            print(f"[直接读取] 解析失败：{e}")

    return ""


def process_content(text, mode='extract'):
    """
    处理文本内容
    mode: 'extract' 核心提取 | 'review' 冗余审查 | 'full' 完整处理
    """
    matches = match_rules(text)

    # 分类匹配结果
    keep_items = [m for m in matches if m.get('action') == 'keep']
    review_items = [m for m in matches if m.get('action') == 'review']

    if mode == 'extract':
        # 只保留核心内容
        result_text = ""
        last = 0
        for m in sorted(keep_items, key=lambda x: x['start']):
            s, e = m['start'], m['end']
            if last < s:
                result_text += text[last:s]
            result_text += f"**【核心】{text[s:e]}**\n"
            last = e
        result_text += text[last:]
        return result_text, keep_items

    elif mode == 'review':
        # 标记待审查内容
        result_text = text
        # 按位置倒序处理，避免索引偏移
        for m in sorted(review_items, key=lambda x: x['start'], reverse=True):
            s, e = m['start'], m['end']
            result_text = result_text[:s] + f"⚠️【可能冗余】{result_text[s:e]}⚠️" + result_text[e:]
        return result_text, review_items

    else:  # full
        return text, matches


# ==================== 路由处理 ====================

# 体验区（EXPERIENCE_MODE）下关闭这些「会列出他人数据」或「本地版专属 / 烧钱」的
# 页面与接口——隐私红线（决策 D）：公网多访客时绝不能让访客 A 看到访客 B 的论文。
# 服务端强制拦截，与前端隐藏入口形成双保险。
_EXP_BLOCKED = ('/history', '/documents', '/reports', '/settings',
                '/api/history', '/api/documents', '/api/ai/enhance')


@app.before_request
def _experience_guard():
    if not experience.is_on():
        return None
    p = request.path
    for pre in _EXP_BLOCKED:
        if p == pre or p.startswith(pre + '/'):
            if p.startswith('/api/'):
                return jsonify({"code": 403, "msg": "体验区不提供该功能"}), 403
            return redirect('/')
    return None


@app.route('/')
def index():
    """主页。体验区形态下顺手给访客下发 pc_vid（按访客配额用）。"""
    resp = Response(render_template('index.html'))
    if experience.is_on():
        vid, is_new = _get_or_make_vid()
        if is_new:
            resp.set_cookie('pc_vid', vid, max_age=15552000, httponly=True, samesite='Lax')
    return resp


@app.route('/result')
def result():
    """结果页（数据通过 sessionStorage 传递）"""
    return render_template('result.html')


@app.route('/history')
def history_page():
    """分析历史页（列表通过 /api/history 拉取；回看时把完整结果塞回 sessionStorage 再跳 /result）"""
    return render_template('history.html')


@app.route('/api/history', methods=['GET'])
def api_history_list():
    """历史摘要列表（最新在前）。"""
    return jsonify({"code": 200, "msg": "ok", "data": history.list_records()})


@app.route('/api/history', methods=['DELETE'])
def api_history_clear():
    """一键清空全部历史。"""
    n = history.clear_records()
    return jsonify({"code": 200, "msg": f"已清空 {n} 条历史", "data": {"deleted": n}})


@app.route('/api/history/<rec_id>', methods=['GET'])
def api_history_get(rec_id):
    """取某条完整结果，供回看（前端塞回 sessionStorage 重渲染 /result）。"""
    rec = history.get_record(rec_id)
    if rec is None:
        return jsonify({"code": 404, "msg": "记录不存在"}), 404
    return jsonify({"code": 200, "msg": "ok", "data": rec})


@app.route('/api/history/<rec_id>', methods=['DELETE'])
def api_history_delete(rec_id):
    """删除单条历史。"""
    ok = history.delete_record(rec_id)
    code = 200 if ok else 404
    return jsonify({"code": code, "msg": "已删除" if ok else "记录不存在",
                    "data": {"deleted": ok}}), code


# ==================== 我的文档（已上传文件库） ====================

def _original_name(safe_filename):
    """去掉上传时加的 {timestamp}_ 前缀，还原用户的原始文件名。"""
    import re as _re
    return _re.sub(r'^\d+_', '', safe_filename)


def _scan_documents():
    """扫描 uploads/，按原始文件名去重（保留最新），附带「分析过几次」。"""
    folder = app.config['UPLOAD_FOLDER']
    try:
        names = [n for n in os.listdir(folder)
                 if os.path.isfile(os.path.join(folder, n)) and not n.startswith('.')]
    except FileNotFoundError:
        names = []

    # 历史里同名文件的分析次数
    counts = {}
    for r in history.list_records():
        fn = r.get('filename')
        if fn:
            counts[fn] = counts.get(fn, 0) + 1

    # 按原始名分组，保留 mtime 最新的物理文件
    by_orig = {}
    for n in names:
        path = os.path.join(folder, n)
        orig = docnames.lookup(n) or _original_name(n)
        st = os.stat(path)
        cur = by_orig.get(orig)
        if cur is None or st.st_mtime > cur['_mtime']:
            by_orig[orig] = {
                'safe_filename': n,
                'filename': orig,
                'size': st.st_size,
                '_mtime': st.st_mtime,
                'time_str': time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime)),
                'analyzed_count': counts.get(orig, 0),
            }
    docs = sorted(by_orig.values(), key=lambda d: d['_mtime'], reverse=True)
    for d in docs:
        d.pop('_mtime', None)
    return docs


def _safe_upload_path(safe_filename):
    """把传入文件名收敛到 uploads/ 内的真实路径，挡掉路径穿越。"""
    name = os.path.basename(safe_filename)
    folder = app.config['UPLOAD_FOLDER']
    path = os.path.join(folder, name)
    if os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) != os.path.abspath(folder):
        return None
    return path if os.path.isfile(path) else None


@app.route('/documents')
def documents_page():
    """我的文档页（已上传文件库）。"""
    return render_template('documents.html')


@app.route('/api/documents', methods=['GET'])
def api_documents_list():
    return jsonify({"code": 200, "msg": "ok", "data": _scan_documents()})


@app.route('/api/documents/<path:safe_filename>/download')
def api_documents_download(safe_filename):
    path = _safe_upload_path(safe_filename)
    if path is None:
        return jsonify({"code": 404, "msg": "文件不存在"}), 404
    name = os.path.basename(path)
    return send_from_directory(app.config['UPLOAD_FOLDER'], name,
                               as_attachment=True, download_name=_original_name(name))


@app.route('/api/documents/<path:safe_filename>', methods=['DELETE'])
def api_documents_delete(safe_filename):
    path = _safe_upload_path(safe_filename)
    if path is None:
        return jsonify({"code": 404, "msg": "文件不存在"}), 404
    try:
        os.remove(path)
    except OSError as e:
        return jsonify({"code": 500, "msg": f"删除失败：{e}"}), 500
    docnames.forget(os.path.basename(safe_filename))
    return jsonify({"code": 200, "msg": "已删除", "data": {"deleted": True}})


@app.route('/api/documents/<path:safe_filename>/reanalyze', methods=['POST'])
def api_documents_reanalyze(safe_filename):
    """对「我的文档」里已存在的文件直接重新分析（无需重新上传）。

    文件本就在 uploads/ 里，按 safe_filename 定位后复用 _analyze_and_respond；
    分析参数（模式 / AI 开关）照常从 request.form 读，与首次上传完全一致。
    """
    path = _safe_upload_path(safe_filename)
    if path is None:
        return jsonify({"code": 404, "msg": "文件不存在或已被删除"}), 404
    name = os.path.basename(safe_filename)
    display_filename = docnames.lookup(name) or _original_name(name)
    return _analyze_and_respond(path, display_filename)


# ==================== 结构化报告（报告中心 / 批量导出） ====================

@app.route('/reports')
def reports_page():
    """报告中心页（基于分析历史，逐条导出 Markdown / TXT）。"""
    return render_template('reports.html')


@app.route('/about')
def about_page():
    """关于我们 / 详解页（独立落地页 landing/index.html，自包含单文件）。"""
    return send_from_directory(os.path.join(app.root_path, 'landing'), 'index.html')


@app.route('/brand/')
def brand_page():
    """品牌页入口：默认英语。语言由用户在 /brand/regions/ 自选（不记忆、不自动跳、不按 IP）。"""
    return redirect('/brand/en/')


@app.route('/brand/regions/')
def brand_regions_page():
    """语言/地区选择页（独立自包含单文件）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'regions'), 'index.html')


@app.route('/brand/en/')
def brand_en_page():
    """英文品牌页（warm 副本 + 语言入口；warm 原件冻结不动）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'en'), 'index.html')


@app.route('/brand/en/<path:filename>')
def brand_en_assets(filename):
    """发布 brand/en 下的静态资源。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'en'), filename)


@app.route('/brand/zh/')
def brand_zh_page():
    """中文品牌页「墨析」（独立内容版：文案 + Demo 论文中文化）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'zh'), 'index.html')


@app.route('/brand/zh/<path:filename>')
def brand_zh_assets(filename):
    """发布 brand/zh 下的静态资源。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'zh'), filename)


@app.route('/brand/<path:filename>')
def brand_assets(filename):
    """发布 brand/warm 下的静态资源（style.css / script.js）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'warm'), filename)


@app.route('/brand/ja/')
def brand_ja_page():
    """日语品牌广告页（brand/ja，独立内容版：文案 + Demo 示例论文均日语化）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'ja'), 'index.html')


@app.route('/brand/ja/<path:filename>')
def brand_ja_assets(filename):
    """发布 brand/ja 下的静态资源（style.css / script.js）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'ja'), filename)


@app.route('/brand/ko/')
def brand_ko_page():
    """韩语品牌广告页（brand/ko，独立内容版：文案 + Demo 示例论文均韩语化）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'ko'), 'index.html')


@app.route('/brand/ko/<path:filename>')
def brand_ko_assets(filename):
    """发布 brand/ko 下的静态资源（style.css / script.js）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'ko'), filename)


@app.route('/brand/de/')
def brand_de_page():
    """德语品牌广告页（brand/de，独立内容版：文案 + Demo 示例论文均德语化）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'de'), 'index.html')


@app.route('/brand/de/<path:filename>')
def brand_de_assets(filename):
    """发布 brand/de 下的静态资源（style.css / script.js）。"""
    return send_from_directory(os.path.join(app.root_path, 'brand', 'de'), filename)


@app.route('/settings')
def settings_page():
    """系统设置页（只读，实时反映后端运行状态）。"""
    return render_template('settings.html')


@app.route('/api/history/<rec_id>/report')
def api_history_report(rec_id):
    """把某条历史结果生成结构化报告并作为附件下载。format=md|txt。"""
    rec = history.get_record(rec_id)
    if rec is None:
        return jsonify({"code": 404, "msg": "记录不存在"}), 404
    fmt = request.args.get('format', 'md')
    if fmt not in ('md', 'txt'):
        fmt = 'md'
    content = report.build_report(rec, fmt)
    ext = 'md' if fmt == 'md' else 'txt'
    mime = 'text/markdown; charset=utf-8' if fmt == 'md' else 'text/plain; charset=utf-8'
    return Response(content, mimetype=mime, headers={
        'Content-Disposition': f'attachment; filename="PaperCore_report_{rec_id}.{ext}"'
    })


# ==================== 隐藏演示路由 ====================

_DEMO_DATA = {
    "quick": {
        "filename": "基于多尺度注意力机制的医学图像分割方法研究.pdf",
        "text_length": 18432,
        "analysis_mode": "quick",
        "quality_score": {
            "total": 88,
            "dimensions": {
                "structure":    {"score": 23, "max": 25, "label": "结构完整度",    "detail": "检测到 5/5 个标准章节", "suggestions": []},
                "innovation":   {"score": 20, "max": 25, "label": "创新声明密度",  "detail": "检测到 4 处创新声明关键词", "suggestions": []},
                "data_support": {"score": 22, "max": 25, "label": "数据支撑度",    "detail": "结论含 6 个数值，与实验章节共享 4 个", "suggestions": []},
                "method":       {"score": 23, "max": 25, "label": "方法描述完整性","detail": "检测到 8 个方法描述关键词", "suggestions": ["建议增加消融分析以验证各模块的独立贡献"]},
            },
            "suggestions": ["建议增加消融分析以验证各模块的独立贡献"]
        },
        "ai_result": {
            "research_question": "针对现有医学图像分割方法在边缘细节捕获与计算效率之间难以权衡的问题，本文提出一种基于多尺度注意力机制的轻量化分割框架，旨在提升模型对病灶边界的精准识别能力。",
            "core_method": "以 U-Net 为基础架构，在跳跃连接处引入多尺度通道注意力模块（MSCA），动态融合不同感受野的特征响应；编码器采用深度可分离卷积替换标准卷积，降低参数量约 40%。",
            "conclusion": "在 ISIC 2018 皮肤病变数据集和 Kvasir-SEG 结肠镜数据集上，所提方法 Dice 系数分别达到 89.7% 和 91.2%，较基线模型提升 2.3% 和 1.8%，推理速度提高 34%，验证了方法的有效性与临床适用性。"
        },
        "matches": [],
        "stats": {"keep_count": 0, "review_count": 0, "total_matches": 0}
    },
    "structured": {
        "filename": "面向低资源场景的知识图谱关系补全方法.pdf",
        "text_length": 24617,
        "analysis_mode": "structured",
        "quality_score": {
            "total": 76,
            "dimensions": {
                "structure":    {"score": 21, "max": 25, "label": "结构完整度",    "detail": "检测到 4/5 个标准章节", "suggestions": ["未检测到关键词章节，建议在摘要后补充关键词行"]},
                "innovation":   {"score": 25, "max": 25, "label": "创新声明密度",  "detail": "检测到 5 处创新声明关键词", "suggestions": []},
                "data_support": {"score": 18, "max": 25, "label": "数据支撑度",    "detail": "结论含 3 个数值，与实验章节共享 2 个", "suggestions": ["结论中的数据与实验章节数据无充分交叉，建议确认结论直接引用了实验结果"]},
                "method":       {"score": 12, "max": 25, "label": "方法描述完整性","detail": "检测到 4 个方法描述关键词", "suggestions": ["未检测到消融实验，建议增加消融分析以验证各模块的独立贡献"]},
            },
            "suggestions": ["未检测到消融实验，建议增加消融分析", "结论数据与实验章节缺乏交叉引用", "建议在摘要后补充关键词行"]
        },
        "ai_result": {
            "research_question": "知识图谱中存在大量缺失关系，现有方法依赖大量标注数据，在低资源领域（如医疗、法律）效果欠佳。本文针对低资源场景下的关系补全问题，探索小样本迁移学习与图神经网络的结合方案。",
            "core_method": "提出 MetaGNN 框架：以 RGCN 为基础图编码器，通过元学习（MAML 变体）在源域关系上训练通用初始化参数；目标域仅需 5 个支持样本即可快速适配。推理阶段采用原型网络计算关系表征相似度。",
            "key_formulas": [
                "关系得分：f(h,r,t) = σ(eₕᵀ · Rᵣ · eₜ)",
                "元更新：θ* = θ − α · ∇θ L_task",
                "原型距离：d(x, cₖ) = ‖f_θ(x) − cₖ‖²"
            ],
            "experimental_data": "在 FB15k-237 和 NELL-ONE 数据集上评估；5-shot 设置下 MRR 达 0.412，Hits@10 为 63.8%；与 GMatching 基线相比，MRR 提升 6.7%，训练收敛速度提高 2.1×。",
            "conclusion": "实验结果表明 MetaGNN 在低资源场景下显著优于现有方法，小样本迁移能力强；消融实验验证了元学习模块与图编码器的协同作用，缺少任一组件均导致性能下降 4% 以上。",
            "innovations": [
                "首次将 MAML 元学习范式引入知识图谱低资源关系补全任务",
                "提出关系感知图卷积层，显式建模关系类型对邻域聚合的影响",
                "设计自适应支持集采样策略，缓解低资源样本分布偏斜问题"
            ],
            "potential_risks": [
                "源域与目标域关系语义差异较大时，元迁移效果可能显著下降",
                "RGCN 在超大规模图（百万节点级）上存在内存瓶颈，实际部署受限"
            ],
            "improvement_suggestions": [
                "引入关系层次结构先验（如本体树），增强跨域迁移的语义一致性",
                "探索图稀疏化或采样策略，解决大规模图上的可扩展性问题",
                "在医疗 KG（UMLS）等真实低资源场景开展端到端验证实验"
            ]
        },
        "matches": [],
        "stats": {"keep_count": 0, "review_count": 0, "total_matches": 0}
    },
    "formula": {
        "filename": "基于能量均衡路由的无线传感器网络寿命优化研究.pdf",
        "text_length": 21089,
        "analysis_mode": "formula",
        "quality_score": {
            "total": 91,
            "dimensions": {
                "structure":    {"score": 25, "max": 25, "label": "结构完整度",    "detail": "检测到 5/5 个标准章节", "suggestions": []},
                "innovation":   {"score": 20, "max": 25, "label": "创新声明密度",  "detail": "检测到 4 处创新声明关键词", "suggestions": []},
                "data_support": {"score": 25, "max": 25, "label": "数据支撑度",    "detail": "结论含 8 个数值，与实验章节共享 6 个", "suggestions": []},
                "method":       {"score": 21, "max": 25, "label": "方法描述完整性","detail": "检测到 7 个方法描述关键词", "suggestions": ["建议在方法章节明确列出各参数的取值范围与敏感性分析"]},
            },
            "suggestions": ["建议补充超参数敏感性分析"]
        },
        "ai_result": {
            "formulas": [
                {"name": "节点剩余能量",   "expression": "E_r(t) = E_0 − E_tx(t) − E_rx(t)",       "meaning": "节点初始能量减去历史发送与接收能耗之和"},
                {"name": "自由空间传输能耗", "expression": "E_tx = l·E_elec + l·ε_fs·d²",            "meaning": "距离 d 内传输 l 比特数据的能量消耗（d < d₀）"},
                {"name": "多路径衰落能耗",  "expression": "E_tx = l·E_elec + l·ε_amp·d⁴",           "meaning": "远距离（d ≥ d₀）传输的放大器能耗模型"},
                {"name": "簇头选举概率",    "expression": "P(n) = p / (1 − p·(r mod 1/p))",         "meaning": "LEACH 协议中第 r 轮节点 n 被选为簇头的概率"},
                {"name": "网络寿命目标",    "expression": "max T   s.t. ∀n: E_r(n,T) ≥ 0",         "meaning": "在所有节点能量不耗尽的约束下最大化网络存活时间"}
            ],
            "variables": [
                {"symbol": "E_0",    "definition": "节点初始能量，实验中设为 0.5 J"},
                {"symbol": "E_elec", "definition": "电路能耗系数，取 50 nJ/bit"},
                {"symbol": "ε_fs",   "definition": "自由空间放大器系数，取 10 pJ/bit/m²"},
                {"symbol": "ε_amp",  "definition": "多路径放大器系数，取 0.0013 pJ/bit/m⁴"},
                {"symbol": "d₀",     "definition": "自由空间与多路径模型的临界距离，约 87 m"},
                {"symbol": "p",      "definition": "簇头比例参数，实验取 0.05（即 5%）"}
            ],
            "experiment_setup": "仿真平台 MATLAB R2023b；100 个节点随机均匀分布在 100×100 m² 区域；基站位于 (50, 175)；数据包大小 4000 bit；每轮仿真重复 100 次取平均；对比算法：LEACH、TEEN、SEP。",
            "key_results": [
                "网络寿命（FND）：所提方法 2847 轮 vs. LEACH 1623 轮（+75.4%）",
                "能量消耗均衡度（标准差）：0.031 J vs. LEACH 0.089 J（降低 65.2%）",
                "数据传输成功率：98.3%，高于 SEP 的 95.1%",
                "仿真收敛时间：约 0.8 s / 1000 轮（满足实时监控需求）"
            ]
        },
        "matches": [],
        "stats": {"keep_count": 0, "review_count": 0, "total_matches": 0}
    },
    "defense": {
        "filename": "基于差分隐私的联邦学习梯度保护方法研究.pdf",
        "text_length": 19856,
        "analysis_mode": "defense",
        "quality_score": {
            "total": 83,
            "dimensions": {
                "structure":    {"score": 23, "max": 25, "label": "结构完整度",    "detail": "检测到 5/5 个标准章节", "suggestions": []},
                "innovation":   {"score": 25, "max": 25, "label": "创新声明密度",  "detail": "检测到 5 处创新声明关键词", "suggestions": []},
                "data_support": {"score": 22, "max": 25, "label": "数据支撑度",    "detail": "结论含 5 个数值，与实验章节共享 4 个", "suggestions": []},
                "method":       {"score": 13, "max": 25, "label": "方法描述完整性","detail": "检测到 4 个方法描述关键词", "suggestions": ["未检测到消融实验，建议增加消融分析以验证各模块的独立贡献", "建议补充与 SMPC/同态加密等基线的对比实验"]},
            },
            "suggestions": ["建议增加与同类方法的消融对比实验", "方法章节描述可进一步细化"]
        },
        "ai_result": {
            "background": "联邦学习允许多方在不共享原始数据的前提下协同训练模型，但研究表明梯度信息仍可能泄露用户隐私（梯度反转攻击）。本文聚焦于在保持模型效用的前提下，通过差分隐私机制对上传梯度进行保护，解决现有方案噪声过大导致模型精度大幅下降的问题。",
            "innovations": [
                "提出自适应裁剪阈值算法，根据梯度历史分布动态调整裁剪边界，减少不必要的信息损失",
                "设计分层噪声注入策略，对不同层参数按重要性差异化分配隐私预算（ε 分配）",
                "理论证明所提方法满足 (ε, δ)-DP 保证，并在 Rényi 差分隐私框架下给出更紧的隐私分析"
            ],
            "highlights": "在 MNIST、CIFAR-10、医疗影像数据集（ChestX-ray14）上验证：ε=2 时模型精度仅下降 1.2%，而标准 DP-FedAvg 下降 4.7%；隐私审计实验表明梯度反转攻击重建误差提升至 0.91，接近随机猜测上界。",
            "qa_pairs": [
                {"q": "差分隐私引入的噪声会不会严重影响模型收敛？",     "a": "分层噪声策略使关键层（如分类头）获得更少噪声，实验证明收敛轮次仅增加约 12%，精度损失控制在 1.2% 以内。"},
                {"q": "隐私预算 ε 如何选取？实际部署中如何权衡？",       "a": "建议 ε ∈ [1, 5]，ε=2 为精度与隐私的推荐平衡点，可依据业务敏感度参考论文中的隐私-效用曲线选取。"},
                {"q": "与安全多方计算（SMPC）相比，本方法的优劣？",      "a": "本方法计算开销极低，无需多方交互，适合大规模部署；代价是隐私保证为概率性。SMPC 提供信息论级别保证但通信开销高出 10× 以上。"},
                {"q": "本方法是否在真实联邦学习系统中验证过？",           "a": "目前在 PySyft 框架上模拟验证（10~50 参与方）；跨机构真实部署是下一步工作方向。"}
            ]
        },
        "matches": [],
        "stats": {"keep_count": 0, "review_count": 0, "total_matches": 0}
    },
}


@app.route('/demo/<slug>')
def demo(slug):
    if slug not in _DEMO_DATA:
        return "演示页面不存在", 404
    return render_template('result.html', demo_data=_DEMO_DATA[slug])


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """
    文件上传与解析接口
    返回：解析后的文本 + 匹配规则结果
    """
    if 'file' not in request.files:
        return jsonify({"code": 400, "msg": "未找到上传文件"}), 400

    f = request.files['file']
    if f.filename == '':
        return jsonify({"code": 400, "msg": "未选择文件"}), 400

    if not allowed_file(f.filename):
        return jsonify({"code": 400, "msg": "不支持的文件格式，请上传 PDF/DOCX/TXT/MD"}), 400

    # 体验区：到限的访客在落盘前就拦下——不写磁盘、不跑 OCR，省算力也防刷盘。
    if experience.is_on():
        _vid, _ = _get_or_make_vid()
        _q = experience.check(_vid, _client_ip())
        if not _q.get('can_use'):
            _resp = jsonify({"code": 429, "msg": "quota_exhausted",
                             "data": {"quota_exhausted": True, "quota": _q}})
            _resp.set_cookie('pc_vid', _vid, max_age=15552000, httponly=True, samesite='Lax')
            return _resp, 429

    # 保存文件。注意：secure_filename 会剥掉中文名的扩展名（"论文.pdf"→"pdf"），
    # 这里单独保住已被 allowed_file 校验过的扩展名，避免下游按扩展名分发时失效。
    filename = f.filename            # 原始文件名（含中文），用于结果/历史的显示
    ext = f.filename.rsplit('.', 1)[1].lower()
    base = secure_filename(f.filename.rsplit('.', 1)[0]) or 'doc'
    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{base}.{ext}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
    f.save(path)
    print(f"文件已保存：{path}")
    # 记下「safe 物理名 → 原始中文名」，供「我的文档」/「重新分析」展示真名（见 docnames.py）
    # 体验区不记此映射：/documents 已关，避免陌生访客的文件名在 doc_names.json 里堆积。
    if not experience.is_on():
        docnames.remember(safe_filename, filename)

    return _analyze_and_respond(path, filename)


def _client_ip():
    """访客真实 IP：优先 X-Forwarded-For 首段（反代后），回退 remote_addr。"""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr or ''


def _get_or_make_vid():
    """读 pc_vid cookie；缺失/非法则新建。返回 (vid, is_new)。
    vid 为明文随机 hex，不需防篡改（伪造等价于清 cookie，已由 IP 软顶兜住）。"""
    vid = request.cookies.get('pc_vid', '')
    if len(vid) == 32 and all(c in '0123456789abcdef' for c in vid):
        return vid, False
    return experience.new_vid(), True


def _analyze_and_respond(path, display_filename):
    """对 uploads/ 里某个已存在文件跑完整分析链：解析→规则→评分→AI→写历史→返回 JSON。

    新上传（upload_file）与库内「重新分析」（api_documents_reanalyze）共用这一段，
    避免两套分析逻辑各自漂移。分析参数仍从 request.form 读（两个入口都是 form POST）；
    display_filename 是展示用文件名（结果页 / 历史），与物理 safe 文件名解耦。
    """
    # 体验区配额闸门：在解析/OCR 之前先查，到限直接返回，不跑分析、不烧云。
    exp_on = experience.is_on()
    vid = ip = None
    if exp_on:
        vid, _ = _get_or_make_vid()
        ip = _client_ip()
        q = experience.check(vid, ip)
        if not q.get('can_use'):
            resp = jsonify({"code": 429, "msg": "quota_exhausted",
                            "data": {"quota_exhausted": True, "quota": q}})
            resp.set_cookie('pc_vid', vid, max_age=15552000, httponly=True, samesite='Lax')
            return resp, 429

    # 解析文本
    text = extract_text(path)

    if not text.strip():
        return jsonify({"code": 400, "msg": "解析出的文本为空，请检查文件内容"}), 400

    # 规则匹配
    matches = match_rules(text)

    # 统计信息（仅统计非 fallback 的真实规则命中）
    keep_count = len([m for m in matches if m.get('action') == 'keep' and not m.get('is_fallback')])
    review_count = len([m for m in matches if m.get('action') == 'review'])

    # 兜底：核心片段为 0 时，从高密度句子中补 3 条候选。
    # 中英双语：句子切分含英文标点 .!? ；关键词含中英文学术高频词，比较时统一小写。
    if keep_count == 0:
        import re as _re
        sentences = _re.split(r'[。！？；.!?\n]', text)
        density_kw = ['提出', '研究', '方法', '实验', '结论', '模型', '算法', '分析', '设计', '验证', '结果', '性能',
                      'propose', 'method', 'experiment', 'result', 'conclusion', 'model', 'algorithm',
                      'analysis', 'approach', 'performance', 'contribution', 'novel', 'framework']
        scored = []
        for s in sentences:
            s = s.strip()
            if len(s) < 10:
                continue
            s_low = s.lower()
            score = sum(1 for kw in density_kw if kw in s_low)
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        for _, s in scored[:3]:
            matches.append({
                "rule": "fallback_candidate",
                "description": "候选核心片段（关键词密度）",
                "start": text.find(s),
                "end": text.find(s) + len(s),
                "snippet": s,
                "salience": 0.5,
                "action": "keep",
                "is_fallback": True,
            })
    fallback_count = len([m for m in matches if m.get('is_fallback')])

    # 本地章节提取（无论有无 API key 都做，作为 fallback）
    sections = extract_sections(text)
    local_summary = {k: v for k, v in sections.items() if v} or None

    # 本地自研评分（四维体检，不依赖外部 API）
    score_mode = request.form.get('score_mode', 'teacher')
    if score_mode not in SCORE_PROFILES:
        score_mode = 'teacher'
    try:
        teacher_cap = int(float(request.form.get('teacher_cap', 85)))
    except (TypeError, ValueError):
        teacher_cap = 85
    teacher_cap = max(70, min(100, teacher_cap))
    subject = (request.form.get('subject', 'general') or 'general').strip().lower()
    if subject not in SUBJECT_RUBRICS:
        subject = 'general'
    quality_score = analyze_paper_quality(text, sections, score_mode, teacher_cap, subject)

    # AI 深度分析：三档独立引擎，按优先级 v4pro 高级 > 用户填的 Key > 本地大模型 选用。
    # v4pro = deepseek-v3 高级模式（产品线包装）：5h 滚动窗口最多 5 次，配额闸门在后端
    # （usage.check_quota），前端绕过也无效（呼应「刷新无效」需求）。
    analysis_mode = request.form.get('analysis_mode', 'structured')
    api_key = (request.form.get('api_key', '').strip()
               or os.environ.get('DEEPSEEK_API_KEY', '').strip())
    use_local_ai = request.form.get('use_local_ai', '').strip().lower() in ('1', 'true', 'on', 'yes')
    use_v4pro    = request.form.get('use_v4pro', '').strip().lower() in ('1', 'true', 'on', 'yes')
    ai_result = None
    ai_engine_used = None  # 'v4pro' | 'deepseek' | 'ollama' | None，供前端展示「分析引擎」

    prompt_template = ANALYSIS_PROMPTS.get(analysis_mode, ANALYSIS_PROMPTS['structured'])
    prompt = prompt_template.format(text=text[:6000])  # 约 1500~2000 tokens

    # 输出语言本地化：界面语言非中文时，让 LLM 把「字段值」用目标语言写、JSON 字段名(key)保持不变
    # （几乎零成本；读英文论文也能出日文/英文摘要）。zh 时不加，默认中文。
    _LOCALE_NAME = {'en': 'English', 'ja': 'Japanese', 'ko': 'Korean', 'de': 'German'}
    _loc = (request.form.get('locale', 'zh') or 'zh').strip().lower()
    if _loc in _LOCALE_NAME:
        prompt += (f"\n\n【输出语言】请将所有分析内容（各字段的「值」）用{_LOCALE_NAME[_loc]}书写；"
                   f"JSON 的字段名（key）保持原样、不要翻译。"
                   f"(Write all field VALUES in {_LOCALE_NAME[_loc]}; keep all JSON keys unchanged.)")

    if exp_on:
        # 体验区：强制走服务端 DeepSeek flash，忽略前端 api_key / v4pro / 本地大模型（决策 D）。
        server_key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
        if server_key:
            try:
                ok, raw, usage_info = CloudAPI.call('deepseek', server_key, prompt, return_usage=True)
                if ok:
                    ai_result = _extract_ai_json(raw)
                    ai_engine_used = 'deepseek'
                    experience.record(vid, ip)   # 成功才记账，失败不扣额
                    experience.log_cost(vid, 'flash',
                                        (usage_info or {}).get('prompt_tokens'),
                                        (usage_info or {}).get('completion_tokens'))
                else:
                    print(f"[体验区/DeepSeek] {raw}")
            except Exception as e:
                print(f"[体验区/DeepSeek] 调用失败：{e}")
        else:
            print("[体验区] 未配置 DEEPSEEK_API_KEY，无法提供云端分析")
    elif use_v4pro and V4ProAPI.is_available() and usage.check_quota():
        try:
            print("[v4pro] 高级模式分析中（deepseek-v3 + 资深评审 prompt），耗时略长...")
            ok, raw = V4ProAPI.call(prompt)
            if ok:
                ai_result = _extract_ai_json(raw)
                ai_engine_used = 'v4pro'
                usage.record_use()  # 成功才记一次，失败不扣配额
            else:
                print(f"[v4pro] {raw}")
        except Exception as e:
            print(f"[v4pro] 调用失败：{e}")
    elif api_key:
        try:
            ok, raw = CloudAPI.call('deepseek', api_key, prompt)
            if ok:
                ai_result = _extract_ai_json(raw)
                ai_engine_used = 'deepseek'
        except Exception as e:
            print(f"[DeepSeek] 调用失败：{e}")
    elif use_local_ai and OllamaAPI.is_available():
        try:
            print(f"[Ollama] 本地大模型分析中（{OllamaAPI.MODEL}），可能较慢...")
            ok, raw = OllamaAPI.call(prompt)
            if ok:
                ai_result = _extract_ai_json(raw)
                ai_engine_used = 'ollama'
            else:
                print(f"[Ollama] {raw}")
        except Exception as e:
            print(f"[Ollama] 调用失败：{e}")

    result_data = {
        "filename": display_filename,
        "text_length": len(text),
        "text": text[:200000],
        "analysis_mode": analysis_mode,
        "score_mode": score_mode,
        "ai_result": ai_result,
        "ai_engine_used": ai_engine_used,
        "local_summary": local_summary,
        "quality_score": quality_score,
        "analysis_overview": build_overview(quality_score, matches),
        "matches": matches,
        "stats": {
            "keep_count": keep_count,
            "fallback_count": fallback_count,
            "review_count": review_count,
            "total_matches": len(matches)
        }
    }

    # 追加到本地分析历史（local-first）。失败绝不影响上传主流程，故 try/except 兜底。
    # 体验区不落历史：不留存陌生访客的论文，呼应「云端·尝鲜」隐私叙事（/history 也已关）。
    if not exp_on:
        try:
            history.add_record(result_data)
        except Exception as e:
            print(f"[history] 写入历史失败（不影响本次结果）：{e}")
    else:
        # 体验区：分析完即删上传文件，不留存陌生访客的论文（文件已无用，呼应「云端·尝鲜」）。
        try:
            os.remove(path)
        except OSError:
            pass

    resp = jsonify({"code": 200, "msg": "解析成功", "data": result_data})
    if exp_on and vid:
        resp.set_cookie('pc_vid', vid, max_age=15552000, httponly=True, samesite='Lax')
    return resp


@app.route('/api/process', methods=['POST'])
def process_text():
    """
    文本处理接口（提取/审查）
    """
    req = request.json
    if not req:
        return jsonify({"code": 400, "msg": "请求数据为空"}), 400

    text = req.get('text', '')
    mode = req.get('mode', 'extract')  # extract | review | full

    if not text.strip():
        return jsonify({"code": 400, "msg": "文本内容为空"}), 400

    # 处理内容
    processed_text, items = process_content(text, mode)

    return jsonify({
        "code": 200,
        "msg": "处理成功",
        "data": {
            "processed_text": processed_text,
            "items": items,
            "mode": mode
        }
    })


@app.route('/api/ai/enhance', methods=['POST'])
def ai_enhance():
    """
    AI增强功能接口（润色/摘要/关键词）
    需要用户确认风险告知（云端功能）
    """
    req = request.json
    if not req or not req.get('risk_agreed'):
        return jsonify({"code": 403, "msg": "请先阅读并同意AI功能风险告知"}), 403

    engine_type = req.get('engine_type')  # simple | local | cloud
    func = req.get('func_type')  # polish | summarize | keywords
    content = req.get('content')

    if not all([engine_type, func, content]):
        return jsonify({"code": 400, "msg": "缺少必要参数"}), 400

    # 调用AI引擎
    kwargs = {'text': content}
    try:
        if func == 'polish':
            ok, result = ai_engine.process(engine_type, 'polish', **kwargs)
        elif func == 'summarize':
            ok, result = ai_engine.process(engine_type, 'summarize', **kwargs)
        elif func == 'keywords':
            ok, result = ai_engine.process(engine_type, 'keywords', **kwargs)
        else:
            return jsonify({"code": 400, "msg": "不支持的功能类型"}), 400

        if not ok:
            return jsonify({"code": 500, "msg": f"AI处理失败：{result}"}), 500

        return jsonify({
            "code": 200,
            "msg": "AI处理成功",
            "data": {
                "result": result,
                "engine_type": engine_type,
                "func_type": func
            }
        })
    except Exception as e:
        return jsonify({"code": 500, "msg": f"AI处理失败：{str(e)}"}), 500


@app.route('/api/export', methods=['POST'])
def export_file():
    """
    导出处理后的文件
    """
    req = request.json
    if not req:
        return jsonify({"code": 400, "msg": "请求数据为空"}), 400

    content = req.get('content', '')
    format_type = req.get('format', 'txt')  # txt | md

    if not content.strip():
        return jsonify({"code": 400, "msg": "导出内容为空"}), 400

    # 生成导出文件
    timestamp = int(time.time())
    filename = f"processed_{timestamp}.{format_type}"
    path = os.path.join(app.config['OUTPUT_FOLDER'], filename)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

    return jsonify({
        "code": 200,
        "msg": "导出成功",
        "data": {
            "filename": filename,
            "download_url": f"/api/download/{filename}"
        }
    })


@app.route('/api/download/<filename>')
def download_file(filename):
    """下载导出文件"""
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)


@app.route('/api/rules', methods=['GET'])
def get_rules():
    """获取当前规则列表（用于前端展示）"""
    return jsonify({
        "code": 200,
        "data": {
            "rules": RULES,
            "total": len(RULES)
        }
    })


@app.route('/api/status', methods=['GET'])
def get_status():
    """返回后端配置状态，供前端展示"""
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    has_key = key.startswith('sk-') and len(key) > 20
    exp_on = experience.is_on()
    exp_quota = None
    if exp_on:
        vid, _ = _get_or_make_vid()
        exp_quota = experience.check(vid, _client_ip())
    return jsonify({
        "code": 200,
        "data": {
            "api_key_configured": has_key,
            "ocr_available": OCR_OK,
            "ollama_available": OllamaAPI.is_available(),
            "ollama_model": OllamaAPI.MODEL,
            "v4pro_available": V4ProAPI.is_available(),
            "v4pro_quota": usage.get_status(),
            "experience_mode": exp_on,
            "experience_quota": exp_quota,
        }
    })


# 留邮箱按 IP 轻量限频（防灌水）：每 IP 1 小时最多 5 次。重启即清，MVP 够用。
_waitlist_hits = {}
_waitlist_lock = threading.Lock()


@app.route('/api/waitlist', methods=['POST'])
def api_waitlist():
    """体验区到限后留邮箱候补（验证付费意愿）。仅 EXPERIENCE_MODE 下可用。"""
    if not experience.is_on():
        return jsonify({"code": 403, "msg": "未开启"}), 403
    ip = _client_ip()
    now = time.time()
    with _waitlist_lock:
        hits = [t for t in _waitlist_hits.get(ip, []) if now - t < 3600]
        if len(hits) >= 5:
            return jsonify({"code": 429, "msg": "提交太频繁，请稍后再试"}), 429
        hits.append(now)
        _waitlist_hits[ip] = hits
    payload = request.get_json(silent=True) or request.form
    email = (payload.get('email') or '').strip()
    source = (payload.get('source') or 'limit').strip()[:40]
    vid, _ = _get_or_make_vid()
    ok, msg = experience.add_waitlist(email, source=source, vid=vid)
    if ok:
        return jsonify({"code": 200, "msg": "已记录，上线会第一时间通知你"})
    return jsonify({"code": 400, "msg": "邮箱格式不太对，再检查下" if msg == 'invalid' else "提交失败，稍后再试"}), 400


@app.route('/admin/stats')
def admin_stats():
    """体验区运营看板：分析数 / 独立访客 / tokens / 估算成本¥ / 留邮箱数。
    M1 核心产出，喂定价校准。用 env EXPERIENCE_ADMIN_TOKEN 保护，?token= 传入。
    默认出 HTML 便于眼看；?format=json 给机读。"""
    token = os.environ.get('EXPERIENCE_ADMIN_TOKEN', '').strip()
    if not token or request.args.get('token', '') != token:
        return jsonify({"code": 403, "msg": "forbidden"}), 403
    s = experience.stats()
    if request.args.get('format') == 'json':
        return jsonify({"code": 200, "data": s})
    rows = ''.join(f"<tr><td>{k}</td><td><b>{v}</b></td></tr>" for k, v in s.items())
    return (f"<!doctype html><meta charset=utf-8><title>体验区看板</title>"
            f"<style>body{{font:14px/1.7 system-ui,-apple-system,sans-serif;max-width:640px;"
            f"margin:48px auto;padding:0 20px;color:#222}}h1{{font-size:18px}}"
            f"table{{border-collapse:collapse;width:100%}}td{{border:1px solid #e5e5e5;"
            f"padding:7px 12px}}td:first-child{{color:#777;width:58%}}</style>"
            f"<h1>PaperCore 体验区 · 近 {s.get('window_hours','?')}h</h1><table>{rows}</table>")


# ==================== 错误处理 ====================

@app.errorhandler(413)
def too_large(e):
    """文件过大错误处理"""
    return jsonify({"code": 413, "msg": "文件大小超过50MB限制"}), 413


@app.errorhandler(500)
def internal_error(e):
    """服务器内部错误处理"""
    return jsonify({"code": 500, "msg": f"服务器内部错误：{str(e)}"}), 500


# ==================== 启动入口 ====================

if __name__ == '__main__':
    print("=" * 50)
    print("PaperCore · 本地优先的论文核心内容提取系统")
    print("启动中...")
    print("=" * 50)
    # debug 默认关闭：开启会暴露 Werkzeug 交互式调试器（可执行任意代码），
    # 一旦服务被对外暴露（如 ngrok）即为 RCE 风险。本地调试时设 FLASK_DEBUG=1。
    #
    # 端口/主机可配（PORT / HOST env），让本地版与体验版同机并行：
    # 体验版默认挪到 5004，避开本地版的 5003，两个进程互不抢端口；显式设 PORT 一律以 PORT 为准。
    # HOST 默认仅本机；上公网时设 HOST=0.0.0.0（配 nginx 反代）。
    default_port = 5004 if experience.is_on() else 5003
    try:
        port = int(os.environ.get('PORT', default_port))
    except (TypeError, ValueError):
        port = default_port
    host = os.environ.get('HOST', '127.0.0.1').strip() or '127.0.0.1'
    mode_label = '体验版 EXPERIENCE_MODE' if experience.is_on() else '本地版'
    print(f"启动形态：{mode_label}  →  http://{host}:{port}")
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=port, host=host)
