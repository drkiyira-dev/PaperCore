"""PaperCore 领域 LoRA 微调（SFT）。

只用本地 JSONL 数据，对一个小型开源模型（默认 Qwen2.5-1.5B-Instruct）做 LoRA 微调，
产出一个体积很小的 adapter（几十 MB），不动底模权重。

用法（在 papercore-finetune/ 目录下）：
    # 先跑通样例
    python src/train.py --data examples/sample.jsonl --output outputs/demo --epochs 3
    # 换成你自己的数据
    python src/train.py --data data/train.jsonl --output outputs/papercore-lora

自动适配设备：CUDA(可选 4bit) / Apple 芯片 MPS / CPU。
"""
import argparse
import os
import sys

import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    Trainer,
    TrainingArguments,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from format_utils import tokenize_example  # noqa: E402


def pick_device_dtype():
    """选设备和精度。Mac(MPS) 用 fp32 更稳；CUDA 优先 bf16。"""
    if torch.cuda.is_available():
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        return "cuda", dtype
    if torch.backends.mps.is_available():
        return "mps", torch.float32
    return "cpu", torch.float32


def parse_args():
    p = argparse.ArgumentParser(description="PaperCore 领域 LoRA 微调")
    p.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct",
                   help="底模。Mac 内存小可换 Qwen/Qwen2.5-0.5B-Instruct")
    p.add_argument("--data", default="examples/sample.jsonl", help="训练用 JSONL")
    p.add_argument("--output", default="outputs/papercore-lora", help="adapter 输出目录")
    p.add_argument("--epochs", type=float, default=3.0)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--max-len", type=int, default=1024)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--grad-ckpt", action="store_true",
                   help="开梯度检查点省显存（大模型/CUDA 用；小模型可不开）")
    p.add_argument("--load-4bit", action="store_true",
                   help="QLoRA 4bit 量化，仅 CUDA + bitsandbytes 有效")
    return p.parse_args()


def main():
    args = parse_args()
    device, dtype = pick_device_dtype()
    print(f"[device] {device} / dtype={dtype}")

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # ---- 加载底模（可选 4bit）----
    model_kwargs = {"torch_dtype": dtype, "trust_remote_code": True}
    use_4bit = args.load_4bit and device == "cuda"
    if use_4bit:
        from transformers import BitsAndBytesConfig
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype,
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(args.model, **model_kwargs)

    # ---- LoRA 配置 ----
    if use_4bit:
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=args.grad_ckpt
        )
    lora_cfg = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        # Qwen / Llama 系的注意力+MLP 投影层
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    if args.grad_ckpt and not use_4bit:
        # PEFT + 梯度检查点时，必须让输入 embedding 参与梯度
        model.enable_input_require_grads()
    if args.grad_ckpt:
        model.config.use_cache = False

    # ---- 读取并 tokenize 数据 ----
    ds = load_dataset("json", data_files=args.data, split="train")
    print(f"[data] {len(ds)} 条样本，来自 {args.data}")
    ds = ds.map(
        lambda ex: tokenize_example(ex, tokenizer, args.max_len),
        remove_columns=ds.column_names,
    )

    collator = DataCollatorForSeq2Seq(
        tokenizer, label_pad_token_id=-100, padding="longest"
    )

    targs = TrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=5,
        save_strategy="epoch",
        save_total_limit=2,
        gradient_checkpointing=args.grad_ckpt,
        # 只有 CUDA 才开混合精度；MPS 上 fp16 容易 NaN，一律 fp32
        bf16=(device == "cuda" and dtype == torch.bfloat16),
        fp16=(device == "cuda" and dtype == torch.float16),
        optim="adamw_torch",
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=ds,
        data_collator=collator,
    )
    trainer.train()

    # ---- 保存 adapter + tokenizer ----
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"[done] LoRA adapter 已保存到 {args.output}")


if __name__ == "__main__":
    main()
