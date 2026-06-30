<div align="center">

# PaperCore

English | [简体中文](README-CH.md)

**A local-first core-content extraction system for engineering papers**

Turn a paper of dozens of pages into "research question / core method / key formulas / experimental data / conclusions" — right on your own computer.
Your data never leaves the machine, and it works offline.

`Python` · `Flask` · `Local-first` · `Pluggable AI engines` · `Built for engineering papers`

</div>

---

## What it is

PaperCore is a **local-first** system for extracting and structuring the core content of academic papers, aimed at students and researchers who need to read papers quickly. It parses PDF / DOCX / TXT / Markdown papers on your own machine and presents, as structured cards, what a paper "did, how it did it, and what the results were".

Processing has five layers; the **first four run entirely on your machine**, and the fifth — cloud AI enhancement — is optional. Even without a network connection or any API key, the system can still produce a basic structured analysis. This is exactly why it is **"not just an AI wrapper"**: the large language model is a replaceable part, not the foundation of the system.

---

## Who it's for, and the pain it solves

| Target user | Pain point | How PaperCore helps |
|---|---|---|
| **Students / exam candidates** | Reading is slow; hard to grasp what a paper actually did | 30-second skim mode gives the core three elements + a defense outline |
| **Researchers / grad students** | Need to filter and locate fast; formulas and data must not be misread | Formula/variable protection + experimental-data extraction, six-dimension structured analysis |
| **Privacy-sensitive cases** (unpublished drafts, confidential projects) | Can't upload originals to a cloud LLM | Local mode runs **fully offline**; files and text never leave the machine |

---

## Why not "just use a large model directly"

The question most asked at defense. The core answer: **PaperCore is a system; the LLM is one replaceable part inside it.**

| Aspect | Calling a cloud LLM directly | PaperCore |
|---|---|---|
| Deployment | Cloud; data leaves the device | Local-first; privacy-safe |
| Input | Plain text | PDF / DOCX / TXT / MD, incl. local OCR for scans |
| Extraction | Improvised per prompt | Fixed rule engine + customizable domain priors |
| Scoring basis | None | Salience, anchored to TF-IDF / RAKE / YAKE |
| AI dependency | Always online | Falls back to local rules without a key; fully offline |
| Model swap | Locked to one model | Rules / local small model / local LLM / cloud API, freely switchable |

---

## Measured results (small-sample quantitative evaluation)

To answer "what can the local rules actually achieve", we ran a small-sample evaluation on **10 real engineering papers** (mixed Chinese/English, across EEG, ECG, remote sensing, medical, systems, communications, and more), with **human-labeled gold standards**:

| Dimension | Result (n=10) |
|---|---|
| Local rules · standard-section detection | micro **P 0.98 / R 0.78 / F1 0.87** |
| Core method-sentence extraction | **Recall@10 0.74** |
| AI enhancement · info-completeness gain | avg **+69%** |

Section detection and core-sentence recall run **entirely on local rules and salience ranking, and are reproducible offline**.

> **Honest boundary**: this is a small-sample pilot on 10 papers, not a claim of large-scale generalization; the current salience is a heuristic score, and probability calibration is listed as future work.

---

## Core capabilities

- **🔍 Smart section detection** — automatically identifies abstract, introduction, method, experiment, conclusion, etc.
- **∑ Formula & variable protection** — precisely extracts math formulas and variable definitions, keeping the symbol system intact.
- **📊 Experimental-data extraction** — identifies tables, chart data, and key experimental conclusions.
- **🎯 Salience scoring** — gives each extracted snippet a literature-grounded, traceable importance score.
- **🧩 Semantic keyword matching (optional)** — beyond literal keyword lists, an optional local sentence-embedding model also credits paraphrased innovation/method statements; it downloads once, then runs offline, and falls back to literal matching if absent.
- **🖨 Scanned-document OCR** — for image-only scanned/photographed papers, falls back to local OCR, with adaptive image preprocessing (deskew + binarization) for low-quality scans.
- **📑 Structured reports** — generates structured reports, exportable as TXT / Markdown.
- **🗂 History / My documents / Report center** — saves and revisits analyses locally, with a file library and batch export.
- **🌐 Multilingual UI** — interface switchable between Chinese, English and Japanese.

### Four analysis modes

| Mode | Use |
|---|---|
| ⚡ 30-second skim | Extract the core three elements; decide whether to read closely |
| 🔬 Structured understanding | Six-dimension deep analysis, full structured report |
| ∑ Formula/experiment protection | Precisely preserve technical data and symbols |
| 🎓 Defense prep | Directly generate a defense outline |

---

## Salience: scoring with a basis

The old approach used a hand-tuned formula `conf = min(0.95, 0.4 + len/50)` — longer means higher, with no theoretical basis. PaperCore reworks it into **salience**, anchored to three widely recognized keyword/importance measures:

