/*
 * PaperCore · 轻量前端 i18n（中 → 英，只翻「界面壳」）
 * ---------------------------------------------------------------
 * 设计：
 *  - 中文是源语言（lang=zh 时引擎不动）；DICT 是单向 zh→en 映射。
 *  - 切换语言 = 存 localStorage + reload：永远不需要「英→中」反向还原，最稳。
 *    sessionStorage 跨 reload 保留，结果页照常重渲染，再被自动翻译。
 *  - translateDOM 遍历文本节点 + placeholder/title/aria-label 属性，精确匹配才替换。
 *  - MutationObserver 监听 DOM 变化：JS 动态渲染出的新节点（结果页大量卡片）
 *    自动翻译，无需逐个改 t()。译过即英文、不再匹配 zh key，不会死循环。
 *  - 范围：只翻导航/按钮/标题/提示/标签等界面壳。后端生成的分析内容
 *    （综合点评、体检明细、显著度标签、导出报告）保持中文——这是产品定的范围。
 */
(function () {
  'use strict';

  var LANG_KEY = 'papercore_lang';
  var lang = localStorage.getItem(LANG_KEY) || 'zh';

  // ===== 中 → 英 词典（界面壳）=====
  var DICT = {
    // 侧栏导航 / 通用状态
    '工作台': 'Workbench', '我的文档': 'My Documents', '分析历史': 'History',
    '结构化报告': 'Reports', '设置': 'Settings', '系统设置': 'System Settings',
    '本地运行中': 'Running Locally', '所有数据仅在本地处理': 'All data processed locally',
    '本地模式': 'Local Mode', '数据不上云': 'No Cloud Upload', '隐私保护中': 'Privacy Protected',
    '数据经 API 上云': 'Data sent via API', 'AI 模式': 'AI Mode',
    // 首页 系统状态卡
    '系统状态': 'System Status', '运行中': 'Running', '规则引擎': 'Rule Engine',
    '已加载': 'Loaded', 'AI 增强': 'AI Boost', '隐私保护': 'Privacy', '开启': 'On',
    // 首页 hero
    '论文分析工作台': 'Paper Analysis Workbench',
    'AI 深度分析': 'Deep AI Analysis', '多模式理解': 'Multi-mode Understanding',
    '本地隐私优先': 'Local-first Privacy', '工科论文专用': 'Built for STEM Papers',
    '接入 DeepSeek V3.2，精准识别研究问题、核心方法、关键公式、实验数据与创新贡献，生成结构化分析报告，提升科研阅读效率。':
      'Powered by DeepSeek V3.2 — pinpoints research questions, core methods, key formulas, experimental data and contributions, generating structured reports to speed up research reading.',
    // 上传区
    '上传论文': 'Upload Paper',
    '拖拽或点击选择 PDF / DOCX / TXT / Markdown': 'Drag & drop or click to choose PDF / DOCX / TXT / Markdown',
    '浏览文件': 'Browse Files',
    'DeepSeek API Key': 'DeepSeek API Key',
    '（可选，不填则使用本地规则分析）': '(Optional — falls back to local rule-based analysis)',
    '用本地大模型分析': 'Analyze with Local LLM', '本地': 'Local',
    '不填 Key，调用本机 Ollama 做深度分析 · 纯本地不出网（较慢）':
      'No key needed — runs local Ollama for deep analysis · fully offline (slower)',
    'v4pro 高级分析': 'v4pro Advanced',
    '资深评审视角，更深更长的结构化分析 · 5 小时内限用 5 次':
      'Senior-reviewer perspective, deeper analysis · up to 5 uses per 5 hours',
    // 分析模式
    '选择分析模式': 'Choose Analysis Mode', '30秒速读': '30-sec Skim',
    '提取核心三要素': 'Core three elements', '结构化理解': 'Structured', '快速摘要': 'Quick Summary',
    '六维深度解析': 'Six-dimension parsing', '公式/实验保护': 'Formula / Data Mode',
    '精确技术数据': 'Precise technical data', '答辩汇报': 'Defense Mode',
    '生成答辩大纲': 'Generate defense outline', '开始分析': 'Start Analysis',
    // 实时预览面板
    '实时分析预览': 'Live Analysis Preview', '等待上传论文': 'Waiting for upload',
    '上传后将实时展示：': 'After upload you will see:', '研究问题': 'Research Question',
    '核心方法': 'Core Method', '实验结果': 'Results', '创新点': 'Innovations',
    '论文结论': 'Conclusion', '分析日志': 'Analysis Log', 'AI 分析中': 'Analyzing',
    '请勿关闭页面': 'Please keep this page open',
    // 为谁而做
    '为谁而做': 'Who It’s For',
    'PaperCore 不是又一个聊天框，而是一套本地优先的论文理解系统——把核心内容留在你自己的电脑里。':
      'PaperCore is not just another chat box — it is a local-first paper understanding system that keeps your content on your own machine.',
    '在校学生 · 考研党': 'Students & Exam Prep',
    '读得慢，抓不住一篇论文「做了什么、怎么做、结果如何」':
      'Reading is slow — hard to grasp what a paper did, how, and the results',
    '30 秒速读核心三要素，一键生成答辩大纲':
      '30-sec skim of the core three elements, one-click defense outline',
    '科研人员 · 研究生': 'Researchers & Grad Students',
    '海量文献要快速筛选定位，公式与实验数据不能读错':
      'Massive literature to screen fast; formulas and data must not be misread',
    '公式/变量保护 + 实验数据提取，六维结构化解析':
      'Formula/variable protection + data extraction, six-dimension parsing',
    '隐私敏感场景': 'Privacy-sensitive Cases',
    '未发表稿件、涉密课题，不敢把原文丢进云端大模型':
      'Unpublished drafts or confidential work — can’t risk cloud LLMs',
    '本地模式可完全离线运行，文件与正文不出本机':
      'Local mode runs fully offline; files and text never leave your machine',
    // 系统能力
    '系统能力': 'Capabilities', '智能章节识别': 'Smart Section Detection',
    '自动识别摘要、引言、方法、实验、结论等标准章节结构':
      'Auto-detects abstract, intro, methods, experiments, conclusion',
    '公式与变量保护': 'Formula & Variable Protection',
    '精确提取数学公式与变量定义，保持符号系统完整性':
      'Precisely extracts formulas and variable definitions, keeping notation intact',
    '实验数据提取': 'Experimental Data Extraction',
    '识别表格、图表数据与关键实验结论，不遗漏关键数值':
      'Captures tables, chart data and key results without missing numbers',
    '结构化报告导出': 'Structured Report Export',
    '生成可复用的结构化分析报告，支持 TXT / Markdown 导出':
      'Generates reusable structured reports, exportable as TXT / Markdown',
    // 流程
    '分析流程': 'Pipeline', '上传 PDF': 'Upload PDF', 'docling 解析': 'docling Parse',
    '章节识别': 'Section Detection', '核心提取': 'Core Extraction',
    'DeepSeek 分析': 'DeepSeek Analysis', '导出报告': 'Export Report',
    // SPA 占位页
    '本地隐私模式下，文档不会自动上传云端': 'In local privacy mode, documents are never auto-uploaded',
    '未授权': 'Not Enabled', '本地文档库暂未开启': 'Local document library not enabled yet',
    '如需启用历史文档管理，可在后续版本接入本地数据库或浏览器缓存':
      'A future version may add a local database or browser cache for document management',
    '返回工作台': 'Back to Workbench', '上传新论文': 'Upload New Paper',
    '当前版本仅保留本次分析结果': 'This version keeps only the current result',
    '暂无记录': 'No Records', '还没有历史分析记录': 'No analysis history yet',
    '后续版本可接入 SQLite / IndexedDB，实现持久化历史管理':
      'A future version may use SQLite / IndexedDB for persistent history',
    '开始首次分析': 'Run First Analysis',
    '分析完成后可生成 Markdown / TXT 报告': 'Generate Markdown / TXT reports after analysis',
    '待生成': 'Pending', '请先完成一次论文分析': 'Run an analysis first',
    '分析结果将在工作台生成后，支持导出为结构化 Markdown 或 TXT 格式报告':
      'Once analyzed in the workbench, results can be exported as structured Markdown or TXT',
    '前往工作台分析': 'Go to Workbench', '当前为演示配置，设置项为只读状态': 'Demo config — settings are read-only',
    // 设置页
    '当前为只读配置 · 实时反映后端运行状态': 'Read-only config · reflects live backend status',
    '解析与规则提取均在本地运行': 'Parsing and rule extraction run locally',
    '文件与文本内容不发送至远程服务器': 'Files and text are never sent to remote servers',
    '扫描件 OCR': 'Scanned-PDF OCR',
    '无文字层 PDF 的本地 OCR 兜底（RapidOCR，不出网）': 'Local OCR fallback for text-less PDFs (RapidOCR, offline)',
    '本地大模型（Ollama）': 'Local LLM (Ollama)',
    '本机 Ollama 深度分析，纯本地不出网': 'Deep analysis via local Ollama, fully offline',
    '云端 API Key': 'Cloud API Key',
    'DeepSeek 云端 API 密钥（可选，不填则走本地）': 'DeepSeek cloud API key (optional; falls back to local)',
    'v4pro 高级模式': 'v4pro Advanced',
    '资深评审视角的深度分析 · 5 小时滚动配额，最多 5 次':
      'Senior-reviewer deep analysis · rolling 5-hour quota, up to 5 uses',
    '调用 DeepSeek API 进行深度分析': 'Calls DeepSeek API for deep analysis',
    'DeepSeek 云端 API 密钥': 'DeepSeek cloud API key', '演示模式': 'Demo',
    '可用': 'Available', '不可用': 'Unavailable', '未检测到': 'Not Detected',
    '已配置': 'Configured', '未配置': 'Not Set', '需配置 Key': 'Key Required',
    '已锁定': 'Locked', '检测中…': 'Checking…', '状态获取失败：': 'Status fetch failed: ',
    // 我的文档
    '上传过的论文原件，仅存在本机 · 同名文件只显示最新一份':
      'Uploaded paper originals, stored locally · only the latest of same-name files is shown',
    '还没有上传过文档': 'No documents uploaded yet',
    '去工作台上传一篇论文，文件会留在这里供管理': 'Upload a paper in the workbench; files will be managed here',
    '前往工作台': 'Go to Workbench', '文档加载失败：': 'Failed to load documents: ',
    '尚未分析': 'Not analyzed', '下载原件': 'Download', '重新分析': 'Re-analyze',
    '删除': 'Delete', '删除失败': 'Delete failed', '删除失败：': 'Delete failed: ',
    '确认删除这份文档原件？此操作不可撤销（不影响已生成的分析历史）。':
      'Delete this document original? This cannot be undone (analysis history is unaffected).',
    // 分析历史
    '所有记录仅存在本机 · 不上传云端 · 可随时一键清空':
      'All records are local · never uploaded · clear anytime',
    '清空历史': 'Clear History', '还没有分析记录': 'No analysis records yet',
    '去工作台上传一篇论文，分析结果会自动留在这里':
      'Upload a paper in the workbench; results are saved here automatically',
    '查看': 'View', '历史加载失败：': 'Failed to load history: ',
    '本地规则 + 算法': 'Local rules + algorithm', '记录不存在或已删除': 'Record not found or deleted',
    '打开失败：': 'Open failed: ', '确认删除这条分析记录？此操作不可撤销。':
      'Delete this analysis record? This cannot be undone.',
    '确认清空全部分析历史？此操作不可撤销。': 'Clear all analysis history? This cannot be undone.',
    '清空失败：': 'Clear failed: ',
    // 报告中心
    '基于分析历史，一键导出 Markdown / TXT 结构化报告 · 全程本地生成':
      'One-click Markdown / TXT structured reports from history · generated locally',
    '导出全部（MD）': 'Export All (MD)', '还没有可导出的报告': 'No reports to export yet',
    '先在工作台完成一次论文分析，这里就能导出结构化报告':
      'Run an analysis in the workbench, then export structured reports here',
    '报告列表加载失败：': 'Failed to load reports: ', '导出 MD': 'Export MD', '导出 TXT': 'Export TXT',
    // 结果页
    '分析结果': 'Analysis Result', 'AI 分析报告': 'AI Analysis Report',
    '未找到解析数据，请': 'No parsed data found, please ', '返回首页': 'return home',
    '重新上传。': ' and re-upload.', '文本字数': 'Word Count', '核心命中': 'Core Hits',
    '候选片段': 'Candidates', '待审查': 'To Review', '论文体检报告': 'Paper Health Check',
    '本地算法 · 不上云': 'Local algorithm · offline', '综合点评': 'Overall Review',
    '本地生成 · 不上云': 'Generated locally · offline', 'DeepSeek 结构化分析': 'DeepSeek Structured Analysis',
    '当前为演示分析结果，用于展示系统报告结构；接入 DeepSeek 后将根据论文内容生成真实分析。':
      'This is a demo result showing the report structure; with DeepSeek connected it generates real analysis.',
    '规则匹配结果': 'Rule Matches', '导出 Markdown': 'Export Markdown', '导出 TXT': 'Export TXT',
    '← 重新上传': '← Re-upload',
    '由 PaperCore AI 自动生成 · 仅供学术参考 · 数据未上云':
      'Auto-generated by PaperCore AI · for academic reference · data not uploaded',
    '处理中...': 'Processing...', '原文对照': 'Source Text', '核心': 'Core', '候选': 'Candidate',
    '未知文件': 'Unknown file', '显著度': 'Salience', '关键词密度兜底': 'Keyword-density fallback',
    '分析引擎': 'Engine', '论文': 'Paper', '分析时间': 'Analyzed at', '模式': 'Mode',
    '未匹配到强规则片段，已展示候选片段（基于关键词密度自动选取）。':
      'No strong rule matches; showing candidate snippets (auto-selected by keyword density).',
    '本地提取结果': 'Local Extraction', '本地规则模式': 'Local rule mode',
    '提取说明': 'Extraction Note',
    '本地规则未能识别标准章节结构（摘要/方法/结论）。':
      'Local rules could not detect standard sections (abstract / methods / conclusion).',
    '可能原因：PDF 为图像版或章节命名不规范。':
      'Possible cause: image-based PDF or non-standard section titles.',
    '请尝试接入 DeepSeek API 进行 AI 分析，或查看下方规则匹配片段。':
      'Try connecting the DeepSeek API for AI analysis, or see the rule matches below.',
    '重点建议': 'Key Suggestions', '改进建议': 'Suggestions',
    '无数据可导出': 'No data to export', '正在生成导出文件...': 'Generating export file...',
    '导出失败：': 'Export failed: ', '章节识别失败': 'Section detection failed',
    '未提取到': 'Not extracted',
    // 通用
    '折叠/展开导航': 'Collapse / expand nav', '收起原文栏': 'Collapse source panel',
  };

  // ===== 引擎 =====
  function tr(s) {
    if (s == null) return s;
    var key = String(s).trim();
    if (!key) return s;
    var en = DICT[key];
    if (en === undefined) return s;
    // 保留原文前后空白
    return String(s).replace(key, en);
  }

  var ATTRS = ['placeholder', 'title', 'aria-label'];

  function translateNode(node) {
    if (node.nodeType === 3) { // 文本节点
      var t = tr(node.nodeValue);
      if (t !== node.nodeValue) node.nodeValue = t;
      return;
    }
    if (node.nodeType !== 1) return;
    if (node.tagName === 'SCRIPT' || node.tagName === 'STYLE') return;
    for (var i = 0; i < ATTRS.length; i++) {
      if (node.hasAttribute(ATTRS[i])) {
        var v = node.getAttribute(ATTRS[i]);
        var nv = tr(v);
        if (nv !== v) node.setAttribute(ATTRS[i], nv);
      }
    }
    for (var c = node.firstChild; c; c = c.nextSibling) translateNode(c);
  }

  function translateDOM(root) { translateNode(root || document.body); }

  // ===== 顶栏 中/EN 快捷开关（自动注入每页 .topbar）=====
  function injectSwitch() {
    var bars = document.querySelectorAll('.topbar');
    bars.forEach(function (bar) {
      if (bar.querySelector('.lang-switch')) return;
      var btn = document.createElement('button');
      btn.className = 'lang-switch';
      btn.type = 'button';
      btn.textContent = (lang === 'en') ? '中' : 'EN';
      btn.title = (lang === 'en') ? '切换为中文' : 'Switch to English';
      btn.addEventListener('click', function () {
        setLang(lang === 'en' ? 'zh' : 'en');
      });
      // 放到状态徽章区旁/顶栏末尾
      var badges = bar.querySelector('.status-badges');
      if (badges) badges.appendChild(btn); else bar.appendChild(btn);
    });
  }

  function setLang(l) {
    localStorage.setItem(LANG_KEY, l);
    location.reload(); // 切换=重载：永不需要反向还原，最稳
  }
  window.setPaperCoreLang = setLang; // 供设置页按钮调用

  // ===== 启动 =====
  function boot() {
    document.documentElement.setAttribute('lang', lang === 'en' ? 'en' : 'zh-CN');
    injectSwitch();
    if (lang === 'en') {
      translateDOM(document.body);
      // 监听后续 JS 动态渲染的节点
      var obs = new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var m = muts[i];
          for (var j = 0; j < m.addedNodes.length; j++) translateNode(m.addedNodes[j]);
          if (m.type === 'characterData' && m.target) translateNode(m.target);
        }
      });
      obs.observe(document.body, { childList: true, subtree: true, characterData: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
