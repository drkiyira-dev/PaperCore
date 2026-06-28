# ai_engines.py

import os
import requests
import re
import jieba
import jieba.analyse

# deepseek-chat 这个别名 2026-07-24 起官方停用（迁移到显式模型名），默认改用 deepseek-v4-flash。
# 仍可用 DEEPSEEK_MODEL 环境变量覆盖。
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# ==================== 1. 关键词映射字典（Gemini 设计） ====================
cs_keyword_map = {
    # 类别A：系统与性能描述
    "很快": ["低延迟", "高吞吐量", "时间复杂度优化"],
    "省空间": ["内存优化", "空间复杂度低", "轻量级"],
    "很好用": ["高可用性", "用户友好"],
    "很稳定": ["高鲁棒性", "容错率高"],
    "算得很准": ["高精度", "召回率高"],

    # 类别B：动作与实现
    "用": ["采用", "利用", "部署"],
    "做": ["实现", "构建", "提出"],
    "看": ["分析", "评估", "观测"],
    "找": ["检索", "遍历", "挖掘"],

    # 类别C：逻辑关系与连接词
    "因为": ["鉴于", "归因于"],
    "所以": ["因此", "进而"],
    "然后": ["随后", "进一步地"],

    # 类别D：避免主观色彩
    "我们觉得": ["实验表明", "数据显示"],
    "我证明了": ["本文验证了", "结果证实了"]
}

def generate_academic_prompt(user_text):
    """
    根据用户文本生成定制化的润色提示词（用于 AI 模型）
    返回一个包含具体修改建议的 prompt 字符串
    """
    found_issues = []
    # 扫描文本中的口语化词汇（使用正则匹配完整单词，避免子串误判）
    for weak_word, strong_alternatives in cs_keyword_map.items():
        # 用正则 \b 匹配单词边界（中文没有明确边界，但可简单处理）
        # 为了更准确，这里使用直接字符串包含（因为弱词通常是一个完整词）
        if weak_word in user_text:
            alt_str = "、".join(strong_alternatives)
            found_issues.append(f"将「{weak_word}」替换为更专业的工程术语（如：{alt_str}）")

    # 构建系统提示词
    system_prompt = (
        "你是一个顶级的计算机科学学术编辑。请重写下以下段落，使其符合 IEEE 或 ACM 的学术论文标准。\n"
        "要求：\n"
        "1. 保持原有的技术逻辑不变。\n"
        "2. 消除主观语气，使用客观描述。\n"
        "3. 增强代码、算法和系统架构的专业表达。\n"
    )
    if found_issues:
        system_prompt += "4. 特别注意以下修改建议：\n"
        for issue in found_issues:
            system_prompt += f"   - {issue}\n"

    system_prompt += f"\n[原始文本]:\n{user_text}\n\n[重写后的学术文本]:"
    return system_prompt


# ==================== 2. 自己设计的轻量AI（基于规则和统计） ====================
class SimpleAI:
    @staticmethod
    def polish(text):
        """基于规则直接替换（作为简易润色）"""
        result = text
        for weak, strong_list in cs_keyword_map.items():
            if weak in result:
                # 简单替换为第一个专业术语
                result = result.replace(weak, strong_list[0])
        # 还可以增加其他规则...
        return result

    @staticmethod
    def summarize(text, max_sentences=3):
        sentences = re.split(r'(?<=[。！？])\s*', text)
        if len(sentences) <= max_sentences:
            return text
        word_freq = {}
        for sent in sentences:
            for w in jieba.lcut(sent):
                if len(w) > 1 and w not in ['的', '了', '在', '是', '我', '也', '就', '不', '人', '有']:
                    word_freq[w] = word_freq.get(w, 0) + 1
        sent_scores = [sum(word_freq.get(w, 0) for w in jieba.lcut(sent)) for sent in sentences]
        top_indices = sorted(range(len(sent_scores)), key=lambda i: sent_scores[i], reverse=True)[:max_sentences]
        top_indices.sort()
        return ''.join([sentences[i] for i in top_indices])

    @staticmethod
    def extract_keywords(text, top_k=5):
        return ', '.join(jieba.analyse.extract_tags(text, topK=top_k))


# ==================== 3. 本地开源小模型（使用 T5 中文） ====================
class LocalModelAI:
    _model = None
    _tokenizer = None
    _model_name = "uer/t5-base-chinese"

    @classmethod
    def _load_model(cls):
        if cls._model is None:
            from transformers import T5Tokenizer, T5ForConditionalGeneration
            print(f"正在加载中文模型 {cls._model_name}，首次会下载...")
            cls._tokenizer = T5Tokenizer.from_pretrained(cls._model_name)
            cls._model = T5ForConditionalGeneration.from_pretrained(cls._model_name)
        return cls._tokenizer, cls._model

    @classmethod
    def _generate(cls, prompt, max_length=200):
        tokenizer, model = cls._load_model()
        inputs = tokenizer(prompt, return_tensors="pt", padding=True, truncation=True, max_length=512)
        outputs = model.generate(
            inputs.input_ids,
            max_length=max_length,
            num_beams=4,
            early_stopping=True,
            temperature=0.7,
            do_sample=True
        )
        return tokenizer.decode(outputs[0], skip_special_tokens=True)

    @classmethod
    def polish(cls, text):
        # 使用 Gemini 的提示词优化功能
        prompt = generate_academic_prompt(text)
        # 由于 T5 是 encoder-decoder，直接将生成的 prompt 作为输入
        return cls._generate(prompt, max_length=len(text) + 200)

    @classmethod
    def summarize(cls, text):
        prompt = f"摘要：{text}"
        return cls._generate(prompt, max_length=150)

    @classmethod
    def extract_keywords(cls, text):
        prompt = f"关键词：{text}"
        return cls._generate(prompt, max_length=30)


