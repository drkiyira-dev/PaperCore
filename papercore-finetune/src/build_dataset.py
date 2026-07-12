"""从 PaperCore 的**自有资产**构建微调用 JSONL 数据 —— 合规、可复现、无闭源蒸馏。

本脚本只用三类**合规来源**产出训练标签，绝不调用/蒸馏任何闭源大模型的输出：

  1) 人工金标准  eval/gold/*.json 里的 `gold_core_sentences`（人手抄的核心方法/贡献句）；
  2) 自研规则引擎  项目根目录 rules.py 的 28 条确定性正则规则（keep/review 两类动作）——
     把「我们自己写的启发式」蒸馏进小模型，天然合规；
  3) 自撰合成样例  examples/sample.jsonl（我们自己手写的抽取式样例）。

由此生成 5 类任务的样本：
  - 章节识别（句 → 引言/方法/实验/结论）        [来源 2]
  - 降噪判断（句 → 核心 / 可精简）               [来源 2，PaperCore 的看家能力]
  - 核心方法句判定（句 → 是/否）                  [来源 1]
  - 核心方法句抽取（候选句列表 → 那一句）          [来源 1]
  - 抽取式概括/JSON（沿用自撰合成样例）            [来源 3]

用法（在 papercore-finetune/ 下，用**主项目的 venv** 跑，因为要 import 主项目的 rules.py）：
    ../venv/bin/python src/build_dataset.py                     # 仅金标准（快，纯净）
    ../venv/bin/python src/build_dataset.py --papers ../eval/papers --max-papers 25   # 加 PDF 富集
产物：data/train.jsonl + data/val.jsonl，并打印类别分布。data/ 默认不进 git。
"""
import argparse
import glob
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FINETUNE_ROOT = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(FINETUNE_ROOT)      # output_projects/
sys.path.insert(0, PROJECT_ROOT)                    # 为了 import rules

# ---- 规则引擎（自研启发式）：可选，import 失败则跳过依赖它的任务 ----
try:
    import rules as _rules_mod
    RULES = _rules_mod.RULES
except Exception as e:                               # pragma: no cover
    RULES = []
    print(f"[warn] 无法 import 主项目 rules.py（{e}）；将只用金标准生成方法类任务。")

# 规则 name → 粗粒度章节（仅对 action=keep 的规则；review 规则不进章节任务）
SECTION_OF = {
    "problem_motivation": "引言",
    "definition_notation": "方法",
    "method_extract": "方法",
    "system_architecture": "方法",
    "algorithm_extract": "方法",
    "innovation_extract": "方法",
    "implementation": "方法",
    "formula_extract": "方法",
    "theorem_proof": "方法",
    "hyperparameter_setting": "实验",
    "experiment_setup": "实验",
    "experiment_result": "实验",
    "performance_gain": "实验",
    "metric_indicator": "实验",
    "figure_table_ref": "实验",
    "statistical_significance": "实验",
    "comparison_baseline": "实验",
    "conclusion_extract": "结论",
    "limitation_future": "结论",
}
# 方法家族：这些 keep 规则视为「核心方法/贡献」，作方法句判定的正类（是）。
# 注意：definition_notation 不在此列——定义/符号不是"贡献"。2026-07-10 错误分析显示，
# 把定义标成"是"会让模型在数学/理论句上乱判"是"（如 "Recall N is defined"）。
METHOD_FAMILY = {"method_extract", "system_architecture", "algorithm_extract",
                 "innovation_extract"}
# 非方法但显著：作方法句判定的负类（否）。后三个是数学/理论"机关"——技术味重但不是
# 核心贡献（定义、定理/证明/假设、公式），明确标"否"来治过判。
NONMETHOD_SALIENT = {"experiment_setup", "experiment_result", "performance_gain",
                     "metric_indicator", "figure_table_ref", "comparison_baseline",
                     "problem_motivation",
                     "definition_notation", "theorem_proof", "formula_extract"}

# 预编译规则（保留 name/action/pattern）
_COMPILED = []
for r in RULES:
    try:
        _COMPILED.append((r["name"], r.get("action", "keep"),
                          re.compile(r["pattern"], re.IGNORECASE | re.DOTALL)))
    except re.error:
        pass


