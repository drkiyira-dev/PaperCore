# rules.py · 工科论文规则词库（中英双语）
import re
from salience import doc_tfidf_weights, compute_salience

# 设计约定
# --------
# 1) 中英双语：每条 pattern 的触发词同时含中文与英文学术高频表达；匹配统一走
#    re.IGNORECASE（见 match_rules），故英文不区分大小写（We Propose / we propose 均可）。
# 2) 片段收尾：英文学术句多以「.」结尾，但「.」也出现在小数(0.412)、缩写(et al.)里，
#    拿它当硬边界会把英文片段切碎；故片段以「中文标点 ；。！？ / 换行」收尾。
#    keep 尾部用 {0,}（而非 {3,}）：避免触发短语落在句末时整句失配（"由…组成。"）。
# 3) 三个字段决定一条命中怎么呈现：
#       action="keep"   —— 核心内容，显著度高、优先保留
#       action="review" —— 套话冗余，显著度低、建议删/精简
#       note            —— 给用户看的「人话说明」：keep 说这是什么核心信息；
#                          review 说为什么是水、建议怎么处理。前端卡片直接展示。
# 4) 面向工科论文横向铺开，覆盖「问题→定义→方法→架构→算法→创新→实验→结果→指标→
#    图表→公式→理论→实现→结论→局限」的完整叙事链，兼顾 CS/AI、电子通信、机械、材料、
#    控制等方向的通用结构性表达（强信号短语，而非堆砌纯术语，避免误把普通句当核心）。