# ==================== 4. 云端 API（扩展版） ====================
class CloudAPI:
    PLATFORMS = {
        "doubao": {
            "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            # 豆包 / 火山方舟的 endpoint ID 由每个账号自建，须在 .env 配置 DOUBAO_ENDPOINT_ID
            "model": os.environ.get("DOUBAO_ENDPOINT_ID", ""),
            "headers_template": {"Authorization": "Bearer {api_key}"}
        },
        "tongyi": {
            "url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
            "model": "qwen-turbo",
            "headers_template": {"Authorization": "Bearer {api_key}"}
        },
        "deepseek": {
            "url": "https://api.deepseek.com/chat/completions",
            "model": DEEPSEEK_MODEL,
            "headers_template": {"Authorization": "Bearer {api_key}"}
        },
    }

    @staticmethod
    def call(platform, api_key, prompt, return_usage=False):
        """调用云端 chat API。

        return_usage=True 时返回 (ok, result, usage)，usage 为 OpenAI/DeepSeek
        兼容响应里的 {prompt_tokens, completion_tokens, ...}（供体验区成本埋点）；
        默认 False 返回 (ok, result)，与原签名兼容，老调用方无需改动。
        """
        def _ret(ok, result, usage=None):
            return (ok, result, usage) if return_usage else (ok, result)

        if platform not in CloudAPI.PLATFORMS:
            return _ret(False, "不支持的平台")
        cfg = CloudAPI.PLATFORMS[platform]
        headers = cfg["headers_template"].copy()
        headers["Authorization"] = headers["Authorization"].format(api_key=api_key)
        headers["Content-Type"] = "application/json"

        if platform == "tongyi":
            payload = {
                "model": cfg["model"],
                "input": {"messages": [{"role": "user", "content": prompt}]},
                "parameters": {"result_format": "message"}
            }
        else:
            payload = {
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 1500
            }
        timeout = 90 if platform == "deepseek" else 30
        try:
            resp = requests.post(cfg["url"], headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if platform == "tongyi":
                result = data["output"]["choices"][0]["message"]["content"]
            else:
                result = data["choices"][0]["message"]["content"]
            return _ret(True, result, data.get("usage"))
        except Exception as e:
            return _ret(False, f"调用失败：{str(e)}")

    @classmethod
    def polish(cls, api_key, platform, text):
        # 使用 Gemini 的提示词优化功能
        prompt = generate_academic_prompt(text)
        return cls.call(platform, api_key, prompt)

    @classmethod
    def summarize(cls, api_key, platform, text):
        prompt = f"请为以下文本撰写一份200字以内的摘要，突出核心内容：\n{text}"
        return cls.call(platform, api_key, prompt)

    @classmethod
    def extract_keywords(cls, api_key, platform, text):
        prompt = f"请从以下文本中提取5个关键词，用逗号分隔：\n{text}"
        return cls.call(platform, api_key, prompt)


# ==================== 4.4 v4pro 高级模式（产品线包装层） ====================
class V4ProAPI:
    """
    v4pro 高级分析模式：**真正调用更高一档的 deepseek-v4-pro 模型**（普通档走
    deepseek-chat，服务端=deepseek-v4-flash；二者是不同模型，DeepSeek 后台可区分），
    并叠加差异化的「资深学术评审」system prompt + 更大的 max_tokens。

    重要：MODEL 必须显式写 deepseek-v4-pro，不能用 DEEPSEEK_MODEL（那是 flash 别名）——
    否则 v4pro 名不副实、监控里只见 flash。

    配额管理：见 usage.py（5 小时滚动窗口最多 5 次）。本类只负责调用，
    配额校验与记账由 app.py 主流程统一处理（调前 check_quota、调后 record_use）。
    """

    # 高级模型 ID：deepseek-v4-pro（区别于普通档的 deepseek-chat=v4-flash）。可用环境变量覆盖。
    MODEL = os.environ.get("V4PRO_MODEL", "deepseek-v4-pro")

    SYSTEM_PROMPT = (
        "你是一位资深学术评审专家，长期为顶会顶刊审稿。请用「资深评审视角」"
        "对论文做深度结构化分析：每一项不要泛泛而谈，要点出具体证据、潜在风险与改进建议。"
        "保持中立、严谨、可证伪。"
    )

    MAX_TOKENS = 3200  # 普通 deepseek 默认 1500；高级版给更深更长的输出

    @staticmethod
    def is_available():
        """是否具备调用条件：环境里有 DEEPSEEK_API_KEY 即可。"""
        key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
        return key.startswith('sk-') and len(key) > 20

    @staticmethod
    def call(prompt, timeout=120):
        """调用 deepseek-v4-pro，附加资深评审 system prompt + 更大 max_tokens。
        返回 (ok, text_or_err)，与 CloudAPI.call 同构。
        """
        key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
        if not key:
            return False, "未配置 DEEPSEEK_API_KEY"
        url = "https://api.deepseek.com/chat/completions"
        payload = {
            "model": V4ProAPI.MODEL,
            "messages": [
                {"role": "system", "content": V4ProAPI.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": V4ProAPI.MAX_TOKENS,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            return True, resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return False, f"v4pro 调用失败：{e}"


# ==================== 4.5 本地大模型（Ollama） ====================
class OllamaAPI:
    """
    本地大模型引擎：通过 Ollama 在本机运行大模型（默认 deepseek-r1:7b）。
    与 CloudAPI 同构（走 OpenAI 兼容的 /v1/chat/completions），但全程本机推理、
    不出网——是 local-first 叙事下「不依赖云端 Key 也能做深度分析」的真正落地，
    把可插拔引擎从「纯规则 / 本地 T5 / 云端 API」补齐到「本地大模型」这一档。

    deepseek-r1 是推理模型，输出可能带 <think>...</think> 思考块，这里统一剥离，
    只把最终回答交给上层。
    """
    BASE_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:7b")

    @staticmethod
    def is_available(timeout=1.5):
        """快速探测本机 Ollama 服务是否在跑（不阻塞正常流程）。"""
        try:
            r = requests.get(f"{OllamaAPI.BASE_URL}/api/tags", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _strip_think(text):
        """剥离推理模型的 <think> 思考块，只保留最终答案。"""
        if '</think>' in text:
            # 取最后一个 </think> 之后的内容（兼容开头标签缺失的情况）
            text = text.rsplit('</think>', 1)[-1]
        # 再保险：去掉任何残留的成对 <think>...</think>
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        return text.strip()

    @staticmethod
    def call(prompt, model=None, timeout=300, num_ctx=8192):
        # 用 Ollama 原生 /api/chat（而非 OpenAI 兼容端点）：它支持 options.num_ctx，
        # 可把上下文窗口调大。论文分析提示词约 4000+ token，超过默认 4096 会 400，
        # 故显式设 num_ctx=8192 容纳长输入 + 推理输出。
        url = f"{OllamaAPI.BASE_URL}/api/chat"
        payload = {
            "model": model or OllamaAPI.MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_ctx": num_ctx, "temperature": 0.3},
        }
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["message"]["content"]
            return True, OllamaAPI._strip_think(content)
        except Exception as e:
            return False, f"Ollama 调用失败：{str(e)}"

    @classmethod
    def polish(cls, text):
        return cls.call(generate_academic_prompt(text))

    @classmethod
    def summarize(cls, text):
        return cls.call(f"请为以下文本撰写一份200字以内的摘要，突出核心内容：\n{text}")

    @classmethod
    def extract_keywords(cls, text):
        return cls.call(f"请从以下文本中提取5个关键词，用逗号分隔：\n{text}")


# ==================== 5. 统一接口 ====================
class AIEngine:
    @staticmethod
    def process(engine_type, func, **kwargs):
        if engine_type == 'simple':
            if func == 'polish':
                return True, SimpleAI.polish(kwargs['text'])
            elif func == 'summarize':
                return True, SimpleAI.summarize(kwargs['text'])
            elif func == 'keywords':
                return True, SimpleAI.extract_keywords(kwargs['text'])
            else:
                return False, "不支持的功能"
        elif engine_type == 'local':
            if func == 'polish':
                return True, LocalModelAI.polish(kwargs['text'])
            elif func == 'summarize':
                return True, LocalModelAI.summarize(kwargs['text'])
            elif func == 'keywords':
                return True, LocalModelAI.extract_keywords(kwargs['text'])
            else:
                return False, "不支持的功能"
        elif engine_type == 'cloud':
            platform = kwargs.get('platform')
            api_key = kwargs.get('api_key')
            if not platform or not api_key:
                return False, "缺少云端平台或API密钥"
            if func == 'polish':
                return CloudAPI.polish(api_key, platform, kwargs['text'])
            elif func == 'summarize':
                return CloudAPI.summarize(api_key, platform, kwargs['text'])
            elif func == 'keywords':
                return CloudAPI.extract_keywords(api_key, platform, kwargs['text'])
            else:
                return False, "不支持的功能"
        elif engine_type == 'ollama':
            if func == 'polish':
                return OllamaAPI.polish(kwargs['text'])
            elif func == 'summarize':
                return OllamaAPI.summarize(kwargs['text'])
            elif func == 'keywords':
                return OllamaAPI.extract_keywords(kwargs['text'])
            else:
                return False, "不支持的功能"
        else:
            return False, "未知的引擎类型"