def match_rule(sentence):
    """按 RULES 顺序（核心规则在前）返回首个命中的 (name, action)；无命中返回 (None, None)。"""
    for name, action, pat in _COMPILED:
        if pat.search(sentence):
            return name, action
    return None, None


# ------------------------- 指令模板 -------------------------
I_SECTION = "识别下面这句话所属的论文章节，只输出：引言 / 方法 / 实验 / 结论 之一。"
I_DENOISE = "判断下面这句话是论文的核心内容还是可精简的铺垫/套话，只输出：核心 / 可精简 之一。"
I_ISCORE = "判断下面这句话是否在陈述论文的核心方法或主要贡献，只回答 是 或 否。"
I_PICKCORE = ("下面是从一篇论文中抽取的若干候选句（每行一句），"
              "请选出最能代表其核心方法／主要贡献的一句，原样输出该句。")


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


_JUNK = re.compile(r"(\[[A-Z]\]\s*\.|et al\.|doi:|https?://|arXiv:|^\s*\d+\s*\.\s*$|参考文献|References)", re.IGNORECASE)


def is_sentence_like(s, lo=14, hi=240, require_terminal=False):
    """粗过滤：长度适中、含足够中文或英文字母、非表格/引用残渣。

    require_terminal=True 时要求以句末标点收尾——用于 PDF 抽取的句子，
    过滤掉换行处被截断的半句（金标准句为人工抄录，不强制）。
    """
    s = s.strip()
    if not (lo <= len(s) <= hi):
        return False
    letters = len(re.findall(r"[一-鿿A-Za-z]", s))
    if letters < max(8, len(s) * 0.4):
        return False
    if s.count("|") >= 3 or s.count("\t") >= 2:      # 表格残渣
        return False
    if _JUNK.search(s):                              # 引用/链接/编号残渣
        return False
    if require_terminal and not re.search(r"[。！？.!?][\"'）)\]]?$", s):
        return False
    return True


# 先把 PDF 的换行(硬折行)接回段落，再只按句末标点切句——避免把整句从中间截断
_DEHYPH = re.compile(r"([A-Za-z])-\s*\n\s*([a-z])")     # 英文行尾连字符断词
_WRAP = re.compile(r"\s*\n\s*")
SENT_SPLIT = re.compile(r"(?<=[。！？；])\s*|(?<=[.!?;])\s+")


def split_sentences(text):
    text = _DEHYPH.sub(r"\1\2", text)
    text = _WRAP.sub(" ", text)                      # 折行接回，句子不再被换行截断
    return [p for p in (norm(x) for x in SENT_SPLIT.split(text)) if p]


# ------------------------- 金标准来源（来源 1） -------------------------
def find_candidate_list(d):
    """gold JSON 里「系统 top10 候选句」那一项（键名带 top…句）。"""
    for k, v in d.items():
        if isinstance(v, list) and re.search(r"top\d*句", k) and all(isinstance(x, str) for x in v):
            return v
    return []


def from_gold(gold_dir, rows):
    files = sorted(glob.glob(os.path.join(gold_dir, "*.json")))
    if not files:
        print(f"[warn] {gold_dir} 下没有 gold JSON。")
        return
    n_core = n_neg = n_pick = n_sec = 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        pid = os.path.basename(f)
        start = len(rows)                       # 记录本篇产出的起止，便于按论文打标
        core = [norm(s) for s in d.get("gold_core_sentences", []) if norm(s)]
        cands = [norm(s) for s in find_candidate_list(d) if is_sentence_like(s)]
        core_set = set(core)

        # (a) 方法句判定：正类=人工核心句
        for s in core:
            if is_sentence_like(s):
                rows.append({"instruction": I_ISCORE, "input": s, "output": "是"}); n_core += 1
        # (b) 方法句判定：负类=显著但非方法的候选句（规则判定）
        for s in cands:
            if s in core_set:
                continue
            name, _ = match_rule(s)
            if name in NONMETHOD_SALIENT:
                rows.append({"instruction": I_ISCORE, "input": s, "output": "否"}); n_neg += 1
        # (c) 核心方法句抽取：候选列表 → 人工核心句
        if core:
            pool = list(dict.fromkeys(([core[0]] + cands)))   # 保证正确答案在候选内、去重
            random.shuffle(pool)
            if 2 <= len(pool) <= 12:
                numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(pool))
                rows.append({"instruction": I_PICKCORE, "input": numbered, "output": core[0]})
                n_pick += 1
        # (d) 章节识别：人工核心句 → 方法；候选句 → 规则映射的章节
        for s in core:
            rows.append({"instruction": I_SECTION, "input": s, "output": "方法"}); n_sec += 1
        for s in cands:
            name, action = match_rule(s)
            if action == "keep" and name in SECTION_OF:
                rows.append({"instruction": I_SECTION, "input": s, "output": SECTION_OF[name]}); n_sec += 1
        for r in rows[start:]:
            r["_paper"] = pid                   # 标注这些行来自哪篇论文
    print(f"[gold] 方法句正类 {n_core} / 负类 {n_neg} / 抽取 {n_pick} / 章节 {n_sec}")


