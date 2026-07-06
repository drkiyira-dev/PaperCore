"""加载底模 + 训练好的 LoRA adapter，跑 PaperCore 任务推理测试。

用法（在 papercore-finetune/ 目录下）：
    # 用内置的几条 PaperCore 测试样例
    python src/infer.py --adapter outputs/demo

    # 自己给一条
    python src/infer.py --adapter outputs/demo \
        --instruction "识别该段落所属章节，只输出章节名。" \
        --input "本文提出一种基于频率的跨页去重方法……"

底模无需手动指定：会从 adapter 的 config 里自动读出训练时用的 base 模型。
"""
import argparse
import os
import sys

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftConfig, PeftModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from format_utils import build_messages  # noqa: E402


def pick_device_dtype():
    if torch.cuda.is_available():
        return "cuda", (torch.bfloat16 if torch.cuda.is_bf16_supported()
                        else torch.float16)
    if torch.backends.mps.is_available():
        return "mps", torch.float32
    return "cpu", torch.float32


# 内置的 PaperCore 冒烟测试（覆盖四类任务）
DEFAULT_CASES = [
    {"instruction": "识别该段落所属章节，只输出章节名（摘要/引言/相关工作/方法/实验/结论 之一）。",
     "input": "本文提出一种基于出现频率的行级去重模块，在文本抽取前剔除页眉、页脚与水印。"},
    {"instruction": "用一句话概括这段论文的核心方法。",
     "input": "评分模块把规则关键词词库与 MiniLM 语义相似度结合，并用饱和曲线对分数做校准。"},
    {"instruction": "从实验部分提取：数据集、评价指标、主要结果，用 JSON 输出。",
     "input": "We evaluate on 240 PDF papers. Extraction F1 reaches 0.87, a 6-point gain over the pdfplumber baseline."},
    {"instruction": "总结这篇论文的结论，不超过两句话。",
     "input": "In this paper we presented a local-first paper analysis pipeline; future work will extend to formula and figure understanding."},
]


def load(adapter, base_override=None):
    device, dtype = pick_device_dtype()
    peft_cfg = PeftConfig.from_pretrained(adapter)
    base = base_override or peft_cfg.base_model_name_or_path
    print(f"[load] base={base} + adapter={adapter} on {device}")

    tokenizer = AutoTokenizer.from_pretrained(adapter, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        base, torch_dtype=dtype, trust_remote_code=True
    )
    model = PeftModel.from_pretrained(model, adapter)
    model.to(device).eval()
    return tokenizer, model, device


@torch.no_grad()
def generate(tokenizer, model, device, instruction, user_input="", max_new_tokens=512):
    messages = build_messages(instruction, user_input)
    prompt = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=False
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    out = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,               # 抽取式任务用贪心，结果稳定可复现
        repetition_penalty=1.05,
        pad_token_id=tokenizer.eos_token_id,
    )
    gen = out[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(gen, skip_special_tokens=True).strip()


def main():
    p = argparse.ArgumentParser(description="PaperCore LoRA 推理测试")
    p.add_argument("--adapter", default="outputs/papercore-lora")
    p.add_argument("--base", default=None, help="覆盖底模（一般不用填）")
    p.add_argument("--instruction", default=None)
    p.add_argument("--input", default="")
    p.add_argument("--max-new-tokens", type=int, default=512)
    args = p.parse_args()

    tokenizer, model, device = load(args.adapter, args.base)

    if args.instruction:
        cases = [{"instruction": args.instruction, "input": args.input}]
    else:
        cases = DEFAULT_CASES

    for i, c in enumerate(cases, 1):
        ans = generate(tokenizer, model, device, c["instruction"], c["input"],
                       args.max_new_tokens)
        print(f"\n===== 用例 {i} =====")
        print(f"[任务] {c['instruction']}")
        if c["input"]:
            print(f"[输入] {c['input']}")
        print(f"[输出] {ans}")


if __name__ == "__main__":
    main()
