/*
 * PaperCore · 轻量前端 i18n（多语言，只翻「界面壳」）
 * ---------------------------------------------------------------
 * 设计：
 *  - 中文是源语言（lang=zh 时引擎不动）；DICT 是「中文 → {各目标语言}」映射。
 *    扩语言 = 给每条加一个 'ja'/'ko'/'de' 值，不动模板、不动引擎。
 *  - 切换语言 = 存 localStorage + reload：永远不需要反向还原，最稳。
 *  - translateDOM 遍历文本节点 + placeholder/title/aria-label 属性，精确匹配才替换。
 *  - MutationObserver 监听 DOM 变化：JS 动态渲染出的新节点（结果页大量卡片）
 *    自动翻译，无需逐个改。译过即目标语言、不再匹配中文 key，不会死循环。
 *  - 富文本 / JS 生成内容（体验区弹窗、带数字的配额标）：用 data-i18n 键 + KEYS
 *    （innerHTML 注入），以及 window.pcI18n.key() 供 main.js 取译文。
 *  - 范围：只翻界面壳（导航/按钮/标题/提示/标签）。后端生成的分析正文保持原样
 *    （另由「输出语言本地化」让 LLM 直接按界面语言产出）。
 *  - 日语为 AI 初翻，上线前建议日语母语校对；韩/德结构已留好，填值即可。
 */