# ------------------------- PDF 富集来源（来源 2，可选） -------------------------
def extract_pdf_text(path, max_pages=12):
    """用 pypdfium2（BSD 许可，非 AGPL）抽取前若干页文本。失败返回空串。"""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return ""
    try:
        pdf = pdfium.PdfDocument(path)
        n = min(len(pdf), max_pages)
        chunks = []
        for i in range(n):
            page = pdf[i]
            tp = page.get_textpage()
            chunks.append(tp.get_text_range())
            tp.close(); page.close()
        pdf.close()
        return "\n".join(chunks)
    except Exception:
        return ""


def from_papers(papers_dir, rows, max_papers, per_paper=60, denoise=False, exclude=None):
    """对真实 PDF 跑规则引擎，富集 章节 与 方法句判定 两类样本（弱标签，合规）。

    降噪(核心/可精简)任务默认**不**产出：英文 arXiv 语料极少触发 review/填充规则，
    「可精简」类严重不足，硬塞会让模型学成「永远答核心」。留 --denoise 开关，
    等有填充丰富的语料（如中文论文/含结论致谢全文）再开。
    """
    if not _COMPILED:
        print("[papers] 规则未加载，跳过 PDF 富集。")
        return
    exclude = exclude or set()
    pdfs = [p for p in sorted(glob.glob(os.path.join(papers_dir, "*.pdf")))
            if os.path.basename(p) not in exclude][:max_papers]
    if not pdfs:
        print(f"[warn] {papers_dir} 下没有 PDF。")
        return
    n_keep = n_cut = n_sec = n_pos = n_neg = 0
    for path in pdfs:
        text = extract_pdf_text(path)
        if not text:
            continue
        start = len(rows)
        seen = set()
        kept = 0
        for s in split_sentences(text):
            if not is_sentence_like(s, require_terminal=True) or s in seen:
                continue
            seen.add(s)
            name, action = match_rule(s)
            if action == "keep":
                if name in SECTION_OF:
                    rows.append({"instruction": I_SECTION, "input": s, "output": SECTION_OF[name]}); n_sec += 1
                if name in METHOD_FAMILY:
                    rows.append({"instruction": I_ISCORE, "input": s, "output": "是"}); n_pos += 1
                elif name in NONMETHOD_SALIENT:
                    rows.append({"instruction": I_ISCORE, "input": s, "output": "否"}); n_neg += 1
                if denoise:
                    rows.append({"instruction": I_DENOISE, "input": s, "output": "核心"}); n_keep += 1
                kept += 1
            elif action == "review":
                if denoise:
                    rows.append({"instruction": I_DENOISE, "input": s, "output": "可精简"}); n_cut += 1
                kept += 1
            if kept >= per_paper:
                break
        for r in rows[start:]:
            r["_paper"] = os.path.basename(path)
    msg = f"[papers] {len(pdfs)} 篇 → 章节 {n_sec} / 方法句 是{n_pos}·否{n_neg}"
    if denoise:
        msg += f" / 降噪 核心{n_keep}·可精简{n_cut}"
    print(msg)


# ------------------------- 自撰合成样例（来源 3） -------------------------
def from_synthetic(rows):
    p = os.path.join(FINETUNE_ROOT, "examples", "sample.jsonl")
    if not os.path.exists(p):
        return
    n = 0
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append({**json.loads(line), "_paper": "_synthetic"}); n += 1
    print(f"[synthetic] 自撰样例 {n} 条")


# ------------------------- 平衡 / 去重 / 落盘 -------------------------
def dedup(rows):
    seen, out = set(), []
    for r in rows:
        key = (r["instruction"], r.get("input", ""), r["output"])
        if key not in seen:
            seen.add(key); out.append(r)
    return out


