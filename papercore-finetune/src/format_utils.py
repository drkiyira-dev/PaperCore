"""提示词模板 + tokenizer 处理。

训练脚本(train.py)和推理脚本(infer.py)都从这里取同一套格式，
保证「训练时怎么拼、推理时就怎么拼」，避免两边不一致导致效果虚高/虚低。
"""

# PaperCore 领域系统提示：把模型钉在「抽取式论文分析」这件事上，
# 不做通用闲聊，也不让它编造原文没有的信息。
SYSTEM_PROMPT = (
    "你是 PaperCore 的学术论文分析助手，只负责论文结构识别、核心方法提取、"
    "实验信息提取、结论摘要等抽取式任务。请严格依据给定文本作答，"
    "不要编造原文中不存在的信息，输出尽量简洁、结构化。"
)


def build_messages(instruction, user_input="", system=SYSTEM_PROMPT):
    """把一条样本拼成 chat 消息列表。instruction 是任务要求，input 是论文片段。"""
    user_input = (user_input or "").strip()
    if user_input:
        user_content = f"{instruction.strip()}\n\n{user_input}"
    else:
        user_content = instruction.strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def build_prompt_ids(tokenizer, instruction, user_input=""):
    """只到「该模型开始回答」为止的 token（不含答案），用于训练时给答案打 mask。

    兼容不同 transformers 版本：4.x 的 apply_chat_template(tokenize=True) 直接返回
    List[int]；5.x 起默认返回 BatchEncoding/dict（甚至嵌一层 batch）。统一取成 List[int]。
    """
    messages = build_messages(instruction, user_input)
    out = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True
    )
    if hasattr(out, "input_ids"):          # BatchEncoding
        out = out.input_ids
    elif isinstance(out, dict):            # 普通 dict
        out = out["input_ids"]
    if out and isinstance(out[0], (list, tuple)):   # 去掉 batch 维 [[...]] -> [...]
        out = out[0]
    return list(out)


def tokenize_example(example, tokenizer, max_len=1024):
    """把一条 {instruction,input,output} 变成 {input_ids, labels}。

    关键点：只对「答案」部分计算 loss，提示词部分 label 设为 -100（忽略）。
    这样模型学的是「给定任务+论文片段，如何产出答案」，而不是复述提示词。
    """
    prompt_ids = build_prompt_ids(
        tokenizer, example["instruction"], example.get("input", "")
    )
    answer = (example.get("output") or "").strip()
    answer_ids = tokenizer(answer, add_special_tokens=False)["input_ids"]
    if tokenizer.eos_token_id is not None:
        answer_ids = answer_ids + [tokenizer.eos_token_id]

    input_ids = prompt_ids + answer_ids
    labels = [-100] * len(prompt_ids) + list(answer_ids)

    # 超长就从尾部截断（提示：把 max_len 设大一点，别把答案截没了）
    input_ids = input_ids[:max_len]
    labels = labels[:max_len]
    return {"input_ids": input_ids, "labels": labels}