(function () {
  'use strict';

  var LANG_KEY = 'papercore_lang';
  var LANGS = ['zh', 'en', 'ja'];           // zh = 源语言；扩语言在此加 + 给 DICT/KEYS 补值
  var lang = localStorage.getItem(LANG_KEY) || 'zh';
  if (LANGS.indexOf(lang) < 0) lang = 'zh';

  // ===== 中文 → 各语言 词典（界面壳）=====
  var DICT = {
    // 侧栏导航 / 通用状态
    '工作台': { en: 'Workbench', ja: 'ワークベンチ' },
    '我的文档': { en: 'My Documents', ja: 'マイドキュメント' },
    '分析历史': { en: 'History', ja: '分析履歴' },
    '结构化报告': { en: 'Reports', ja: '構造化レポート' },
    '设置': { en: 'Settings', ja: '設定' },
    '系统设置': { en: 'System Settings', ja: 'システム設定' },
    '产品介绍': { en: 'Product', ja: 'プロダクト紹介' },
    '关于我们': { en: 'About', ja: '私たちについて' },
    '本地运行中': { en: 'Running Locally', ja: 'ローカルで実行中' },
    '所有数据仅在本地处理': { en: 'All data processed locally', ja: 'すべてのデータはローカルで処理' },
    '本地模式': { en: 'Local Mode', ja: 'ローカルモード' },
    '数据不上云': { en: 'No Cloud Upload', ja: 'クラウド送信なし' },
    '隐私保护中': { en: 'Privacy Protected', ja: 'プライバシー保護中' },
    '数据经 API 上云': { en: 'Data sent via API', ja: 'データはAPI経由で送信' },
    'AI 模式': { en: 'AI Mode', ja: 'AIモード' },
    // 首页 系统状态卡
    '系统状态': { en: 'System Status', ja: 'システム状態' },
    '运行中': { en: 'Running', ja: '稼働中' },
    '规则引擎': { en: 'Rule Engine', ja: 'ルールエンジン' },
    '已加载': { en: 'Loaded', ja: '読み込み済み' },
    'AI 增强': { en: 'AI Boost', ja: 'AI強化' },
    '隐私保护': { en: 'Privacy', ja: 'プライバシー保護' },
    '开启': { en: 'On', ja: 'オン' },
    // 首页 hero
    '论文分析工作台': { en: 'Paper Analysis Workbench', ja: '論文分析ワークベンチ' },
    'AI 深度分析': { en: 'Deep AI Analysis', ja: 'AI詳細分析' },
    '多模式理解': { en: 'Multi-mode Understanding', ja: 'マルチモード理解' },
    '本地隐私优先': { en: 'Local-first Privacy', ja: 'ローカル優先のプライバシー' },
    '工科论文专用': { en: 'Built for STEM Papers', ja: '理工系論文専用' },
    '接入 DeepSeek V3.2，精准识别研究问题、核心方法、关键公式、实验数据与创新贡献，生成结构化分析报告，提升科研阅读效率。':
      { en: 'Powered by DeepSeek V3.2 — pinpoints research questions, core methods, key formulas, experimental data and contributions, generating structured reports to speed up research reading.',
        ja: 'DeepSeek V3.2 を搭載し、研究課題・コア手法・重要な数式・実験データ・新規性を的確に抽出。構造化レポートを生成して論文読解を加速します。' },
    // 上传区
    '上传论文': { en: 'Upload Paper', ja: '論文をアップロード' },
    '拖拽或点击选择 PDF / DOCX / TXT / Markdown': { en: 'Drag & drop or click to choose PDF / DOCX / TXT / Markdown', ja: 'PDF / DOCX / TXT / Markdown をドラッグ＆ドロップ、またはクリックして選択' },
    '浏览文件': { en: 'Browse Files', ja: 'ファイルを選択' },
    'DeepSeek API Key': { en: 'DeepSeek API Key', ja: 'DeepSeek API キー' },
    '（可选，不填则使用本地规则分析）': { en: '(Optional — falls back to local rule-based analysis)', ja: '（任意：未入力ならローカルのルール分析）' },
    '用本地大模型分析': { en: 'Analyze with Local LLM', ja: 'ローカルLLMで分析' },
    '本地': { en: 'Local', ja: 'ローカル' },
    '不填 Key，调用本机 Ollama 做深度分析 · 纯本地不出网（较慢）':
      { en: 'No key needed — runs local Ollama for deep analysis · fully offline (slower)',
        ja: 'キー不要、ローカルの Ollama で詳細分析・完全オフライン（やや遅い）' },
    'v4pro 高级分析': { en: 'v4pro Advanced', ja: 'v4pro 上級分析' },
    '资深评审视角，更深更长的结构化分析 · 5 小时内限用 5 次':
      { en: 'Senior-reviewer perspective, deeper analysis · up to 5 uses per 5 hours',
        ja: 'ベテラン査読者の視点でより深い構造化分析・5時間に5回まで' },
    // 分析模式
    '选择分析模式': { en: 'Choose Analysis Mode', ja: '分析モードを選択' },
    '30秒速读': { en: '30-sec Skim', ja: '30秒速読' },
    '提取核心三要素': { en: 'Core three elements', ja: 'コア3要素を抽出' },
    '结构化理解': { en: 'Structured', ja: '構造化理解' },
    '快速摘要': { en: 'Quick Summary', ja: 'クイック要約' },
    '六维深度解析': { en: 'Six-dimension parsing', ja: '6次元の詳細解析' },
    '公式/实验保护': { en: 'Formula / Data Mode', ja: '数式・実験データ保護' },
    '精确技术数据': { en: 'Precise technical data', ja: '技術データを正確に' },
    '答辩汇报': { en: 'Defense Mode', ja: '口頭発表モード' },
    '生成答辩大纲': { en: 'Generate defense outline', ja: '発表アウトラインを生成' },
    '开始分析': { en: 'Start Analysis', ja: '分析を開始' },
    // 论文学科选择器（决定方法维度用哪套方法学环节评分）
    '论文学科 · 方法尺度': { en: 'Subject · Method Rubric', ja: '分野 · 手法の尺度' },
    '通用 / 不确定': { en: 'General / Unsure', ja: '汎用 / 不明' },
    '计算机 / 人工智能': { en: 'Computer Science / AI', ja: '計算機 / 人工知能' },
    '医学 / 生命科学': { en: 'Medicine / Life Sciences', ja: '医学 / 生命科学' },
    '工程 / 物理 / 材料': { en: 'Engineering / Physics / Materials', ja: '工学 / 物理 / 材料' },
    '材料 / 化学': { en: 'Materials / Chemistry', ja: '材料 / 化学' },
    '电子 / 电气': { en: 'Electronics / EE', ja: '電子 / 電気' },
    '土木 / 建筑': { en: 'Civil / Architecture', ja: '土木 / 建築' },
    '生物 / 生命科学': { en: 'Biology / Life Sciences', ja: '生物 / 生命科学' },
    '数学 / 理论': { en: 'Mathematics / Theory', ja: '数学 / 理論' },
    '这篇 PDF 带水印吗？': { en: 'Does this PDF have a watermark?', ja: 'この PDF に透かしはありますか？' },
    '正常分析': { en: 'Normal analysis', ja: '通常の分析' },
    '带水印 · 加强去除': { en: 'Has watermark · stronger removal', ja: '透かしあり · 強力に除去' },
    '社科 / 经管 / 人文': { en: 'Social Sciences / Business / Humanities', ja: '社会科学 / 経営 / 人文' },
    '按论文学科评「方法完整性」，选对更准；词库上千后靠这个避免「堆词判高」。': {
        en: 'Scores "method completeness" by subject — pick the right one for accuracy; with a large keyword base this prevents keyword-stuffing from inflating scores.',
        ja: '分野に応じて「手法の完全性」を評価。正しく選ぶほど正確です。語彙が大規模化した今、これで「語句の詰め込みによる過大評価」を防ぎます。' },
    // 实时预览面板
    '实时分析预览': { en: 'Live Analysis Preview', ja: 'リアルタイム分析プレビュー' },
    '等待上传论文': { en: 'Waiting for upload', ja: '論文のアップロード待ち' },
    '上传后将实时展示：': { en: 'After upload you will see:', ja: 'アップロード後に表示されます：' },
    '研究问题': { en: 'Research Question', ja: '研究課題' },
    '核心方法': { en: 'Core Method', ja: 'コア手法' },
    '实验结果': { en: 'Results', ja: '実験結果' },
    '创新点': { en: 'Innovations', ja: '新規性' },
    '论文结论': { en: 'Conclusion', ja: '結論' },
    '分析日志': { en: 'Analysis Log', ja: '分析ログ' },
    'AI 分析中': { en: 'Analyzing', ja: 'AI分析中' },
    '请勿关闭页面': { en: 'Please keep this page open', ja: 'このページを閉じないでください' },
    // 为谁而做
    '为谁而做': { en: 'Who It’s For', ja: '対象ユーザー' },
    'PaperCore 不是又一个聊天框，而是一套本地优先的论文理解系统——把核心内容留在你自己的电脑里。':
      { en: 'PaperCore is not just another chat box — it is a local-first paper understanding system that keeps your content on your own machine.',
        ja: 'PaperCore は単なるチャットボックスではなく、コンテンツを自分のPCに留めるローカル優先の論文理解システムです。' },
    '在校学生 · 考研党': { en: 'Students & Exam Prep', ja: '学生・受験生' },
    '读得慢，抓不住一篇论文「做了什么、怎么做、结果如何」':
      { en: 'Reading is slow — hard to grasp what a paper did, how, and the results',
        ja: '読むのが遅く、論文の「何を・どうやって・結果は」を掴みにくい' },
    '30 秒速读核心三要素，一键生成答辩大纲':
      { en: '30-sec skim of the core three elements, one-click defense outline',
        ja: '30秒でコア3要素を速読、ワンクリックで発表アウトライン' },
    '科研人员 · 研究生': { en: 'Researchers & Grad Students', ja: '研究者・大学院生' },
    '海量文献要快速筛选定位，公式与实验数据不能读错':
      { en: 'Massive literature to screen fast; formulas and data must not be misread',
        ja: '大量の文献を素早く選別、数式と実験データを読み違えない' },
    '公式/变量保护 + 实验数据提取，六维结构化解析':
      { en: 'Formula/variable protection + data extraction, six-dimension parsing',
        ja: '数式・変数の保護＋実験データ抽出、6次元の構造化解析' },
    '隐私敏感场景': { en: 'Privacy-sensitive Cases', ja: 'プライバシー重視の場面' },
    '未发表稿件、涉密课题，不敢把原文丢进云端大模型':
      { en: 'Unpublished drafts or confidential work — can’t risk cloud LLMs',
        ja: '未発表の原稿や機密課題を、クラウドLLMに渡すのは不安' },
    '本地模式可完全离线运行，文件与正文不出本机':
      { en: 'Local mode runs fully offline; files and text never leave your machine',
        ja: 'ローカルモードは完全オフラインで動作、ファイルも本文も端末から出ません' },
    // 系统能力
    '系统能力': { en: 'Capabilities', ja: '機能' },
    '智能章节识别': { en: 'Smart Section Detection', ja: 'インテリジェントな章構成検出' },
    '自动识别摘要、引言、方法、实验、结论等标准章节结构':
      { en: 'Auto-detects abstract, intro, methods, experiments, conclusion',
        ja: '要旨・序論・手法・実験・結論などの標準的な章構成を自動検出' },
    '公式与变量保护': { en: 'Formula & Variable Protection', ja: '数式・変数の保護' },
    '精确提取数学公式与变量定义，保持符号系统完整性':
      { en: 'Precisely extracts formulas and variable definitions, keeping notation intact',
        ja: '数式と変数定義を正確に抽出し、記号体系の整合性を維持' },
    '实验数据提取': { en: 'Experimental Data Extraction', ja: '実験データの抽出' },
    '识别表格、图表数据与关键实验结论，不遗漏关键数值':
      { en: 'Captures tables, chart data and key results without missing numbers',
        ja: '表・図のデータと重要な実験結論を捉え、重要な数値を見逃さない' },
    '结构化报告导出': { en: 'Structured Report Export', ja: '構造化レポートの出力' },
    '生成可复用的结构化分析报告，支持 TXT / Markdown 导出':
      { en: 'Generates reusable structured reports, exportable as TXT / Markdown',
        ja: '再利用可能な構造化レポートを生成、TXT / Markdown で出力可能' },
    // 流程
    '分析流程': { en: 'Pipeline', ja: '分析フロー' },
    '上传 PDF': { en: 'Upload PDF', ja: 'PDFをアップロード' },
    'docling 解析': { en: 'docling Parse', ja: 'docling 解析' },
    '章节识别': { en: 'Section Detection', ja: '章構成検出' },
    '核心提取': { en: 'Core Extraction', ja: 'コア抽出' },
    'DeepSeek 分析': { en: 'DeepSeek Analysis', ja: 'DeepSeek 分析' },
    '导出报告': { en: 'Export Report', ja: 'レポート出力' },
    // SPA 占位页
    '本地隐私模式下，文档不会自动上传云端': { en: 'In local privacy mode, documents are never auto-uploaded', ja: 'ローカルプライバシーモードでは、文書が自動でクラウドに送られることはありません' },
    '未授权': { en: 'Not Enabled', ja: '未許可' },
    '本地文档库暂未开启': { en: 'Local document library not enabled yet', ja: 'ローカル文書ライブラリは未開放' },
    '如需启用历史文档管理，可在后续版本接入本地数据库或浏览器缓存':
      { en: 'A future version may add a local database or browser cache for document management',
        ja: '今後のバージョンでローカルDBやブラウザキャッシュによる文書管理を追加予定' },
    '返回工作台': { en: 'Back to Workbench', ja: 'ワークベンチに戻る' },
    '上传新论文': { en: 'Upload New Paper', ja: '新しい論文をアップロード' },
    '当前版本仅保留本次分析结果': { en: 'This version keeps only the current result', ja: '現バージョンは今回の分析結果のみ保持します' },
    '暂无记录': { en: 'No Records', ja: '記録なし' },
    '还没有历史分析记录': { en: 'No analysis history yet', ja: '分析履歴はまだありません' },
    '后续版本可接入 SQLite / IndexedDB，实现持久化历史管理':
      { en: 'A future version may use SQLite / IndexedDB for persistent history',
        ja: '今後 SQLite / IndexedDB で永続的な履歴管理を追加予定' },
    '开始首次分析': { en: 'Run First Analysis', ja: '最初の分析を実行' },
    '分析完成后可生成 Markdown / TXT 报告': { en: 'Generate Markdown / TXT reports after analysis', ja: '分析後に Markdown / TXT レポートを生成できます' },
    '待生成': { en: 'Pending', ja: '生成待ち' },
    '请先完成一次论文分析': { en: 'Run an analysis first', ja: 'まず分析を1回実行してください' },
    '分析结果将在工作台生成后，支持导出为结构化 Markdown 或 TXT 格式报告':
      { en: 'Once analyzed in the workbench, results can be exported as structured Markdown or TXT',
        ja: 'ワークベンチで分析後、構造化 Markdown / TXT レポートとして出力できます' },
    '前往工作台分析': { en: 'Go to Workbench', ja: 'ワークベンチへ' },
    '当前为演示配置，设置项为只读状态': { en: 'Demo config — settings are read-only', ja: 'デモ設定のため、項目は読み取り専用です' },
    // 设置页
    '当前为只读配置 · 实时反映后端运行状态': { en: 'Read-only config · reflects live backend status', ja: '読み取り専用設定・バックエンドの稼働状態をリアルタイム反映' },
    '解析与规则提取均在本地运行': { en: 'Parsing and rule extraction run locally', ja: '解析とルール抽出はローカルで実行' },
    '文件与文本内容不发送至远程服务器': { en: 'Files and text are never sent to remote servers', ja: 'ファイルとテキストはリモートサーバーに送信されません' },
    '扫描件 OCR': { en: 'Scanned-PDF OCR', ja: 'スキャンPDFのOCR' },
    '无文字层 PDF 的本地 OCR 兜底（RapidOCR，不出网）': { en: 'Local OCR fallback for text-less PDFs (RapidOCR, offline)', ja: 'テキスト層のないPDF向けローカルOCR（RapidOCR、オフライン）' },
    '本地大模型（Ollama）': { en: 'Local LLM (Ollama)', ja: 'ローカルLLM（Ollama）' },
    '本机 Ollama 深度分析，纯本地不出网': { en: 'Deep analysis via local Ollama, fully offline', ja: 'ローカルの Ollama で詳細分析、完全オフライン' },
    '云端 API Key': { en: 'Cloud API Key', ja: 'クラウドAPIキー' },
    'DeepSeek 云端 API 密钥（可选，不填则走本地）': { en: 'DeepSeek cloud API key (optional; falls back to local)', ja: 'DeepSeek クラウドAPIキー（任意：未入力ならローカル）' },
    'v4pro 高级模式': { en: 'v4pro Advanced', ja: 'v4pro 上級モード' },
    '资深评审视角的深度分析 · 5 小时滚动配额，最多 5 次':
      { en: 'Senior-reviewer deep analysis · rolling 5-hour quota, up to 5 uses',
        ja: 'ベテラン査読者視点の詳細分析・5時間のローリング枠で最大5回' },
    '调用 DeepSeek API 进行深度分析': { en: 'Calls DeepSeek API for deep analysis', ja: 'DeepSeek API を呼び出して詳細分析' },
    'DeepSeek 云端 API 密钥': { en: 'DeepSeek cloud API key', ja: 'DeepSeek クラウドAPIキー' },
    '演示模式': { en: 'Demo', ja: 'デモ' },
    '可用': { en: 'Available', ja: '利用可能' },
    '不可用': { en: 'Unavailable', ja: '利用不可' },
    '未检测到': { en: 'Not Detected', ja: '未検出' },
    '已配置': { en: 'Configured', ja: '設定済み' },
    '未配置': { en: 'Not Set', ja: '未設定' },
    '需配置 Key': { en: 'Key Required', ja: 'キーが必要' },
    '已锁定': { en: 'Locked', ja: 'ロック中' },
    '检测中…': { en: 'Checking…', ja: '確認中…' },
    '状态获取失败：': { en: 'Status fetch failed: ', ja: '状態取得に失敗：' },
    // 我的文档
    '上传过的论文原件，仅存在本机 · 同名文件只显示最新一份':
      { en: 'Uploaded paper originals, stored locally · only the latest of same-name files is shown',
        ja: 'アップロード済みの論文原本、端末内のみ・同名は最新の1件のみ表示' },
    '还没有上传过文档': { en: 'No documents uploaded yet', ja: 'アップロード済みの文書はありません' },
    '去工作台上传一篇论文，文件会留在这里供管理': { en: 'Upload a paper in the workbench; files will be managed here', ja: 'ワークベンチで論文をアップロードすると、ここで管理できます' },
    '前往工作台': { en: 'Go to Workbench', ja: 'ワークベンチへ' },
    '文档加载失败：': { en: 'Failed to load documents: ', ja: '文書の読み込みに失敗：' },
    '尚未分析': { en: 'Not analyzed', ja: '未分析' },
    '下载原件': { en: 'Download', ja: '原本をダウンロード' },
    '重新分析': { en: 'Re-analyze', ja: '再分析' },
    '删除': { en: 'Delete', ja: '削除' },
    '删除失败': { en: 'Delete failed', ja: '削除に失敗' },
    '删除失败：': { en: 'Delete failed: ', ja: '削除に失敗：' },
    '确认删除这份文档原件？此操作不可撤销（不影响已生成的分析历史）。':
      { en: 'Delete this document original? This cannot be undone (analysis history is unaffected).',
        ja: 'この文書原本を削除しますか？取り消せません（生成済みの分析履歴には影響しません）。' },
    // 分析历史
    '所有记录仅存在本机 · 不上传云端 · 可随时一键清空':
      { en: 'All records are local · never uploaded · clear anytime',
        ja: 'すべての記録は端末内のみ・クラウド送信なし・いつでも一括削除可能' },
    '清空历史': { en: 'Clear History', ja: '履歴を消去' },
    '还没有分析记录': { en: 'No analysis records yet', ja: '分析記録はまだありません' },
    '去工作台上传一篇论文，分析结果会自动留在这里':
      { en: 'Upload a paper in the workbench; results are saved here automatically',
        ja: 'ワークベンチで論文をアップロードすると、結果がここに自動保存されます' },
    '查看': { en: 'View', ja: '表示' },
    '历史加载失败：': { en: 'Failed to load history: ', ja: '履歴の読み込みに失敗：' },
    '本地规则 + 算法': { en: 'Local rules + algorithm', ja: 'ローカルルール＋アルゴリズム' },
    '记录不存在或已删除': { en: 'Record not found or deleted', ja: '記録が存在しないか削除済み' },
    '打开失败：': { en: 'Open failed: ', ja: '開けませんでした：' },
    '确认删除这条分析记录？此操作不可撤销。': { en: 'Delete this analysis record? This cannot be undone.', ja: 'この分析記録を削除しますか？取り消せません。' },
    '确认清空全部分析历史？此操作不可撤销。': { en: 'Clear all analysis history? This cannot be undone.', ja: 'すべての分析履歴を消去しますか？取り消せません。' },
    '清空失败：': { en: 'Clear failed: ', ja: '消去に失敗：' },
    // 报告中心
    '基于分析历史，一键导出 Markdown / TXT 结构化报告 · 全程本地生成':
      { en: 'One-click Markdown / TXT structured reports from history · generated locally',
        ja: '分析履歴から Markdown / TXT 構造化レポートをワンクリック出力・すべてローカル生成' },
    '导出全部（MD）': { en: 'Export All (MD)', ja: 'すべて出力（MD）' },
    '还没有可导出的报告': { en: 'No reports to export yet', ja: '出力できるレポートはまだありません' },
    '先在工作台完成一次论文分析，这里就能导出结构化报告':
      { en: 'Run an analysis in the workbench, then export structured reports here',
        ja: 'ワークベンチで分析を1回行うと、ここで構造化レポートを出力できます' },
    '报告列表加载失败：': { en: 'Failed to load reports: ', ja: 'レポート一覧の読み込みに失敗：' },
    '导出 MD': { en: 'Export MD', ja: 'MD出力' },
    '导出 TXT': { en: 'Export TXT', ja: 'TXT出力' },
    // 结果页
    '分析结果': { en: 'Analysis Result', ja: '分析結果' },
    'AI 分析报告': { en: 'AI Analysis Report', ja: 'AI分析レポート' },
    '未找到解析数据，请': { en: 'No parsed data found, please ', ja: '解析データが見つかりません。' },
    '返回首页': { en: 'return home', ja: 'ホームに戻る' },
    '重新上传。': { en: ' and re-upload.', ja: '再アップロードしてください。' },
    '文本字数': { en: 'Word Count', ja: '文字数' },
    '核心命中': { en: 'Core Hits', ja: 'コア一致' },
    '候选片段': { en: 'Candidates', ja: '候補断片' },
    '待审查': { en: 'To Review', ja: '要確認' },
    '论文体检报告': { en: 'Paper Health Check', ja: '論文ヘルスチェック' },
    '本地算法 · 不上云': { en: 'Local algorithm · offline', ja: 'ローカルアルゴリズム・オフライン' },
    '综合点评': { en: 'Overall Review', ja: '総合評価' },
    '本地生成 · 不上云': { en: 'Generated locally · offline', ja: 'ローカル生成・オフライン' },
    'DeepSeek 结构化分析': { en: 'DeepSeek Structured Analysis', ja: 'DeepSeek 構造化分析' },
    '当前为演示分析结果，用于展示系统报告结构；接入 DeepSeek 后将根据论文内容生成真实分析。':
      { en: 'This is a demo result showing the report structure; with DeepSeek connected it generates real analysis.',
        ja: 'これはレポート構成を示すデモ結果です。DeepSeek 接続後は論文内容に基づく実分析を生成します。' },
    '规则匹配结果': { en: 'Rule Matches', ja: 'ルール一致結果' },
    '导出 Markdown': { en: 'Export Markdown', ja: 'Markdown出力' },
    '← 重新上传': { en: '← Re-upload', ja: '← 再アップロード' },
    '由 PaperCore AI 自动生成 · 仅供学术参考 · 数据未上云':
      { en: 'Auto-generated by PaperCore AI · for academic reference · data not uploaded',
        ja: 'PaperCore AI により自動生成・学術参考用・データ未送信' },
    '处理中...': { en: 'Processing...', ja: '処理中...' },
    '原文对照': { en: 'Source Text', ja: '原文対照' },
    '核心': { en: 'Core', ja: 'コア' },
    '候选': { en: 'Candidate', ja: '候補' },
    '未知文件': { en: 'Unknown file', ja: '不明なファイル' },
    '显著度': { en: 'Salience', ja: '顕著度' },
    '关键词密度兜底': { en: 'Keyword-density fallback', ja: 'キーワード密度フォールバック' },
    '分析引擎': { en: 'Engine', ja: '分析エンジン' },
    '论文': { en: 'Paper', ja: '論文' },
    '分析时间': { en: 'Analyzed at', ja: '分析日時' },
    '模式': { en: 'Mode', ja: 'モード' },
    '未匹配到强规则片段，已展示候选片段（基于关键词密度自动选取）。':
      { en: 'No strong rule matches; showing candidate snippets (auto-selected by keyword density).',
        ja: '強いルール一致がないため、候補断片（キーワード密度で自動選択）を表示しています。' },
    '本地提取结果': { en: 'Local Extraction', ja: 'ローカル抽出結果' },
    '本地规则模式': { en: 'Local rule mode', ja: 'ローカルルールモード' },
    '提取说明': { en: 'Extraction Note', ja: '抽出に関する注記' },
    '本地规则未能识别标准章节结构（摘要/方法/结论）。':
      { en: 'Local rules could not detect standard sections (abstract / methods / conclusion).',
        ja: 'ローカルルールで標準的な章構成（要旨／手法／結論）を検出できませんでした。' },
    '可能原因：PDF 为图像版或章节命名不规范。':
      { en: 'Possible cause: image-based PDF or non-standard section titles.',
        ja: '考えられる原因：画像版PDF、または章タイトルが非標準です。' },
    '请尝试接入 DeepSeek API 进行 AI 分析，或查看下方规则匹配片段。':
      { en: 'Try connecting the DeepSeek API for AI analysis, or see the rule matches below.',
        ja: 'DeepSeek API を接続してAI分析を試すか、下のルール一致断片をご覧ください。' },
    '重点建议': { en: 'Key Suggestions', ja: '重点提案' },
    '改进建议': { en: 'Suggestions', ja: '改善提案' },
    '无数据可导出': { en: 'No data to export', ja: '出力できるデータがありません' },
    '正在生成导出文件...': { en: 'Generating export file...', ja: '出力ファイルを生成中...' },
    '导出失败：': { en: 'Export failed: ', ja: '出力に失敗：' },
    '章节识别失败': { en: 'Section detection failed', ja: '章構成の検出に失敗' },
    '未提取到': { en: 'Not extracted', ja: '抽出できず' },
    // 通用
    '折叠/展开导航': { en: 'Collapse / expand nav', ja: 'ナビの折りたたみ／展開' },
    '收起原文栏': { en: 'Collapse source panel', ja: '原文パネルを閉じる' },
    // 体验区（单节点字串；富文本/带数字的见 KEYS）
    '☁️ 云端体验版': { en: '☁️ Cloud Trial', ja: '☁️ クラウド体験版' },
    '想数据永不离机？用本地版 →': { en: 'Want data to stay on your device? Use the local version →', ja: 'データを端末から出したくない？ローカル版へ →' },
    '云端 · 尝鲜 · 敏感勿传': { en: 'Cloud · Trial · Keep Sensitive Papers Off', ja: 'クラウド・お試し・機密厳禁' },
    '我已了解，开始体验': { en: 'Got it, start exploring', ja: '同意して始める' },
    '今天的免费额度用完啦': { en: 'Today’s free quota is used up', ja: '本日の無料枠を使い切りました' },
    '邮箱仅用于上线通知，不做他用。': { en: 'Email is used only for launch notifications, nothing else.', ja: 'メールは公開通知のみに使用します。' },
    '通知我': { en: 'Notify Me', ja: '通知を受け取る' },
    '先不用，关闭': { en: 'Not now, close', ja: 'あとで' },
  };

  // ===== 富文本 / JS 生成内容的键控翻译（体验区等，下个任务填值）=====
  // 用法：HTML 元素加 data-i18n="键名"（innerHTML 注入，可含 <b>/<a>）；
  //      JS 里用 window.pcI18n.key('键名') 取当前语言译文（带 {占位} 自行 replace）。
  var KEYS = {
    'exp.banner': {
      en: '☁️ Cloud Trial · please <b>don’t upload confidential / private papers</b>',
      ja: '☁️ クラウド体験版・<b>機密／プライベートな論文はアップロードしないでください</b>'
    },
    'exp.disclaimer': {
      en: 'This is the <b>cloud trial</b> of PaperCore — uploaded papers are sent to a cloud LLM for analysis.<br>Please <b>do not upload unpublished drafts, confidential or private papers</b>.<br>Need “data never leaves your device”? Use the <a href="https://github.com/drkiyira-dev/PaperCore" target="_blank" rel="noopener">local open-source version</a>, fully offline.',
      ja: 'これは PaperCore の<b>クラウド体験版</b>です。アップロードした論文はクラウドLLMに送信され分析されます。<br><b>未発表の原稿・機密・プライベートな論文はアップロードしないでください</b>。<br>「データを端末から出さない」なら、<a href="https://github.com/drkiyira-dev/PaperCore" target="_blank" rel="noopener">ローカル版（オープンソース）</a>を完全オフラインでご利用ください。'
    },
    'exp.limit': {
      en: 'The trial allows <b id="expLimitQuota">5</b> free analyses per day.<br>Leave your email and we’ll <b>notify you first when Pro / more quota launches</b>.',
      ja: '体験版は1日 <b id="expLimitQuota">5</b> 件まで無料です。<br>メールを登録いただければ、<b>Pro／追加枠の公開時に最初にお知らせ</b>します。'
    },
    'exp.quota': { zh: '今日免费额度 {n}/{t}', en: 'Free today {n}/{t}', ja: '本日の無料枠 {n}/{t}' },
    'exp.wl.needEmail': { zh: '请先填邮箱', en: 'Please enter your email', ja: 'メールアドレスを入力してください' },
    'exp.wl.ok': { zh: '已记录，上线会第一时间通知你', en: 'Got it — we’ll notify you at launch', ja: '登録しました。公開時に最初にお知らせします' },
    'exp.wl.fail': { zh: '提交失败，稍后再试', en: 'Submission failed, please try again', ja: '送信に失敗しました。後でお試しください' },
    'exp.wl.netErr': { zh: '网络错误，稍后再试', en: 'Network error, please try again', ja: 'ネットワークエラー。後でお試しください' }
  };

  // ===== 引擎 =====
  function tr(s) {
    if (s == null) return s;
    var key = String(s).trim();
    if (!key) return s;
    var e = DICT[key];
    if (!e) return s;
    var out = e[lang];
    if (!out) return s;
    return String(s).replace(key, out); // 保留原文前后空白
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
    if (node.hasAttribute('data-i18n')) applyKey(node);
    for (var c = node.firstChild; c; c = c.nextSibling) translateNode(c);
  }

  // data-i18n 富文本：按键取当前语言译文，innerHTML 注入（zh 时用元素原样，不动）
  function applyKey(el) {
    var k = el.getAttribute('data-i18n');
    var e = KEYS[k];
    if (e && e[lang]) el.innerHTML = e[lang];
  }

  function translateDOM(root) { translateNode(root || document.body); }

  // ===== 顶栏 语言选择器（自动注入每页 .topbar）=====
  var LABELS = { zh: '中文', en: 'EN', ja: '日本語' };
  function injectSwitch() {
    var bars = document.querySelectorAll('.topbar');
    bars.forEach(function (bar) {
      if (bar.querySelector('.lang-switch')) return;
      var sel = document.createElement('select');
      sel.className = 'lang-switch';
      sel.title = 'Language / 语言';
      sel.style.cssText = 'margin-left:8px;padding:3px 6px;border-radius:7px;border:1px solid var(--color-border,#ddd);background:#fff;color:inherit;font-size:12px;cursor:pointer';
      LANGS.forEach(function (l) {
        var o = document.createElement('option');
        o.value = l; o.textContent = LABELS[l];
        if (l === lang) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', function () { setLang(sel.value); });
      var badges = bar.querySelector('.status-badges');
      if (badges) badges.appendChild(sel); else bar.appendChild(sel);
    });
  }

  function setLang(l) {
    if (LANGS.indexOf(l) < 0) l = 'zh';
    localStorage.setItem(LANG_KEY, l);
    location.reload(); // 切换=重载：永不需要反向还原，最稳
  }
  window.setPaperCoreLang = setLang; // 供设置页按钮调用

  // 供 main.js 等取当前语言/译文（JS 生成内容用，如带数字的配额标）
  window.pcI18n = {
    get lang() { return lang; },
    key: function (k) { var e = KEYS[k]; return (e && (e[lang] || e.en)) || ''; },
    t: tr
  };

  // ===== 启动 =====
  function boot() {
    document.documentElement.setAttribute('lang', lang === 'ja' ? 'ja' : (lang === 'en' ? 'en' : 'zh-CN'));
    injectSwitch();
    if (lang !== 'zh') {
      translateDOM(document.body);
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