def cap_per_class(rows, cap):
    """按 (instruction, output) 类别做上限截断，避免某类淹没其它类。"""
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        buckets[(r["instruction"], r["output"])].append(r)
    out = []
    for _, items in buckets.items():
        random.shuffle(items)
        out.extend(items[:cap])
    return out


def split_by_paper(rows, val_ratio, seed):
    """按论文切分：val 里的论文整篇留出，其句子绝不出现在 train，杜绝信息泄漏。

    合成样例(_paper='_synthetic')始终进 train。返回 (train, val, val_papers)。
    """
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        by[r.get("_paper", "_synthetic")].append(r)
    papers = [p for p in by if p != "_synthetic"]
    random.Random(seed).shuffle(papers)
    target = int(len(rows) * val_ratio)
    val, val_papers = [], set()
    for p in papers:
        if len(val) >= target:
            break
        val.extend(by[p]); val_papers.add(p)
    train = [r for p in by if p not in val_papers for r in by[p]]
    return train, val, val_papers


def summarize(rows):
    from collections import Counter
    c = Counter((r["instruction"][:14] + "…", r["output"] if len(r["output"]) < 6 else "＜长答案＞")
                for r in rows)
    print("\n[分布] 指令 / 答案 → 条数")
    for k, v in sorted(c.items()):
        print(f"   {k[0]:16} {k[1]:8} {v}")


def main():
    ap = argparse.ArgumentParser(description="构建 PaperCore 合规微调数据集")
    ap.add_argument("--gold", default=os.path.join(PROJECT_ROOT, "eval", "gold"))
    ap.add_argument("--papers", default=None, help="给出 PDF 目录则做富集（如 ../eval/papers）")
    ap.add_argument("--max-papers", type=int, default=25)
    ap.add_argument("--denoise", action="store_true",
                    help="额外产出降噪(核心/可精简)任务；英文语料填充类不足，默认关闭")
    ap.add_argument("--exclude-papers", default="",
                    help="逗号分隔的 PDF 文件名，从 --papers 富集中排除（用来留出测试集论文，防泄漏）")
    ap.add_argument("--cap-per-class", type=int, default=90, help="每个(指令,答案)类别上限，防失衡")
    ap.add_argument("--val-ratio", type=float, default=0.1)
    ap.add_argument("--split", choices=["paper", "row"], default="paper",
                    help="paper=按论文整篇留出(无泄漏,推荐)；row=按行随机(同篇会泄漏,数字虚高)")
    ap.add_argument("--out-dir", default=os.path.join(FINETUNE_ROOT, "data"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    rows = []
    from_gold(args.gold, rows)
    if args.papers:
        exclude = {p.strip() for p in args.exclude_papers.split(",") if p.strip()}
        from_papers(args.papers, rows, args.max_papers, denoise=args.denoise, exclude=exclude)
    from_synthetic(rows)

    rows = dedup(rows)
    rows = cap_per_class(rows, args.cap_per_class)

    os.makedirs(args.out_dir, exist_ok=True)
    if args.split == "paper":
        train, val, val_papers = split_by_paper(rows, args.val_ratio, args.seed)
        print(f"[split] 按论文留出 {len(val_papers)} 篇作 val（无泄漏，推荐）")
    else:
        random.shuffle(rows)
        n_val = max(1, int(len(rows) * args.val_ratio))
        val, train = rows[:n_val], rows[n_val:]
        print("[split] ⚠️ 按行随机切分：同一篇论文的句子可能同时进 train/val（泄漏，数字虚高）")

    for name, part in (("train", train), ("val", val)):
        path = os.path.join(args.out_dir, f"{name}.jsonl")
        with open(path, "w", encoding="utf-8") as fo:
            for r in part:
                r = {k: v for k, v in r.items() if k != "_paper"}   # 落盘前去掉内部字段
                fo.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[write] {path}  ({len(part)} 条)")
    summarize(train)
    print(f"\n[done] 合计 {len(train) + len(val)} 条（train {len(train)} / val {len(val)}）。"
          f"\n       合规：仅人工金标准 + 自研规则引擎 + 自撰合成，无任何闭源模型输出。")


if __name__ == "__main__":
    main()