RULES = [
    # ==================== 一、核心内容（action=keep）====================

    {
        "name": "problem_motivation",
        "description": "研究问题 / 动机 / 目标",
        "note": "点明论文要解决的问题，是全文的出发点，删了就看不懂为什么做。",
        "pattern": r"(针对[^\n，。；]{0,20}?(问题|挑战|不足|局限|难题|现象)|为(了)?(解决|应对|克服|缓解)|旨在|本文(主要)?(研究|关注)|研究目标|存在[^\n，。；]{0,15}?(问题|不足|缺陷)|难以[^\n，。；]{0,12}?(满足|实现)|to address|to solve|to tackle|aim(s|ed)? (to|at)|the problem of|motivated by|in order to|to overcome|to deal with|suffer(s|ed)? from)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.8,
        "action": "keep",
    },
    {
        "name": "definition_notation",
        "description": "定义 / 符号约定",
        "note": "形式化定义或符号约定，是读懂后文公式与方法的前提。",
        "pattern": r"(定义为|记为|表示为|令[^\n，。；]{0,8}?[为表]|设[^\n，。；]{0,8}?为|其中[^\n，。；]{0,12}?表示|定义[^\n，。；]{0,8}?如下|denote(s|d)?(\s+by)?|is defined as|we define|where[^\n，。；]{0,15}?denotes?)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.72,
        "action": "keep",
    },
    {
        "name": "method_extract",
        "description": "方法 / 技术路线",
        "note": "核心方法 / 技术路线所在，论文最该保留的部分之一。",
        "pattern": r"(本文方法|所提方法|提出了?|设计了?|构建了?|建立了?|采用了?|引入了?|利用[^\n，。；]{0,12}?(方法|模型|机制|策略)|基于[^\n，。；]{0,20}?(方法|模型|框架|算法|网络|策略|机制)|通过[^\n，。；]{0,20}?(实现|完成|得到)|the proposed method|our (method|approach|scheme)|we (design|develop|build|introduce|adopt|employ|utilize|propose)|is proposed|method(s)? based on|based on[^\n，。；]{0,20}?(method|model|framework|network))[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.78,
        "action": "keep",
    },
    {
        "name": "system_architecture",
        "description": "系统架构 / 框架 / 模块组成",
        "note": "系统或模型的整体结构，理解全文的骨架。",
        "pattern": r"(系统(架构|结构|框架)|整体(架构|框架|流程|结构)|总体设计|网络结构|由[^\n，。；]{0,30}?(组成|构成)|包含[^\n，。；]{0,30}?(模块|部分|单元|层)|分为[^\n，。；]{0,20}?(模块|部分|阶段)|the (overall|proposed|whole) (architecture|framework|system|pipeline|network)|consists? of|is composed of|the framework (of|consists)|our (system|framework|pipeline)|the architecture of)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.75,
        "action": "keep",
    },
    {
        "name": "algorithm_extract",
        "description": "算法 / 流程 / 优化目标",
        "note": "算法流程或优化目标，方法可复现的关键细节。",
        "pattern": r"(算法[\s\d]|算法(流程|步骤|描述)|伪代码|时间复杂度|空间复杂度|计算复杂度|迭代(过程|更新|求解)|优化(目标|问题|算法)|损失函数|目标函数|梯度(下降|更新)|收敛性?|Algorithm\s*\d|the algorithm|time complexity|space complexity|loss function|objective function|optimization (problem|objective)|we optimize|gradient descent|converge(s|nce)?)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.78,
        "action": "keep",
    },
    {
        "name": "innovation_extract",
        "description": "创新点 / 主要贡献",
        "note": "作者自述的创新与贡献，答辩、审稿最关注的地方。",
        "pattern": r"(主要贡献|核心创新|创新点|本文工作|我们提出|主要工作|首次(提出|实现|引入)|与[^\n，。；]{0,15}?不同|不同于[^\n，。；]{0,20}?(现有|已有|传统)|区别于|main contribution|key contribution|our contribution|the contributions of|key innovation|we propose a novel|novel(ty)?|for the first time|unlike (existing|previous|prior|conventional))[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.82,
        "action": "keep",
    },
    {
        "name": "experiment_setup",
        "description": "实验设置 / 数据集 / 评价指标",
        "note": "实验怎么做的（数据集 / 指标 / 设置），决定结果可不可信。",
        "pattern": r"(实验(设置|环境|配置|平台|条件|参数)|数据集|训练集|测试集|验证集|样本(数|量|集)|消融(实验|研究)|对比实验|评价指标|评估指标|基准(方法|模型)|in (our|the) experiments?|the dataset|training set|test(ing)? set|validation set|evaluation metric|ablation (study|experiment)|experimental setup|we (conduct|perform|carry out)[^\n，。；]{0,20}?experiment|benchmark|baseline(s)?)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.78,
        "action": "keep",
    },
    {
        "name": "experiment_result",
        "description": "实验结果 / 评测数据",
        "note": "实验得到的关键结果与数据，论文的事实依据。",
        "pattern": r"(实验结果(表明|显示|证明|说明)|结果(表明|显示|证明|说明)|实验表明|测试结果|准确率|精确率|精度|召回率|查准率|查全率|均方误差|误差率?|结果显示|experimental results (show|demonstrate|indicate|reveal)|results (show|demonstrate|indicate|reveal)|achieve(s|d)?|accuracy|precision|recall|F1[\s-]?score|RMSE|the results)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.82,
        "action": "keep",
    },
    {
        "name": "performance_gain",
        "description": "性能对比 / 数值提升（强信号）",
        "note": "量化的性能提升 / 对比优势，最硬的证据，务必保留。",
        "pattern": r"((提升|提高|增加|改善|降低|减少|缩短|节省)了?\s*\d+(\.\d+)?\s*(%|个百分点|倍|dB)|相比[^\n，。；]{0,25}?(提升|提高|优于|超过|降低)|优于|超过[^\n，。；]{0,10}?(现有|基线|对比)|领先|达到[^\n，。；]{0,15}?(最优|最高|state)|(improve|increase|reduce|decrease|boost|gain)(s|d|es)?\s+(by\s+)?\d+(\.\d+)?\s*%|outperform(s|ed)?|surpass(es|ed)?|superior to|state-of-the-art|SOTA|the best (performance|result|accuracy))[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.85,
        "action": "keep",
    },
    {
        "name": "metric_indicator",
        "description": "性能指标 / 工科度量",
        "note": "吞吐、延迟、能耗等工科度量的实测值（须带数字，光提指标名不计入）。",
        "pattern": r"(吞吐量|吞吐率|延迟|时延|能耗|功耗|信噪比|误码率|帧率|带宽|响应时间|资源利用率|内存占用|运行时间|throughput|latency|energy consumption|power consumption|signal[- ]to[- ]noise|bit error rate|frame rate|bandwidth|response time|memory (usage|footprint)|runtime)[^\n。！？；]{0,18}?\d[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.74,
        "action": "keep",
    },
    {
        "name": "figure_table_ref",
        "description": "图表引用（指向关键数据）",
        "note": "指向图 / 表的句子，通常承载关键数据或结论，是结果的索引。",
        "pattern": r"(如(图|表)\s*\d|(图|表)\s*\d[^\n，。；]{0,4}?(所示|显示|表明|给出|列出)|见(图|表)\s*\d|(Fig(ure)?|Table)\.?\s*\d[^\n，。；]{0,4}?(shows?|illustrates?|presents?|depicts?|reports?|lists?|summarizes?))[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.73,
        "action": "keep",
    },
    {
        "name": "formula_extract",
        "description": "公式（LaTeX / 编号引用）",
        "note": "公式 / 数学表达，方法的形式化核心。",
        # 行内 $...$ 要求内部含数学特征(\ ^ _ =)，避免把「$3 per hour」这类货币金额误当公式。
        "pattern": r"\$[^$\n]*[\\^_=][^$\n]*\$|\\begin\{equation\}.*?\\end\{equation\}|\\begin\{align\}.*?\\end\{align\}|公式\s*\(?\d+\)?|式\s*\(\d+\)|Eq(uation)?\.?\s*\(?\d+\)?",
        "min_confidence": 0.88,
        "action": "keep",
    },
    {
        "name": "theorem_proof",
        "description": "定理 / 引理 / 证明 / 假设",
        "note": "理论结果（定理 / 证明 / 假设），论文的理论支撑。",
        "pattern": r"(定理\s*\d|引理\s*\d|推论\s*\d|命题\s*\d|证明[:：]|假设[\s\d]|前提条件|约束条件|满足[^\n，。；]{0,15}?条件|Theorem\s*\d|Lemma\s*\d|Corollary\s*\d|Proposition\s*\d|we (prove|assume)|assume(s|d)? that|subject to|the proof of|it follows that)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.8,
        "action": "keep",
    },
    {
        "name": "implementation",
        "description": "实现 / 运行环境",
        "note": "实现与运行环境（平台 / 硬件 / 框架），关乎可复现性。",
        "pattern": r"(基于[^\n，。；]{0,15}?(平台|框架)实现|在[^\n，。；]{0,15}?(平台|环境|GPU|CPU|FPGA|服务器|集群)上(实现|运行|部署|训练|测试)|使用[^\n，。；]{0,12}?(框架|工具|库)实现|代码(基于|使用)|implemented (in|on|using|with)|is implemented|we implement|run(s)? on[^\n，。；]{0,15}?(GPU|CPU|FPGA|platform|server|cluster)|using (PyTorch|TensorFlow|Keras|CUDA))[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.7,
        "action": "keep",
    },
    {
        "name": "conclusion_extract",
        "description": "结论性语句",
        "note": "结论性论断，全文要点的收口。",
        "pattern": r"(结论|综上所述|综上|总的来说|本文(提出|设计|采用|实现|研究|构建)|本研究|研究表明|可以得出|得出结论|实验(充分)?证明|in conclusion|to conclude|we conclude|in summary|to summarize|this paper (proposes|presents|introduces|develops)|overall,|our (work|study) (shows|demonstrates))[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.8,
        "action": "keep",
    },
    {
        "name": "limitation_future",
        "description": "局限 / 未来工作",
        "note": "作者承认的局限与后续方向，体现研究的严谨与边界。",
        "pattern": r"(局限性?|不足之处|有待(改进|提高|完善)|未来(工作|研究|方向)|进一步(研究|工作|改进|探索)|下一步(工作)?|尚(存在|需|未)|仍然?(存在|面临)|limitation(s)?|future work|in (the )?future|further (research|work|study|investigation)|remains? to be|leave[^\n，。；]{0,15}?future work)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.7,
        "action": "keep",
    },

    # ==================== 二、冗余 / 套话（action=review）====================

    {
        "name": "generic_background",
        "description": "通用背景铺垫",
        "note": "开头的背景铺垫，与核心结论关系弱。这类「近年来 / 随着…发展」的套话通常可删，或压成一句。",
        "pattern": r"(随着[^\n。！？]{0,30}?的(发展|进步|普及|提高)|近年来|当今(社会|时代)|众所周知|如今|长期以来|在[^\n。！？]{0,15}?背景下|in recent years|with the (development|growth|advancement|progress)|recently,|it is well known|nowadays|over the past)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.45,
        "action": "review",
    },
    {
        "name": "importance_cliche",
        "description": "重要性 / 广泛应用套话",
        "note": "「具有重要意义 / 广泛应用」是泛化论断、缺具体支撑，审稿常视为注水。建议删，或换成具体数据。",
        "pattern": r"(具有(重要|重大)[^\n。！？]{0,10}?(意义|价值|作用)|得到了?广泛(的)?(应用|关注|研究)|受到[^\n。！？]{0,10}?(广泛)?关注|发挥[^\n。！？]{0,8}?重要作用|扮演[^\n。！？]{0,8}?重要角色|至关重要|不可或缺|越来越[^\n。！？]{0,8}?(重要|受到)|play(s|ed)? an? (important|crucial|key|vital|significant) role|is of great (importance|significance)|has attracted[^\n。！？]{0,15}?attention|widely (used|applied|adopted|studied)|an important role)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.4,
        "action": "review",
    },
    {
        "name": "common_definition",
        "description": "常识性泛定义",
        "note": "对常识概念的泛泛解释（「所谓…是指」「通常指…」），多为铺垫，对核心贡献无增量，可删。",
        "pattern": r"(所谓[^\n。！？]{0,10}?(是指|指的是|就是)|通常(是指|指的是)|一般(是指|指的是)|可以(理解为|看作是?)|是指[^\n。！？]{0,4}?一(种|类)|refer(s|red)? to as|is a (kind|type) of|commonly (known|referred to) as)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.4,
        "action": "review",
    },
    {
        "name": "redundant_transition",
        "description": "冗余过渡 / 章节预告",
        "note": "纯过渡或章节预告（「首先 / 其次」「本文组织如下」），信息量低，精简后不影响阅读。",
        "pattern": r"(首先|其次|再次|然后|最后|总而言之|一方面|另一方面|本文(的)?(组织|结构)(如下|安排)|本文(余下|剩余|其余)部分|本章(主要)?(介绍|讨论|阐述)|firstly|secondly|thirdly|finally,|on the one hand|on the other hand|the (rest|remainder) of this paper|is organized as follows|the structure of this paper|the remainder)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.4,
        "action": "review",
    },
    {
        "name": "filler_hedge",
        "description": "填充词 / 模糊限定",
        "note": "「值得注意的是 / 在一定程度上」等填充与模糊限定词，删掉不损信息，句子更紧。",
        "pattern": r"(值得(注意|一提|指出)的是|需要(注意|指出|说明)的是|不难(发现|看出|得到)|显而易见|在一定程度上|某种程度上|相对而言|总体而言|一般来说|it (is|should be) (worth )?(noting|noted|mentioned|pointed out|emphasized)|it is worth (noting|mentioning)|it (can|could|may) be (seen|observed|shown|noted|verified|found|concluded)|we (can|could) (see|observe|note|find|conclude)|note that|notice that|as (mentioned|shown|discussed|noted|described|illustrated|seen) (above|earlier|previously)|to some extent|generally speaking|in general,)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.38,
        "action": "review",
    },
    {
        "name": "vague_prospect",
        "description": "空泛展望",
        "note": "「提供了新思路 / 应用前景广阔 / 有望」类空泛展望，缺落地依据，建议删或换成具体方向。",
        "pattern": r"(提供了?[^\n。！？]{0,8}?新(思路|视角|途径|方向)|具有[^\n。！？]{0,8}?(广阔|广泛|巨大)[^\n。！？]{0,6}?(前景|潜力|价值)|应用前景[^\n。！？]{0,8}?(广|好)|有望[^\n。！？]{0,12}?|为[^\n。！？]{0,12}?提供[^\n。！？]{0,8}?(参考|借鉴|思路)|provides? a (new|novel|promising)|has (great|wide|broad) (potential|prospects?)|is promising|paves? the way|opens? (up )?new)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.38,
        "action": "review",
    },
    {
        "name": "acknowledgment_funding",
        "description": "致谢 / 基金资助",
        "note": "致谢 / 基金属正文外信息，提取论文核心内容时一般可剔除。",
        "pattern": r"(致\s*谢|衷心感谢|感谢[^\n。！？]{0,20}?(老师|导师|同学|审稿|课题组|基金|资助|支持)|本(文|研究|工作)(得到|受到?)[^\n。！？]{0,20}?(基金|项目|课题|资助)|国家自然科学基金|课题(资助|支持)|acknowledg(e?ment|ements)|we (would like to )?thank|this work (was|is) (partially )?supported (by|in part)|funded by|grant (no|number|#))[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.35,
        "action": "review",
    },

    # ===== 补充细分（深化）：更硬核的核心类 + 更细的水话类 =====
    {
        "name": "statistical_significance",
        "description": "统计显著性 / 检验",
        "note": "统计显著性（p 值 / 置信区间 / 显著性检验），结果可信度的硬证据。",
        "pattern": r"(统计(学)?(上)?显著|显著性(差异|检验|水平)|显著差异|p\s*[<>=]\s*0?\.\d+|置信区间|置信水平|方差分析|[tF]\s*检验|卡方检验|假设检验|standard (deviation|error)|p-?values?|confidence intervals?|statistical(ly)? significan|t-tests?|ANOVA|chi-square|hypothesis test)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.82,
        "action": "keep",
    },
    {
        "name": "hyperparameter_setting",
        "description": "超参数 / 训练设置",
        "note": "学习率、batch、轮数等超参与训练设置，决定结果能否复现。",
        "pattern": r"(学习率|批(大小|尺寸|量大小)|批量大小|迭代(次数|轮数)|训练轮数|轮次为|隐藏层|权重衰减|正则化系数|学习速率|优化器|参数初始化|learning rate|batch\s*size|epochs?|hidden (layer|unit)s?|dropout|weight decay|optimi[sz]er|\bAdam\b|\bSGD\b|hyper-?parameter|fine-?tun(e|ing|ed))[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.74,
        "action": "keep",
    },
    {
        "name": "comparison_baseline",
        "description": "对比 / 基线方法",
        "note": "与基线 / 现有方法的对比，是衡量贡献大小的参照系。",
        "pattern": r"(与[^\n，。；]{0,15}?(方法|算法|模型|基线|工作|方案)[^\n，。；]{0,6}?(相比|对比|比较)|相比(之下|于)[^\n，。；]{0,10}?(方法|基线|现有|传统)|对比[^\n，。；]{0,8}?(方法|算法|实验|基线)|优于[^\n，。；]{0,10}?(基线|现有方法|传统方法)|compared (to|with)|in comparison (to|with)|(outperform|surpass|exceed|beat)(s|ed)?[^\n，。；]{0,12}?(baseline|existing|prior|state-of)|than (the |existing |prior )?baselines?|over (the |existing )?baselines?)[^\n；。！？]{0,}[；。！？\n]?",
        "min_confidence": 0.75,
        "action": "keep",
    },
    {
        "name": "exaggeration",
        "description": "绝对化 / 夸大",
        "note": "「大大 / 极大 / 完美 / 前所未有」类无数据支撑的夸大，审稿易反感，建议删或换成具体数字。",
        "pattern": r"(大大(地)?(提高|提升|改善|降低|增强|减少|缩短)|极大(地|程度上)|完美(地)?(解决|实现|契合)|彻底(地)?(解决|改变|消除)|前所未有|无可比拟|遥遥领先|完全(超越|优于|解决)|极其(重要|优异|出色)|greatly|dramatically|remarkably|perfectly (solve|address)|completely (solve|outperform)|far (superior|better)|unprecedented)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.38,
        "action": "review",
    },
    {
        "name": "tautology_verbose",
        "description": "同义反复 / 啰嗦",
        "note": "「换言之 / 也就是说 / 亦即」等重复表述，前句已说清，删之更紧凑。",
        "pattern": r"(换言之|换句话说|也就是说|亦即|换个角度(说|看)|从某种(意义|程度)上(说|讲)?|严格(来说|地说)|准确地?说|in other words|that is to say|to put it (differently|another way)|strictly speaking|more precisely,)[^\n。！？]{0,}?(。|！|？|\n)",
        "min_confidence": 0.36,
        "action": "review",
    },
]


