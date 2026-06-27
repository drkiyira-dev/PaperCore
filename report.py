"""
PaperCore · 结构化报告生成（报告中心 / 批量导出复用）

把一次分析的完整结果 data（即 /api/upload 返回、history 存的那个 dict）
渲染成 Markdown 或纯文本报告。后端统一生成 = 单一事实源，
报告中心、（未来的）结果页导出都可复用，不必在前端各拼一份。
"""

MODE_LABELS = {'structured': '结构化理解', 'quick': '快速摘要', 'defense': '答辩汇报'}
ENGINE_LABELS = {'deepseek': 'DeepSeek（云端）', 'ollama': '本地大模型（Ollama）'}

# AI 结构化字段的中文标签（英文 key → 中文；缺失则原样用 key）
_AI_LABELS = {
    'research_question': '研究问题',
    'method': '方法',
    'methods': '方法',
    'contribution': '创新贡献',
    'contributions': '创新贡献',
    'innovation': '创新点',
    'experiment': '实验',
    'results': '实验结果',
    'conclusion': '结论',
    'limitation': '局限',
    'limitations': '不足与局限',
    'future_work': '未来工作',
    'keywords': '关键词',
    'summary': '摘要',
}


def _ai_value_to_str(v):
    if isinstance(v, list):
        return '；'.join(str(x) for x in v)
    if isinstance(v, dict):
        return '；'.join(f"{k}：{vv}" for k, vv in v.items())
    return str(v)


def build_report(data, fmt='md'):
    """data → 报告字符串。fmt: 'md' | 'txt'。"""
    md = (fmt != 'txt')
    L = []  # 行缓冲

    def h1(t): L.append(f"# {t}" if md else t)
    def h2(t): L.append(("\n## " + t) if md else ("\n【" + t + "】"))
    def line(t=''): L.append(t)
    def bullet(t): L.append(f"- {t}" if md else f"  · {t}")

    filename = data.get('filename') or '未知文件'
    mode = MODE_LABELS.get(data.get('analysis_mode'), data.get('analysis_mode') or '—')
    engine = ENGINE_LABELS.get(data.get('ai_engine_used'), '本地规则 + 算法')

    h1('PaperCore 分析报告')
    line()
    bullet(f"文件：{filename}")
    bullet(f"分析模式：{mode}")
    bullet(f"文本字数：{data.get('text_length', 0)}")
    bullet(f"分析引擎：{engine}")

    # 论文体检报告
    qs = data.get('quality_score') or {}
    if qs:
        h2(f"论文体检报告（本地算法）  总分 {qs.get('total', '—')}/100")
        dims = qs.get('dimensions') or {}
        for key, d in dims.items():
            label = d.get('label', key)
            bullet(f"{label}：{d.get('score', '—')}/{d.get('max', '—')}"
                   + (f" — {d.get('detail')}" if d.get('detail') else ''))
        sugg = qs.get('suggestions') or []
        if sugg:
            line()
            line('改进建议：' if not md else '**改进建议：**')
            for s in sugg:
                bullet(s if isinstance(s, str) else _ai_value_to_str(s))

    # AI 结构化分析
    ai = data.get('ai_result')
    if isinstance(ai, dict) and not ai.get('raw'):
        h2('AI 结构化分析')
        for k, v in ai.items():
            if v in (None, '', [], {}):
                continue
            label = _AI_LABELS.get(k, k)
            line((f"**{label}**：" if md else f"{label}：") + _ai_value_to_str(v))

    # 核心命中片段（按显著度降序）
    matches = data.get('matches') or []
    keeps = [m for m in matches if m.get('action') == 'keep']
    keeps.sort(key=lambda m: m.get('salience', 0), reverse=True)
    if keeps:
        h2('核心命中片段（按显著度）')
        for m in keeps:
            sal = m.get('salience')
            tag = f"[显著度 {round(sal * 100)}%] " if isinstance(sal, (int, float)) else ''
            desc = m.get('description', '')
            snippet = (m.get('snippet') or '').strip()
            if md:
                line(f"\n**{tag}{desc}**")
                line(f"> {snippet}")
            else:
                line(f"{tag}{desc}")
                line(f"  {snippet}")

    line()
    line('---' if md else '——')
    line('由 PaperCore 本地生成 · 数据未上云 · 仅供学术参考')

    return '\n'.join(L) + '\n'
