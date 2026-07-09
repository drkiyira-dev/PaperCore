# PaperCore Finetune · 领域 LoRA 微调

给 PaperCore 做一个**专用小模型**：只学四类抽取式能力——论文**结构识别**、**核心方法提取**、
**实验信息提取**、**结论摘要**，不追求通用聊天。

- 只用**本地准备的 JSONL 数据**训练，不抓取任何闭源模型输出。
- 基于 Hugging Face `transformers` + `peft`(LoRA)，产出一个几十 MB 的 adapter，不改底模权重。
- 底模默认选**小型开源模型**（Qwen2.5-1.5B-Instruct），Mac 本地 / 云端轻量都能跑。

> 这是主项目 PaperCore 下一个**独立子项目**，不依赖、也不改动主 app 代码与其 venv。

---

## 1. 安装

单独建虚拟环境（依赖里有 torch，别和主 app 混）：

```bash
cd papercore-finetune
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 数据格式（JSONL）

每行一条，三个字段：

```json
{"instruction": "识别该段落所属章节，只输出章节名。", "input": "本文提出一种基于频率的跨页去重方法……", "output": "方法"}
```

- `instruction`：任务要求（“做什么”）。
- `input`：论文片段（可为空字符串）。
- `output`：期望答案。
- 直接看 `examples/sample.jsonl`（10 条自撰合成样例，可跑通全流程）。
- 你自己的数据放 `data/train.jsonl`（该目录不进 git，见 `data/README.md`）。

**样本量建议**：先攒 300~1000 条高质量样本试水；四类任务尽量均衡。样本质量 > 数量。

### 2.1 用 PaperCore 自有资产一键生成（合规·推荐）

不想手写？`src/build_dataset.py` 把 PaperCore **自己的资产**转成训练集，每条标签都来自
**合规来源**，绝不蒸馏闭源大模型（拿 GPT/Claude 等的输出当标签通常违反其服务条款）：

| 合规来源 | 内容 | 产出任务 |
| --- | --- | --- |
| 人工金标准 `../eval/gold/*.json` | 人手抄的核心方法 / 贡献句 | 方法句判定(是/否)、核心方法句抽取 |
| 自研规则引擎 `../rules.py`（28 条确定性正则） | keep/review 动作 + 章节归类 | 章节识别、（可选 `--denoise`）降噪判断 |
| 自撰合成 `examples/sample.jsonl` | 手写抽取式样例 | 一句话概括、实验信息 JSON、结论摘要 |

```bash
# 用主项目的 venv 跑（脚本要 import 主项目 rules.py）。仅金标准，快而纯净：
../venv/bin/python src/build_dataset.py
# 额外用真实 PDF 富集（规则引擎弱标注章节/方法句，de-wrap+句末标点过滤碎句）：
../venv/bin/python src/build_dataset.py --papers ../eval/papers --max-papers 30
```

产出 `data/train.jsonl` + `data/val.jsonl` 并打印类别分布。实测一版：约 460 条、5 类任务，
`是/否` 与 `方法·实验·引言·结论` 基本均衡（`data/` 默认不进 git）。

## 3. 训练

先用样例跑通：

```bash
python src/train.py --data examples/sample.jsonl --output outputs/demo --epochs 3
```

换成你自己的数据：

```bash
python src/train.py --data data/train.jsonl --output outputs/papercore-lora
```

常用参数：`--model`（换底模）、`--epochs`、`--lr`、`--batch-size`、`--grad-accum`、
`--max-len`、`--lora-r/--lora-alpha`。设备自动识别：

| 环境 | 说明 |
| --- | --- |
| **Apple 芯片 (MPS)** | 直接跑，用 fp32（稳）。1.5B 可训；内存吃紧就换 `--model Qwen/Qwen2.5-0.5B-Instruct` |
| **CUDA** | 优先 bf16；显存小可加 `--load-4bit`（需 `pip install bitsandbytes`）做 QLoRA |
| **CPU** | 能跑但慢，仅建议 0.5B + 小数据验证流程 |

**底模下载**：`--model Qwen/Qwen2.5-0.5B-Instruct` 首次会从 HuggingFace 拉底模（约 1 GB）。
国内网络若 HF 卡住/超时，用 **ModelScope**（魔搭，同样是 Qwen 官方权重、国内 CDN 快）：

```bash
pip install modelscope
python -c "from modelscope import snapshot_download; print(snapshot_download('Qwen/Qwen2.5-0.5B-Instruct'))"
# 把打印出的本地路径当 --model 传给 train.py（本地目录 = 离线加载，无需再联网）
```

## 4. 推理测试

```bash
# 跑内置的 4 条 PaperCore 冒烟用例
python src/infer.py --adapter outputs/demo

# 自己给一条
python src/infer.py --adapter outputs/demo \
  --instruction "从实验部分提取：数据集、评价指标、主要结果，用 JSON 输出。" \
  --input "We evaluate on 240 PDF papers. Extraction F1 reaches 0.87 ..."
```

底模会从 adapter 的 config 自动读取，无需手填。

## 5. 用到主项目里（可选）

训练出的是 LoRA adapter。要在 PaperCore 里用，两条路：

1. **保持 adapter**：推理时 `base + adapter` 一起加载（`infer.py` 的方式），adapter 小、可热插拔。
2. **合并权重**：`PeftModel.merge_and_unload()` 后 `save_pretrained` 成一个独立模型，
   再用 Ollama / vLLM / llama.cpp 之类本地部署。**合并即分发**，务必先看第 6 节的许可证提醒。

## 6. ⚠️ 许可证 / 版权 / 服务条款风险（务必读）

这块不是形式主义——真出问题是在“对外分发/商用”那一刻，不是“本地自己训”那一刻。

**① 底模许可证（决定你能不能商用/再分发）**
- **Qwen2.5**：多数尺寸（0.5B/1.5B/7B/14B/32B）是 **Apache-2.0**，可商用；但**3B、72B 用的是 Qwen 自家 License**（有额外条款），换这两个尺寸前先读它的 LICENSE。
- **DeepSeek-R1-Distill-Qwen / DeepSeek-Coder**：这类“蒸馏自 Qwen”的模型，**同时受 DeepSeek 协议与其底座 Qwen 协议约束**，要叠加看。DeepSeek 系整体较宽松（偏 MIT/开放），但**以你实际下载那个模型页的 LICENSE 为准**，别凭印象。
- 结论：**微调不改变底模的许可证**。你分发 adapter 或合并后的权重时，仍要遵守底模原始 License（署名、协议随附、商用条款等）。

**② 训练数据的版权**
- 论文原文**受版权保护**。自己在本地用于研究性微调，风险相对可控；但**把含论文全文的数据集或“记住了原文”的模型对外公开/售卖**，可能构成侵权。
- 出版商/数据库（Elsevier、IEEE、Springer、知网等）的**下载条款常禁止批量抓取和二次分发**——即使 PDF 是你合法下载的。
- 落地建议：优先用**开放获取(OA)/CC 许可**的论文；数据里尽量存**抽取式片段+你自撰的标注**，而不是整篇原文；`data/` 已默认不进 git。

**③ 别踩“蒸馏闭源模型”的坑（你已明确不做，这里做红线备注）**
- 用 GPT / Claude / 以及各家**闭源 API 的输出**当训练标签去训一个模型，**通常违反其服务条款**（多数明文禁止“用输出训练竞品模型”）。
- 本项目**只用你本地自撰/人工标注的数据**，不碰这条线。要扩数据也请人工标或用**开源模型**生成再人工校对。

**④ 分发与署名**
- 若合并权重后再发布：附上底模的 LICENSE 与出处、写清“基于 XXX 微调”、保留原始署名/版权声明。
- 你的 LoRA adapter 是你自己的产物，但它**离不开底模**——分发时按底模协议走。

> 一句话：**本地训随便训；一旦要“对外发模型 / 商用 / 上架”，先把底模 LICENSE 和数据来源合规性过一遍。** 拿不准就先只发 adapter + 让用户自行下载底模，把版权/协议的责任留在底模那边。

## 7. 目录

```
papercore-finetune/
├── README.md
├── requirements.txt
├── examples/sample.jsonl     # 自撰合成样例，可直接跑
├── data/                     # 你的私有数据（不进 git）
└── src/
    ├── build_dataset.py      # 金标准+规则引擎 → 合规 JSONL（train/val）
    ├── format_utils.py       # 提示词模板 + tokenizer（训练/推理共用）
    ├── train.py              # 读数据 → LoRA → Trainer → 保存
    └── infer.py              # 加载 base+adapter 推理测试
```