# -----------------------------------------------------------------------------
# 命中片段的「整句」窗口：替代旧的 text[start-40:end+40]「按字符 ±40 硬切」。
# 旧法两端常切在词/句中间，原文对照看着就像「截了一半」。这里把命中向两侧扩到
# 最近的句末边界（中文标点/换行直接算；英文 . ! ? 需后接空白才算，避开 3.14、Fig.），
# 并设字数上限防跑飞。文本本身的质量（docling 已理顺）不在这里管，这里只管「截得齐不齐」。
# -----------------------------------------------------------------------------
_SNIPPET_HARD = '。！？\n'   # 中文句末 + 换行：直接算边界
_SNIPPET_SOFT = '.!?'       # 英文句末：需后接空白才算边界


def _is_sentence_end(text, i):
    """text[i-1] 是否构成一个句末边界（i 为「边界右侧」下标）。"""
    if i <= 0:
        return True
    c = text[i - 1]
    if c in _SNIPPET_HARD:
        return True
    if c in _SNIPPET_SOFT:
        nxt = text[i] if i < len(text) else ' '
        return nxt.isspace()
    return False


def _sentence_snippet(text, start, end, max_chars=240):
    """把命中 [start, end) 向两侧扩到完整句子边界，得到「不截半」的原文片段。"""
    L = start
    lo = max(0, start - max_chars)
    while L > lo and not _is_sentence_end(text, L):
        L -= 1
    R = end
    hi = min(len(text), end + max_chars)
    while R < hi and not _is_sentence_end(text, R):
        R += 1
    # 若因撞到字数上限而停（句子过长、非句末），回退到最近空白，避免英文在词中间收尾
    if R < len(text) and not _is_sentence_end(text, R):
        sp = text.rfind(' ', end, R)
        if sp > end:
            R = sp
    return text[L:R].strip()


def match_rules(text):
    matches = []
    tfidf = doc_tfidf_weights(text)
    for rule in RULES:
        # IGNORECASE：让英文触发词不区分大小写；对中文无影响。
        for m in re.finditer(rule['pattern'], text, flags=re.IGNORECASE):
            sal, factors = compute_salience(m.group(0), rule, tfidf, return_breakdown=True)
            matches.append({
                "rule": rule['name'],
                "description": rule['description'],
                "note": rule.get('note', ''),  # 人话说明：keep=这是什么核心 / review=为什么是水+建议
                "start": m.start(),
                "end": m.end(),
                "snippet": _sentence_snippet(text, m.start(), m.end()),
                "salience": sal,
                "salience_factors": factors,  # 四特征分解，供「为什么显著度高」展示
                "action": rule.get('action', 'review'),
            })
    return matches
