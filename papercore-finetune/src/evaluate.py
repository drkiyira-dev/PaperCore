"""在留出集上量化评估 LoRA 模型：逐任务准确率 + 是/否 F1，并与底模并排对比。

用法（在 papercore-finetune/ 下）：
    HF_HUB_OFFLINE=1 .venv/bin/python src/evaluate.py --adapter outputs/papercore-lora-smoke

⚠️ 诚实声明（务必读，直接关系到这些数字能不能信）：
  1) val 的标签大多由**规则引擎自动生成**。所以"准确率高"= 模型成功**模仿**了规则，
     并**不等于**"超过了规则"。要证明超过规则，得用**人工标注**的测试集。
  2) 若 train/val 是按行随机切分，同一篇论文的句子可能同时落在两边（**信息泄漏**），
     这些数字会**高估**真实泛化。用 build_dataset.py 的按论文切分可缓解。
  真正诚实的比较是「底模 vs 底模+LoRA」——同一个模型，加不加 adapter，差值就是微调的净收益。
"""
import argparse
import collections
import json
import os
import sys

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftConfig, PeftModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from format_utils import build_messages  # noqa: E402

SECTION_LABELS = ["引言", "相关工作", "方法", "实验", "结论"]


def pick_device_dtype():
    if torch.cuda.is_available():
        return "cuda", (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
    if torch.backends.mps.is_available():
        return "mps", torch.float32
    return "cpu", torch.float32


def parse_earliest(text, candidates):
    """返回最先出现的候选标签（应对底模啰嗦输出，如「该句属于方法部分」）。"""
    best, best_pos = None, 10 ** 9
    for c in candidates:
        p = text.find(c)
        if 0 <= p < best_pos:
            best, best_pos = c, p
    return best


def parse_yesno(text):
    t = text.strip()
    if t[:6].find("不是") >= 0 or t.startswith("否") or t.startswith("不") or t[:3].lower().startswith("no"):
        return "否"
    if t.startswith("是") or t[:6].find("是的") >= 0 or t[:3].lower().startswith("yes"):
        return "是"
    return parse_earliest(t, ["是", "否"])


def predict_label(output, gold):
    """按 gold 的类型，把模型自由文本解析成可比对的标签。"""
    if gold in ("是", "否"):
        return parse_yesno(output)
    if gold in SECTION_LABELS:
        return parse_earliest(output, SECTION_LABELS)
    return output.strip()          # 抽取/生成类：留原样做规范化精确匹配


@torch.no_grad()
def generate(tok, model, device, instruction, user_input, max_new=24):
    msgs = build_messages(instruction, user_input)
    prompt = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
    ids = tok(prompt, return_tensors="pt").to(device)
    out = model.generate(**ids, max_new_tokens=max_new, do_sample=False,
                         repetition_penalty=1.05, pad_token_id=tok.eos_token_id)
    return tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()


def run_over(rows, tok, model, device, max_new):
    """返回 {instruction: [(pred, gold), ...]}。"""
    by = collections.defaultdict(list)
    for r in rows:
        o = generate(tok, model, device, r["instruction"], r.get("input", ""), max_new)
        gold = r["output"].strip()
        by[r["instruction"]].append((predict_label(o, gold), gold))
    return by


def acc(pairs):
    if not pairs:
        return 0.0
    hit = sum(1 for p, g in pairs if p is not None and p.strip() == g.strip())
    return hit / len(pairs)


def f1_pos(pairs, pos="是"):
    tp = sum(1 for p, g in pairs if p == pos and g == pos)
    fp = sum(1 for p, g in pairs if p == pos and g != pos)
    fn = sum(1 for p, g in pairs if p != pos and g == pos)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def short_task(instr):
    if instr.startswith("识别下面") or "所属的论文章节" in instr:
        return "章节识别 (section)"
    if "核心方法或主要贡献" in instr:
        return "方法句 是/否 (binary)"
    if "候选句" in instr:
        return "核心方法抽取 (pick)"
    return instr[:16] + "…"


def main():
    ap = argparse.ArgumentParser(description="量化评估 PaperCore LoRA")
    ap.add_argument("--adapter", default="outputs/papercore-lora-smoke")
    ap.add_argument("--base", default=None, help="覆盖底模路径（默认读 adapter 的 config）")
    ap.add_argument("--data", default="data/val.jsonl")
    ap.add_argument("--max-new-tokens", type=int, default=24)
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.data, encoding="utf-8")]
    device, dtype = pick_device_dtype()
    base_path = args.base or PeftConfig.from_pretrained(args.adapter).base_model_name_or_path
    print(f"[eval] {len(rows)} 条 | base={os.path.basename(base_path.rstrip('/'))} | device={device}")

    tok = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(base_path, torch_dtype=dtype,
                                                trust_remote_code=True).to(device).eval()
    print("[eval] 跑底模（无 adapter）…")
    base_by = run_over(rows, tok, base, device, args.max_new_tokens)
    print("[eval] 挂上 LoRA，再跑一遍…")
    lora = PeftModel.from_pretrained(base, args.adapter).to(device).eval()
    lora_by = run_over(rows, tok, lora, device, args.max_new_tokens)

    # ---- 汇总成表 ----
    print("\n" + "=" * 62)
    print(f"{'任务':26}{'N':>4}{'底模':>9}{'+LoRA':>9}{'Δ':>8}")
    print("-" * 62)
    all_base, all_lora = [], []
    for instr in base_by:
        bp, lp = base_by[instr], lora_by[instr]
        n = len(bp)
        ba, la = acc(bp), acc(lp)
        # 只有分类任务纳入总体准确率
        if any(g in ("是", "否") or g in SECTION_LABELS for _, g in bp):
            all_base += bp
            all_lora += lp
        print(f"{short_task(instr):26}{n:>4}{ba:>8.0%}{la:>9.0%}{(la-ba):>+8.0%}")
        if any(g in ("是", "否") for _, g in bp):
            _, _, bf = f1_pos(bp)
            _, _, lf = f1_pos(lp)
            print(f"{'  └ 是-F1':26}{'':>4}{bf:>8.2f}{lf:>9.2f}{(lf-bf):>+8.2f}")
    print("-" * 62)
    print(f"{'分类总体准确率':24}{len(all_base):>4}{acc(all_base):>8.0%}{acc(all_lora):>9.0%}"
          f"{(acc(all_lora)-acc(all_base)):>+8.0%}")
    print("=" * 62)
    print("注：val 标签多由规则引擎生成 → 高分=成功模仿规则，非'超过规则'。详见本文件顶部说明。")


if __name__ == "__main__":
    main()
