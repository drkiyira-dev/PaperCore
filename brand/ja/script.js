/* ===========================================================
   Paper Core — Brand ad page · interactions (WARM)
   Vanilla JS, no framework. Performance-first:
   transform/opacity only, one rAF scroll loop, IntersectionObserver.
   =========================================================== */
(function () {
  "use strict";

  var reduce = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var isTouch = (window.matchMedia && window.matchMedia("(hover: none)").matches) ||
    ("ontouchstart" in window);
  var hasIO = "IntersectionObserver" in window;
  var PARTICLE = "168,97,31"; /* warm dust */

  /* ---------- 0 · splash ---------- */
  (function splash() {
    var el = document.getElementById("splash");
    if (!el) return;
    if (reduce) { el.parentNode && el.parentNode.removeChild(el); return; }
    var root = document.documentElement, body = document.body, closed = false;
    root.classList.add("splash-lock"); body.classList.add("splash-lock");
    function close() {
      if (closed) return; closed = true;
      el.classList.add("up");
      root.classList.remove("splash-lock"); body.classList.remove("splash-lock");
      setTimeout(function () { el.parentNode && el.parentNode.removeChild(el); }, 1100);
    }
    setTimeout(close, 2450);
    el.addEventListener("click", close);
  })();

  /* ---------- 1 · single rAF scroll loop ---------- */
  var jobs = [], queued = false;
  function schedule() { if (queued) return; queued = true; requestAnimationFrame(run); }
  function run() { queued = false; var y = window.scrollY || window.pageYOffset; for (var i = 0; i < jobs.length; i++) jobs[i](y); }
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule, { passive: true });

  // nav
  var nav = document.getElementById("nav");
  if (nav) jobs.push(function (y) { nav.classList.toggle("scrolled", y > 24); });

  // progress bar
  var bar = document.getElementById("progressBar");
  if (bar) jobs.push(function (y) {
    var h = document.documentElement.scrollHeight - window.innerHeight;
    bar.style.transform = "scaleX(" + (h > 0 ? Math.min(1, y / h) : 0) + ")";
  });

  // parallax (uses individual `translate` so it composes with keyframe transforms)
  if (!reduce) {
    var px = document.querySelectorAll("[data-parallax]");
    if (px.length) jobs.push(function () {
      var vh = window.innerHeight;
      px.forEach(function (el) {
        var f = parseFloat(el.getAttribute("data-parallax")) || 0;
        var r = el.getBoundingClientRect();
        var center = r.top + r.height / 2 - vh / 2;
        el.style.translate = "0 " + (-center * f * 0.12).toFixed(1) + "px";
      });
    });
  }

  // how-it-works scrollytelling
  var howTrack = document.querySelector(".how-track");
  var howVisual = document.getElementById("howVisual");
  var howSteps = document.querySelectorAll(".how-step");
  var howRailFill = document.getElementById("howRailFill");
  var howHint = document.getElementById("howHint");
  if (howSteps[0]) howSteps[0].classList.add("on");
  if (howTrack && howVisual && !reduce) {
    jobs.push(function () {
      var rect = howTrack.getBoundingClientRect();
      var total = howTrack.offsetHeight - window.innerHeight;
      var p = total > 0 ? Math.max(0, Math.min(1, -rect.top / total)) : 0;
      if (howRailFill) howRailFill.style.transform = "scaleY(" + p.toFixed(4) + ")";
      if (howHint) howHint.classList.toggle("gone", p > 0.04);
      var step = Math.min(3, Math.floor(p * 4));
      if (howVisual.getAttribute("data-active") !== String(step)) {
        howVisual.setAttribute("data-active", String(step));
        for (var i = 0; i < howSteps.length; i++)
          howSteps[i].classList.toggle("on", howSteps[i].getAttribute("data-step") === String(step));
      }
    });
  } else if (reduce) {
    for (var s = 0; s < howSteps.length; s++) howSteps[s].classList.add("on");
    if (howRailFill) howRailFill.style.transform = "scaleY(1)";
    if (howHint) howHint.classList.add("gone");
  }
  run(); // prime initial state

  /* ---------- 2 · scroll reveal ---------- */
  var revealEls = document.querySelectorAll(".reveal, [data-reveal]");
  if (reduce || !hasIO) {
    revealEls.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          var sibs = e.target.parentElement
            ? e.target.parentElement.querySelectorAll(":scope > .reveal") : [];
          var idx = Array.prototype.indexOf.call(sibs, e.target);
          e.target.style.transitionDelay = (idx > 0 ? idx * 80 : 0) + "ms";
          e.target.classList.add("in");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.14, rootMargin: "0px 0px -8% 0px" });
    revealEls.forEach(function (el) { io.observe(el); });
  }

  /* ---------- 3 · stats count-up ---------- */
  function countUp(el) {
    var to = parseFloat(el.getAttribute("data-to")) || 0;
    var dec = parseInt(el.getAttribute("data-dec") || "0", 10);
    var suf = el.getAttribute("data-suf") || "";
    if (reduce) { el.textContent = to.toFixed(dec) + suf; return; }
    var dur = 1400, t0 = null;
    function step(ts) {
      if (!t0) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (to * eased).toFixed(dec) + suf;
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = to.toFixed(dec) + suf;
    }
    requestAnimationFrame(step);
  }
  var nums = document.querySelectorAll(".stat-num");
  if (!hasIO) { nums.forEach(countUp); }
  else {
    var nio = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { countUp(e.target); nio.unobserve(e.target); } });
    }, { threshold: 0.6 });
    nums.forEach(function (el) { nio.observe(el); });
  }

  /* ---------- 4 · pipeline light-up ---------- */
  var flow = document.getElementById("pipeFlow");
  if (flow) {
    var lightPipe = function () {
      Array.prototype.forEach.call(flow.children, function (ch, i) {
        if (reduce) { ch.classList.add("on"); return; }
        setTimeout(function () { ch.classList.add("on"); }, i * 200);
      });
    };
    if (!hasIO) lightPipe();
    else {
      var pio = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { lightPipe(); pio.disconnect(); } });
      }, { threshold: 0.3 });
      pio.observe(flow);
    }
  }

  /* ---------- 5 · hero spotlight ---------- */
  var hero = document.querySelector(".hero");
  var spot = document.getElementById("spotlight");
  if (hero && spot && !isTouch && !reduce) {
    hero.addEventListener("pointermove", function (e) {
      if (e.pointerType && e.pointerType !== "mouse") return;
      var r = hero.getBoundingClientRect();
      spot.style.setProperty("--mx", ((e.clientX - r.left) / r.width * 100) + "%");
      spot.style.setProperty("--my", ((e.clientY - r.top) / r.height * 100) + "%");
    });
  }

  /* ---------- 6 · 3D tilt ---------- */
  if (!isTouch && !reduce) {
    document.querySelectorAll(".tilt").forEach(function (el) {
      el.addEventListener("pointerenter", function () { el.style.transition = "transform .12s ease-out"; });
      el.addEventListener("pointermove", function (e) {
        if (e.pointerType && e.pointerType !== "mouse") return;
        var r = el.getBoundingClientRect();
        var rx = (((e.clientY - r.top) / r.height) - 0.5) * -6;
        var ry = (((e.clientX - r.left) / r.width) - 0.5) * 6;
        el.style.transform = "perspective(900px) rotateX(" + rx.toFixed(2) + "deg) rotateY(" + ry.toFixed(2) + "deg) translateY(-4px)";
      });
      el.addEventListener("pointerleave", function () { el.style.transition = ""; el.style.transform = ""; });
    });
  }

  /* ---------- 7 · before / after slider ---------- */
  var baStage = document.getElementById("baStage");
  if (baStage) {
    var dragging = false, rafQ = false, lastX = 0, baRect = null;
    function applySplit() {
      rafQ = false;
      var r = baRect || baStage.getBoundingClientRect();
      var pct = ((lastX - r.left) / r.width) * 100;
      pct = Math.max(4, Math.min(96, pct));
      baStage.style.setProperty("--split", pct + "%");
    }
    function setSplit(clientX) {
      lastX = clientX;
      if (rafQ) return;
      rafQ = true;
      requestAnimationFrame(applySplit);
    }
    baStage.addEventListener("pointerdown", function (e) {
      dragging = true;
      baRect = baStage.getBoundingClientRect();
      try { baStage.setPointerCapture(e.pointerId); } catch (x) {}
      setSplit(e.clientX);
    });
    baStage.addEventListener("pointermove", function (e) { if (dragging) setSplit(e.clientX); });
    function endDrag() { dragging = false; baRect = null; }
    baStage.addEventListener("pointerup", endDrag);
    baStage.addEventListener("pointercancel", endDrag);
  }

  /* ---------- 8 · hero particles (paper dust) ---------- */
  var canvas = document.getElementById("heroCanvas");
  if (canvas && !reduce) {
    var ctx = canvas.getContext("2d");
    var DPR = Math.min(window.devicePixelRatio || 1, 2);
    var W = 0, H = 0, parts = [], raf = null;
    function size() {
      var r = canvas.getBoundingClientRect();
      W = r.width; H = r.height;
      canvas.width = W * DPR; canvas.height = H * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      var count = Math.round(Math.min(80, (W * H) / 16000));
      if (W < 640) count = Math.round(count * 0.5);
      parts = [];
      for (var i = 0; i < count; i++) parts.push({
        x: Math.random() * W, y: Math.random() * H,
        r: Math.random() * 1.8 + 0.5,
        vx: (Math.random() - 0.5) * 0.18,
        vy: -(Math.random() * 0.28 + 0.06),
        a: Math.random() * 0.4 + 0.12
      });
    }
    function frame() {
      ctx.clearRect(0, 0, W, H);
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.x += p.vx; p.y += p.vy;
        if (p.y < -6) { p.y = H + 6; p.x = Math.random() * W; }
        if (p.x < -6) p.x = W + 6; else if (p.x > W + 6) p.x = -6;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, 6.283);
        ctx.fillStyle = "rgba(" + PARTICLE + "," + p.a + ")";
        ctx.fill();
      }
      raf = requestAnimationFrame(frame);
    }
    function startP() { if (!raf) frame(); }
    function stopP() { if (raf) { cancelAnimationFrame(raf); raf = null; } }
    size(); startP();
    var rt;
    window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(size, 200); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) stopP(); else startP(); });
  }

  /* ---------- 9 · demo sequence (cycles through several papers) ---------- */
  var stage = document.querySelector(".demo-stage");
  var typeTarget = document.getElementById("typeTarget");
  var chipsBox = document.getElementById("kwChips");
  var dstatus = document.getElementById("outStatus");
  var formulaOut = document.getElementById("formulaOut");
  var paperTitle = document.querySelector(".paper-title");
  var paperAuth = document.querySelector(".paper-auth");
  var paperFx = document.querySelector(".paper-formula");
  var demoDots = document.getElementById("demoDots");

  var DEMOS = [
    {
      title: "長文検索のための<br>注意機構ガイド付きスパース Transformer",
      auth: "田中 健・鈴木 彩・佐藤 玲 · 2025",
      paperFx: "A(Q,K,V)=softmax(QKᵀ⁄√d<sub>k</sub>)·M&thinsp;V",
      summary: "本論文は、長文検索を O(n log n) にスケールさせるスパース注意機構を提案する。密なベースラインから再現率を 0.6 ポイント以内に保ちつつ、クエリのレイテンシを 4.1 倍削減した。",
      keywords: ["スパース注意", "長文検索", "O(n log n)", "4.1倍高速", "再現率≈密", "Transformer"],
      outFx: 'Attention(Q,K,V)=softmax<span class="paren">(</span><span class="frac"><span class="num">QK<sup>T</sup></span><span class="den">√d<sub>k</sub></span></span><span class="paren">)</span>&thinsp;M&thinsp;V'
    },
    {
      title: "低リソースなタンパク質構造予測のための<br>対照事前学習",
      auth: "山本 大樹・中村 優 · 2024",
      paperFx: "ℒ = −log( exp(s<sup>+</sup>⁄τ) ⁄ Σ exp(s<sup>i</sup>⁄τ) )",
      summary: "対照学習の目的関数でラベルなし配列上で折り畳みモデルを事前学習し、低リソースなファミリーで精度を 12.7 GDT-TS 向上。8 分の 1 のラベルデータで教師ありベースラインに匹敵した。",
      keywords: ["対照学習", "タンパク質構造予測", "低リソース", "+12.7 GDT-TS", "データ1/8", "自己教師あり"],
      outFx: 'ℒ = −log<span class="paren">(</span><span class="frac"><span class="num">exp(s<sup>+</sup>/τ)</span><span class="den">Σ exp(s<sup>i</sup>/τ)</span></span><span class="paren">)</span>'
    },
    {
      title: "逆問題イメージングのための<br>拡散事前分布",
      auth: "小林 翔・渡辺 杏 · 2025",
      paperFx: "x<sub>t</sub> = √α̅<sub>t</sub>·x<sub>0</sub> + √(1−α̅<sub>t</sub>)·ε",
      summary: "事前学習済みの拡散事前分布を再構成ループに組み込むことで、タスク固有の再学習なしに、全変動法より 3.2 dB 高い PSNR で劣化したスキャン画像を復元した。",
      keywords: ["拡散事前分布", "逆問題", "画像復元", "+3.2 dB PSNR", "再学習ゼロ", "ベイズ"],
      outFx: 'x<sub>t</sub> = √α̅<sub>t</sub>&thinsp;x<sub>0</sub> + √(1−α̅<sub>t</sub>)&thinsp;ε'
    }
  ];

  var demoIdx = 0, demoInView = false, demoStarted = false, demoTimer = null;

  function clearDemoTimer() { if (demoTimer) { clearTimeout(demoTimer); demoTimer = null; } }
  function setDots(i) {
    if (!demoDots) return;
    for (var k = 0; k < demoDots.children.length; k++)
      demoDots.children[k].classList.toggle("on", k === i);
  }
  function revealOut() {
    document.querySelectorAll(".out-label.delay-kw,.out-label.delay-fx,.chips,.out-formula")
      .forEach(function (el) { el.classList.add("reveal-out"); });
  }
  function swapPaper(d) {
    if (paperTitle) paperTitle.innerHTML = d.title;
    if (paperAuth) paperAuth.textContent = d.auth;
    if (paperFx) paperFx.innerHTML = d.paperFx;
  }
  function replayScan() {
    if (!stage) return;
    stage.classList.remove("run");
    void stage.offsetWidth;          // force reflow so the scan animation restarts
    stage.classList.add("run");
  }

  function runOne(idx) {
    clearDemoTimer();
    var d = DEMOS[idx];
    demoIdx = idx;
    setDots(idx);
    swapPaper(d);
    if (dstatus) { dstatus.textContent = "● 分析中"; dstatus.classList.remove("done"); }
    chipsBox.innerHTML = "";
    if (formulaOut) { formulaOut.classList.remove("reveal-out"); formulaOut.innerHTML = ""; }
    revealOut();
    replayScan();

    var i = 0;
    typeTarget.innerHTML = '<span class="caret"></span>';
    var caret = typeTarget.querySelector(".caret");
    demoTimer = setTimeout(function tick() {
      if (i <= d.summary.length) {
        typeTarget.textContent = d.summary.slice(0, i);
        typeTarget.appendChild(caret);
        i++;
        demoTimer = setTimeout(tick, 14 + Math.random() * 20);
      } else {
        caret.remove();
        d.keywords.forEach(function (k, n) {
          var c = document.createElement("span");
          c.className = "chip"; c.textContent = k;
          chipsBox.appendChild(c);
          setTimeout(function () { c.classList.add("in"); }, 100 + n * 90);
        });
        var tail = 100 + d.keywords.length * 90;
        setTimeout(function () { if (formulaOut) { formulaOut.innerHTML = d.outFx; formulaOut.classList.add("reveal-out"); } }, tail);
        setTimeout(function () { if (dstatus) { dstatus.textContent = "● 完了"; dstatus.classList.add("done"); } }, tail + 200);
        demoTimer = setTimeout(advance, 3000);   // hold, then next paper
      }
    }, 700);
  }
  function advance() {
    if (reduce) return;
    if (!demoInView) { demoTimer = setTimeout(advance, 1000); return; }   // pause while off-screen
    runOne((demoIdx + 1) % DEMOS.length);
  }

  function runDemoStatic() {
    var d = DEMOS[0];
    swapPaper(d);
    typeTarget.textContent = d.summary;
    d.keywords.forEach(function (k) {
      var c = document.createElement("span"); c.className = "chip in"; c.textContent = k; chipsBox.appendChild(c);
    });
    if (formulaOut) formulaOut.innerHTML = d.outFx;
    revealOut(); setDots(0);
    if (dstatus) { dstatus.textContent = "● 完了"; dstatus.classList.add("done"); }
  }

  if (stage && typeTarget) {
    if (reduce || !hasIO) {
      runDemoStatic();
    } else {
      var dio = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          demoInView = e.isIntersecting;
          if (e.isIntersecting && !demoStarted) { demoStarted = true; runOne(0); }
        });
      }, { threshold: 0.35 });
      dio.observe(stage);
    }
  }
})();
