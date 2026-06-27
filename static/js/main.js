// === State ===
let selectedFile = null;
let preloadedDoc = null;   // 「我的文档」带过来的已上传文件名（?doc=），与 selectedFile 互斥
let selectedMode = 'quick';
let selectedScore = 'teacher';   // 论文体检评分尺度：teacher（默认）/ expert / professor
let loadingTimer = null;
let logTimer = null;

// === Check backend API key status ===
let serverKeyConfigured = false;

function applyStatus(data) {
    if (!data) return;
    if (data.api_key_configured) {
        serverKeyConfigured = true;
        const input = document.getElementById('apiKeyInput');
        const model = document.querySelector('.api-key-model');
        if (input) {
            input.placeholder = '已从服务器环境变量读取，无需填写';
            input.style.color = 'var(--color-green)';
        }
        if (model) {
            model.textContent = '✓ 已配置';
            model.style.color = 'var(--color-green)';
        }
        updateSettingsUI(true);
    }
    if (data.ollama_available) {
        const sec = document.getElementById('localAiSection');
        const desc = document.getElementById('localAiDesc');
        if (sec) sec.style.display = 'block';
        if (desc && data.ollama_model) {
            desc.textContent = `不填 Key，调用本机 ${data.ollama_model} 做深度分析 · 纯本地不出网（较慢）`;
        }
    }
    applyV4ProStatus(data);
}

// 把「解锁时刻(epoch 秒)」格式化成绝对时间，如 "3:45 PM"（精确到分钟，AM/PM）
function fmtRecoverTime(epochSec) {
    const d = new Date(epochSec * 1000);
    let h = d.getHours();
    const m = String(d.getMinutes()).padStart(2, '0');
    const ap = h < 12 ? 'AM' : 'PM';
    h = h % 12; if (h === 0) h = 12;
    return `${h}:${m} ${ap}`;
}

function applyV4ProStatus(data) {
    if (!data || !data.v4pro_available) return;
    const sec    = document.getElementById('v4proSection');
    const quota  = data.v4pro_quota || {};
    const toggle = document.getElementById('v4proToggle');
    const input  = document.getElementById('useV4ProInput');
    const qEl    = document.getElementById('v4proQuota');
    const desc   = document.getElementById('v4proDesc');
    if (!sec) return;
    sec.style.display = 'block';
    if (qEl) qEl.textContent = `${quota.remaining ?? '—'} / ${quota.limit ?? 5}`;

    const locked = quota.can_use === false;
    if (toggle) toggle.classList.toggle('locked', locked);
    if (input) {
        input.disabled = locked;
        if (locked) input.checked = false;
    }
    if (locked && quota.locked_until) {
        const t = fmtRecoverTime(quota.locked_until);   // 绝对恢复时间（AM/PM，精确到分），不做倒计时
        if (desc) desc.textContent = `今日额度已用尽 · 预计 ${t} 恢复`;
        if (toggle) toggle.title = `配额耗尽，预计 ${t} 恢复`;
    } else if (desc) {
        desc.textContent = '资深评审视角，更深更长的结构化分析 · 5 小时内限用 5 次';
        if (toggle) toggle.title = '';
    }
}

fetch('/api/status').then(r => r.json()).then(j => applyStatus(j.data)).catch(() => {});
// 5 秒轮询 v4pro 配额：保持倒计时活，到期自动解锁，无需手动刷新
setInterval(() => {
    fetch('/api/status').then(r => r.json()).then(j => applyV4ProStatus(j.data)).catch(() => {});
}, 5000);