- **TF-IDF** — Spärck Jones, 1972
- **RAKE** — Rose et al., 2010
- **YAKE!** — Campos et al., 2020

The scoring logic is isolated in [`salience.py`](salience.py), with a reproducible self-check script [`salience_selfcheck.py`](salience_selfcheck.py).

---

## The AI engine is pluggable

[`ai_engines.py`](ai_engines.py) exposes a unified `AIEngine` interface with several engines from light to heavy, embodying "AI is a replaceable component":

1. **SimpleAI** — fully local: jieba rule substitution + frequency summary + keyword extraction, no network.
2. **LocalModelAI** — a local open small model (Chinese T5) for offline polishing/summary.
3. **OllamaAPI** — a local LLM via [Ollama](https://ollama.com) (default `deepseek-r1:7b`), deep analysis without leaving the machine.
4. **CloudAPI** — switchable cloud models: DeepSeek / Doubao / Tongyi.
5. **V4ProAPI** — an advanced mode with deeper structured review and a 5-hour rolling quota (freemium form).

Without any key, it defaults to the local path; with Ollama installed, a local LLM can do the deep analysis — **all on your machine**.

---

## Tech stack

| Layer | What's used |
|---|---|
| Web | Flask (local 127.0.0.1:5003) |
| Document parsing | docling → pdfplumber → PyPDF2 → RapidOCR, four-tier fallback |
| Scanned-doc OCR | RapidOCR (local ONNX, built-in CN/EN models) + pypdfium2 rendering |
| Rule engine | [`rules.py`](rules.py) (with domain priors) |
| Salience scoring | [`salience.py`](salience.py) |
| Semantic matching | sentence-transformers · multilingual MiniLM (optional) → [`semantic.py`](semantic.py) |
| Chinese processing | jieba / jieba.analyse |
| AI engines | [`ai_engines.py`](ai_engines.py) (rules / local T5 / Ollama / cloud API) |
| Robust parsing | json_repair (fixes malformed JSON from local LLMs) |
| Frontend | Vanilla HTML/CSS/JS, self-hosted fonts |

---

## Quick start

```bash
# 1. Install dependencies
pip install flask python-dotenv docling pdfplumber PyPDF2 jieba requests json_repair
#   Scanned-doc OCR (local): pip install rapidocr_onnxruntime pypdfium2
#   Local LLM (optional): install Ollama and `ollama pull deepseek-r1:7b`
#   Semantic keyword matching (optional): pip install sentence-transformers, then download the
#     multilingual MiniLM model (~470MB; runs offline afterward, falls back to literal keywords if absent)

# 2. Run
python app.py

# 3. Open in browser
#    http://127.0.0.1:5003
```

- **Offline use**: leave the API key empty and upload a paper; everything runs on local rules and local models, data never leaves the machine.
- **Online enhancement (optional)**: enter a DeepSeek API key on the home page (or set it in `.env`) to call a cloud model for deep analysis.

---

## Design principles

1. **Local-first** — privacy is the default, not a toggle.
2. **Explainability-first** — every score must state its basis and anchor to the literature.
3. **AI is replaceable** — the LLM is a part, not the foundation; swapping models doesn't touch the main pipeline.
4. **Honest boundaries** — if it's a heuristic, say so; mark what's undone (e.g. calibration) as future work.

---

## Roadmap

- [x] Salience rework + literature anchors + end-to-end validation
- [x] Warm academic UI + salience visualization + product narrative
- [x] Scanned-document OCR (local RapidOCR fallback)
- [x] Local LLM via Ollama (deepseek-r1)
- [x] History / My documents / Report center / multilingual UI (Chinese / English / Japanese)
- [x] Small-sample evaluation (10 papers, local rules F1 0.87)
- [x] Adaptive OCR preprocessing (deskew / binarization) for low-quality scans
- [x] Semantic keyword matching via optional local sentence embeddings
- [ ] Probability calibration: turn heuristic salience into a trustworthy probability
- [ ] Broader domain-rule coverage and iteration on real user feedback

---

## Team & acknowledgments

PaperCore was built by a four-member team:

| Member | Role |
|---|---|
| Zhu Houzhen (lead) | Development, system architecture, defense presentation |
| Jiang Yu'ao | Business research, business-plan writing |
| Huang Jiahao | Market analysis |
| Wang Xilin | Testing, user interviews |

Advisors: Yan Jiajie, Li Yepeng.

This project stands on the shoulders of many excellent open-source projects, with thanks to: Flask, docling, pdfplumber, pypdf, pypdfium2, RapidOCR, jieba, Ollama, json-repair, and more.

---

## License

Released under the **MIT** license (see [LICENSE](LICENSE)). Copyright (c) 2026 Zhu Houzhen, Jiang Yu'ao.

---

<div align="center">
<sub>PaperCore · keep the core of every paper on your own computer.</sub>
</div>