// === Settings UI — 根据当前是否启用 AI 动态更新隐私描述 ===
function updateSettingsUI(aiEnabled) {
    const privacyDesc   = document.getElementById('settingPrivacyDesc');
    const privacyToggle = document.getElementById('settingPrivacyToggle');
    const aiToggle      = document.getElementById('settingAIToggle');
    const apiKeyToggle  = document.getElementById('settingAPIKeyToggle');
    const topbarBadge   = document.getElementById('topbarPrivacyBadge');
    const topbarStatus  = document.getElementById('topbarPrivacyStatus');

    if (aiEnabled) {
        if (privacyDesc)   privacyDesc.textContent = 'AI 模式：文本将发送至 DeepSeek API（HTTPS 加密传输）';
        if (privacyToggle) { privacyToggle.textContent = '部分'; privacyToggle.className = 'settings-toggle settings-toggle--demo'; }
        if (aiToggle)      { aiToggle.textContent = '已启用';   aiToggle.className = 'settings-toggle settings-toggle--on'; }
        if (apiKeyToggle)  { apiKeyToggle.textContent = '已配置'; apiKeyToggle.className = 'settings-toggle settings-toggle--on'; }
        if (topbarBadge)   topbarBadge.textContent = 'AI 模式';
        if (topbarStatus)  { topbarStatus.textContent = '数据经 API 上云'; topbarStatus.classList.remove('badge-green'); }
    } else {
        if (privacyDesc)   privacyDesc.textContent = '文件与文本内容不发送至远程服务器';
        if (privacyToggle) { privacyToggle.textContent = '开启'; privacyToggle.className = 'settings-toggle settings-toggle--on'; }
        if (aiToggle)      { aiToggle.textContent = '演示模式'; aiToggle.className = 'settings-toggle settings-toggle--demo'; }
        if (apiKeyToggle)  { apiKeyToggle.textContent = '未配置'; apiKeyToggle.className = 'settings-toggle settings-toggle--off'; }
        if (topbarBadge)   topbarBadge.textContent = '数据不上云';
        if (topbarStatus)  { topbarStatus.textContent = '隐私保护中'; topbarStatus.classList.add('badge-green'); }
    }
}

// === Mode Selector ===
document.querySelectorAll('#modeSelector .mode-card').forEach(card => {
    card.addEventListener('click', () => {
        document.querySelectorAll('#modeSelector .mode-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        selectedMode = card.dataset.mode;
    });
});

// 评分尺度（老师/专家/教授），与分析模式各管各的，按容器隔离选中态
// 老师档专属「分数上限」滑块：仅老师档显示
const teacherCapRow = document.getElementById('teacherCapRow');
const teacherCapSlider = document.getElementById('teacherCapSlider');
const teacherCapValue = document.getElementById('teacherCapValue');
function syncTeacherCapRow() {
    if (teacherCapRow) teacherCapRow.style.display = (selectedScore === 'teacher') ? 'flex' : 'none';
}
if (teacherCapSlider && teacherCapValue) {
    const updateCap = () => {
        teacherCapValue.textContent = teacherCapSlider.value;
        const pct = (teacherCapSlider.value - 70) / 30 * 100;   // 70~100 映射 0~100% 填充
        teacherCapSlider.style.setProperty('--cap-fill', pct + '%');
    };
    teacherCapSlider.addEventListener('input', updateCap);
    updateCap();   // 初始化填充段
}
syncTeacherCapRow();   // 默认老师档 → 显示滑块
document.querySelectorAll('#scoreSelector .mode-card').forEach(card => {
    card.addEventListener('click', () => {
        document.querySelectorAll('#scoreSelector .mode-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        selectedScore = card.dataset.score;
        syncTeacherCapRow();
    });
});

// === Upload Area ===
const uploadArea  = document.getElementById('uploadArea');
const fileInput   = document.getElementById('fileInput');
const fileSelected = document.getElementById('fileSelected');
const fileNameEl  = document.getElementById('fileName');
const fileSizeEl  = document.getElementById('fileSize');

if (uploadArea) {
    uploadArea.addEventListener('click', (e) => {
        if (e.target.closest('label')) return;
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) setFile(e.target.files[0]);
    });

    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('drag-over');
    });

    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('drag-over');
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) setFile(e.dataTransfer.files[0]);
    });
}

// === 从「我的文档」预载（?doc=safe_filename）===
// 浏览器安全限制：JS 无法把磁盘文件塞回 <input type=file>。但文件本就在服务器
// uploads/ 里，这里只需记住它的 safe 文件名，分析时改走 reanalyze 接口（不重新上传）。
(function initPreloadFromDoc() {
    const doc = new URLSearchParams(location.search).get('doc');
    if (!doc) return;
    preloadedDoc = doc;
    const display = doc.replace(/^\d+_/, '');   // 去掉 {timestamp}_ 前缀，还原展示名
    if (fileNameEl) fileNameEl.textContent = display;
    if (fileSizeEl) fileSizeEl.textContent = '来自「我的文档」';
    if (fileSelected) fileSelected.style.display = 'flex';
    const hint = document.querySelector('.upload-hint');
    if (hint) hint.textContent = '已从「我的文档」载入，选好模式后点「开始分析」即可（无需重新上传）';
    const title = document.querySelector('.upload-title');
    if (title) title.textContent = '重新分析已有文档';
    previewPanelFileReady(display);
})();

// API key 输入框变化时同步更新设置页
const _apiKeyInput = document.getElementById('apiKeyInput');
if (_apiKeyInput) {
    _apiKeyInput.addEventListener('input', () => {
        const hasKey = !!(_apiKeyInput.value.trim() || serverKeyConfigured);
        updateSettingsUI(hasKey);
    });
}

function setFile(file) {
    const validExt = ['.pdf', '.docx', '.txt', '.md'];
    if (!validExt.some(ext => file.name.toLowerCase().endsWith(ext))) {
        showError('不支持的格式，请上传 PDF / DOCX / TXT / Markdown');
        return;
    }
    selectedFile = file;
    if (fileNameEl) fileNameEl.textContent = file.name;
    if (fileSizeEl) fileSizeEl.textContent = formatSize(file.size);
    if (fileSelected) fileSelected.style.display = 'flex';
    hideError();
    previewPanelFileReady(file.name);
}

function formatSize(bytes) {
    if (bytes < 1024)        return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// === AI Preview Panel ===
function previewPanelFileReady(filename) {
    const idle      = document.getElementById('aipIdle');
    const analyzing = document.getElementById('aipAnalyzing');
    const logStream = document.getElementById('aipLogStream');
    if (!idle || !analyzing || !logStream) return;

    idle.style.display = 'none';
    analyzing.style.display = 'flex';
    logStream.innerHTML = '';

    const readyLogs = [
        { text: `已检测文件: ${filename}`, type: 'done' },
        { text: '等待开始分析...', type: 'active' },
    ];
    readyLogs.forEach((l, i) => {
        setTimeout(() => appendLog(logStream, l.text, l.type), i * 300);
    });
}

function previewPanelAnalyzing(usingAI) {
    const logStream = document.getElementById('aipLogStream');
    if (!logStream) return;
    logStream.innerHTML = '';

    const steps = usingAI ? [
        { text: '正在解析文档结构...', type: 'active' },
        { text: '提取文本与表格内容...', type: '' },
        { text: '识别论文章节划分...', type: '' },
        { text: '定位实验数据与公式...', type: '' },
        { text: 'DeepSeek 正在生成结构化分析...', type: '' },
        { text: '构建最终报告...', type: '' },
    ] : [
        { text: '正在解析文档结构...', type: 'active' },
        { text: '提取文本与表格内容...', type: '' },
        { text: '规则匹配与内容评分...', type: '' },
        { text: '提取核心句子与公式...', type: '' },
        { text: '生成结构化报告...', type: '' },
    ];

    if (logTimer) clearInterval(logTimer);
    let cur = 0;

    const tick = () => {
        if (cur >= steps.length) { clearInterval(logTimer); return; }
        if (cur > 0) {
            const prev = logStream.querySelectorAll('.aip-log-line')[cur - 1];
            if (prev) {
                const t = prev.querySelector('.log-text');
                if (t) { t.classList.remove('active'); t.classList.add('done'); }
            }
        }
        appendLog(logStream, steps[cur].text, cur === 0 ? 'active' : 'active');
        cur++;
    };

    tick();
    logTimer = setInterval(tick, 1200);
}

function appendLog(container, text, type) {
    const now = new Date();
    const ts = now.toTimeString().slice(0, 8);
    const line = document.createElement('div');
    line.className = 'aip-log-line';
    line.innerHTML = `<span class="log-ts">${ts}</span><span class="log-text ${type}">${text}</span>`;
    container.appendChild(line);
    container.scrollTop = container.scrollHeight;
}

// === Start Analysis ===
const btnStart = document.getElementById('btnStart');
if (btnStart) btnStart.addEventListener('click', startAnalysis);

function startAnalysis() {
    if (!selectedFile && !preloadedDoc) {
        showError('请先上传一篇论文文件');
        return;
    }
    hideError();

    const apiKey  = (document.getElementById('apiKeyInput')?.value || '').trim();
    // v4pro 高级模式：勾选且未被锁定时启用（优先级最高，后端会再校验配额）
    const v4Input = document.getElementById('useV4ProInput');
    const useV4Pro = !!(v4Input && v4Input.checked && !v4Input.disabled);
    // 本地大模型：仅当没用 v4pro / 没云端 Key 且勾选了开关时启用
    const useLocalAi = !useV4Pro && !apiKey && !serverKeyConfigured
        && !!document.getElementById('useLocalAiInput')?.checked;
    const usingAI = !!(useV4Pro || apiKey || serverKeyConfigured || useLocalAi);

    const formData = new FormData();
    formData.append('analysis_mode', selectedMode);
    formData.append('score_mode', selectedScore);
    if (selectedScore === 'teacher' && teacherCapSlider) formData.append('teacher_cap', teacherCapSlider.value);
    if (useV4Pro) formData.append('use_v4pro', '1');
    if (apiKey) formData.append('api_key', apiKey);
    if (useLocalAi) formData.append('use_local_ai', '1');

    // 两种来源：新上传走 /api/upload（带 file）；从「我的文档」预载走 reanalyze
    // （文件已在服务器 uploads/，只发文件名 + 参数，不重新传文件本体）。
    let endpoint;
    if (preloadedDoc) {
        endpoint = `/api/documents/${encodeURIComponent(preloadedDoc)}/reanalyze`;
    } else {
        formData.append('file', selectedFile);
        endpoint = '/api/upload';
    }

    showLoading(usingAI);
    previewPanelAnalyzing(usingAI);

    const MIN_LOADING_MS = 5000;
    const fetchPromise = fetch(endpoint, { method: 'POST', body: formData }).then(r => r.json());
    const minTimer = new Promise(resolve => setTimeout(resolve, MIN_LOADING_MS));

    Promise.all([fetchPromise, minTimer])
        .then(([data]) => {
            clearLoadingTimer();
            hideLoading();
            if (data.code === 200) {
                sessionStorage.setItem('uploadResult', JSON.stringify(data.data));
                window.location.href = '/result';
            } else {
                showError('处理失败：' + data.msg);
            }
        })
        .catch(err => {
            clearLoadingTimer();
            hideLoading();
            showError('请求失败：' + err.message);
        });
}

// === Loading States ===
const STEPS_AI = [
    '正在解析论文结构...',
    '正在识别实验数据...',
    'DeepSeek 正在生成结构化分析...',
];

const STEPS_LOCAL = [
    '正在解析论文结构...',
    '规则匹配与内容提取...',
];

function showLoading(usingAI) {
    const overlay = document.getElementById('loadingOverlay');
    if (!overlay) return;
    overlay.style.display = 'flex';

    const steps = usingAI ? STEPS_AI : STEPS_LOCAL;
    const container = document.getElementById('loadingSteps');
    if (container) {
        container.innerHTML = steps.map((text, i) =>
            `<div class="loading-step" id="lstep${i}"><div class="step-dot"></div><span>${text}</span></div>`
        ).join('');
    }

    let cur = 0;
    setStepActive(0);

    const interval = 1500;
    loadingTimer = setInterval(() => {
        setStepDone(cur);
        cur++;
        if (cur < steps.length) {
            setStepActive(cur);
        } else {
            clearLoadingTimer();
        }
    }, interval);
}

function setStepActive(i) {
    const el = document.getElementById('lstep' + i);
    if (el) el.classList.add('active');
}

function setStepDone(i) {
    const el = document.getElementById('lstep' + i);
    if (el) { el.classList.remove('active'); el.classList.add('done'); }
}

function clearLoadingTimer() {
    if (loadingTimer) { clearInterval(loadingTimer); loadingTimer = null; }
    if (logTimer)     { clearInterval(logTimer);     logTimer = null; }
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.style.display = 'none';
}

// === Error ===
function showError(msg) {
    const el = document.getElementById('errorMessage');
    if (el) { el.textContent = msg; el.style.display = 'block'; }
}

function hideError() {
    const el = document.getElementById('errorMessage');
    if (el) el.style.display = 'none';
}

// === Page Switcher ===
const PAGE_MAP = {
    workbench: 'page-workbench',
    docs:      'page-docs',
    history:   'page-history',
    report:    'page-report',
    settings:  'page-settings',
};

function switchPage(key) {
    Object.entries(PAGE_MAP).forEach(([k, id]) => {
        const panel = document.getElementById(id);
        if (panel) panel.style.display = (k === key) ? 'contents' : 'none';
    });
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
        item.classList.toggle('active', item.dataset.page === key);
    });
}

document.querySelectorAll('.nav-item[data-page]').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        switchPage(item.dataset.page);
    });
});
