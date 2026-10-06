/* =============================================================
 * background.js · 时间感知背景引擎
 * -------------------------------------------------------------
 * · 四套程序化场景：清晨 / 白天 / 黄昏 / 夜晚
 * · auto 模式按本地时间自动切换；manual 模式由用户锁定
 * · 场景之间做数值插值（颜色 / 天体位置 / 星尘密度），过渡平滑
 * · 支持用自己的照片替换程序化天空（config.background.photos）
 * · 尊重 prefers-reduced-motion：只渲染静帧，不跑动画
 * 对外接口：window.SiteBackground
 *   .init(root) .setScene(key,{manual}) .setMode('auto'|'manual')
 *   .getState() .SCENES .todayScene()
 * 事件：document 上派发 'site:scenechange'（detail: {scene, mode, source}）
 * ============================================================= */
(function () {
  'use strict';

  /* 配置在 init() 时才读取：后台配置是先 fetch 回来再初始化的 */
  function bgCfg() { return (window.SITE_CONFIG && window.SITE_CONFIG.background) || {}; }

  /* ---------------- 场景定义 ---------------- */
  var SCENES = {
    dawn: {
      label: '清晨', icon: 'sunrise',
      stops: [[0, '#1b2a52'], [0.42, '#6f4570'], [0.72, '#dd7a63'], [1, '#ffd9a0']],
      orb: { x: 0.78, y: 0.70, r: 0.085, core: '#fff2d6', glow: '#ff9d63' },
      stars: 0.22, cloud: 0.40, cloudColor: '#ffd7c0', haze: 0.30
    },
    day: {
      label: '白天', icon: 'sun',
      stops: [[0, '#245c9e'], [0.45, '#6ba6db'], [0.80, '#c7e4f8'], [1, '#f2f8ff']],
      orb: { x: 0.26, y: 0.20, r: 0.050, core: '#fffaf0', glow: '#ffe3a6' },
      stars: 0, cloud: 0.62, cloudColor: '#ffffff', haze: 0.16
    },
    dusk: {
      label: '黄昏', icon: 'sunset',
      stops: [[0, '#111b38'], [0.40, '#572a63'], [0.68, '#c25563'], [1, '#f2a15f']],
      orb: { x: 0.24, y: 0.74, r: 0.080, core: '#ffe2b4', glow: '#ff7b4f' },
      stars: 0.34, cloud: 0.46, cloudColor: '#ffc59c', haze: 0.28
    },
    night: {
      label: '夜晚', icon: 'moon',
      stops: [[0, '#04060f'], [0.48, '#0a1226'], [0.82, '#131f42'], [1, '#1e2d55']],
      orb: { x: 0.76, y: 0.19, r: 0.055, core: '#ffffff', glow: '#a8c8ff' },
      stars: 1, cloud: 0.16, cloudColor: '#8fa9dd', haze: 0.22
    }
  };
  var ORDER = ['dawn', 'day', 'dusk', 'night'];

  /* ---------------- 时间 → 场景 ---------------- */
  function sceneByHour(hour, minute) {
    var t = hour + (minute || 0) / 60;
    var table = bgCfg().schedule || [
      { from: 5, to: 8, scene: 'dawn' },
      { from: 8, to: 17, scene: 'day' },
      { from: 17, to: 20, scene: 'dusk' },
      { from: 20, to: 29, scene: 'night' }
    ];
    for (var i = 0; i < table.length; i++) {
      var s = table[i];
      if (t >= s.from && t < s.to) return s.scene;
      if (s.to > 24 && t < s.to - 24) return s.scene;   // 跨天区间
    }
    return 'night';
  }
  function todayScene() {
    var d = new Date();
    return sceneByHour(d.getHours(), d.getMinutes());
  }
  function resolveScene(key) { return SCENES[key] ? key : 'night'; }

  /* ---------------- 小工具 ---------------- */
  function hex2rgb(h) {
    h = String(h).replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgba(c, a) {
    return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + (a === undefined ? 1 : a) + ')';
  }
  function mix(a, b, k) { return a + (b - a) * k; }
  function mixRgb(a, b, k) { return [mix(a[0], b[0], k), mix(a[1], b[1], k), mix(a[2], b[2], k)]; }
  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }

  /* 稳定伪随机：保证星尘/云的位置在每次加载与场景切换中都不跳变 */
  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  /* 把场景定义压成可插值的数值状态 */
  function toState(key) {
    var s = SCENES[resolveScene(key)];
    return {
      offsets: s.stops.map(function (st) { return st[0]; }),
      stops: s.stops.map(function (st) { return hex2rgb(st[1]); }),
      orbX: s.orb.x, orbY: s.orb.y, orbR: s.orb.r,
      core: hex2rgb(s.orb.core), glow: hex2rgb(s.orb.glow),
      stars: s.stars, cloud: s.cloud, cloudColor: hex2rgb(s.cloudColor), haze: s.haze
    };
  }

  /* ---------------- 星尘与云 ---------------- */
  var stars = [], clouds = [];
  (function build() {
    var rnd = mulberry32(20250921);
    for (var i = 0; i < 190; i++) {
      stars.push({
        x: rnd(), y: rnd() * 0.78,
        r: 0.35 + rnd() * 1.15,
        phase: rnd() * Math.PI * 2,
        speed: 0.4 + rnd() * 1.5
      });
    }
    for (var j = 0; j < 7; j++) {
      clouds.push({
        x: rnd() * 1.3 - 0.15, y: 0.12 + rnd() * 0.62,
        sx: 0.20 + rnd() * 0.30, sy: 0.035 + rnd() * 0.05,
        speed: 0.004 + rnd() * 0.010,
        alpha: 0.25 + rnd() * 0.45
      });
    }
  })();

  /* ---------------- 引擎 ---------------- */
  var canvas, ctx, photoBox, W = 0, H = 0, dpr = 1;
  var current = null, anim = null;
  var mode = 'auto', manualScene = 'day', sceneKey = 'night';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var playing = true, photos = [];
  var rafId = 0, lastTs = 0, clockTimer = 0, lastSecond = 0;

  /* 主题（文字亮/暗）：白天为亮色主题；某场景配了图片时可用 photo.theme 覆盖 */
  function themeFor(key) {
    for (var i = 0; i < photos.length; i++) {
      if (photos[i].scene === key && photos[i].theme) return photos[i].theme;
    }
    return key === 'day' ? 'light' : 'dark';
  }
  function applyTheme(key) {
    document.documentElement.setAttribute('data-theme', themeFor(key));
  }

  function sizeCanvas() {
    if (!canvas) return;
    dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    /* 改画布尺寸会清空内容：无论是否在跑动画，都必须立刻补画一帧，
       否则在 rAF 被节流（后台标签、无头环境）时可能留下一块空白 */
    if (current) drawStatic(performance.now() / 1000);
  }

  /* 天空渐变 */
  function paintSky() {
    var g = ctx.createLinearGradient(0, 0, 0, H);
    for (var i = 0; i < current.stops.length; i++) {
      g.addColorStop(Math.min(Math.max(current.offsets[i], 0), 1), rgba(current.stops[i], 1));
    }
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }

  /* 云带：柔和的椭圆光斑，缓慢飘移 */
  function paintClouds(time) {
    if (current.cloud <= 0.01) return;
    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    for (var i = 0; i < clouds.length; i++) {
      var c = clouds[i];
      var x = ((c.x + time * c.speed) % 1.35 - 0.15) * W;
      var y = c.y * H;
      var rx = c.sx * W, ry = c.sy * H;
      ctx.save();
      ctx.translate(x, y);
      ctx.scale(rx / 100, ry / 100);
      var g = ctx.createRadialGradient(0, 0, 0, 0, 0, 100);
      g.addColorStop(0, rgba(current.cloudColor, 0.42 * c.alpha * current.cloud));
      g.addColorStop(0.55, rgba(current.cloudColor, 0.16 * c.alpha * current.cloud));
      g.addColorStop(1, rgba(current.cloudColor, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(0, 0, 100, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    ctx.restore();
  }

  /* 星尘：夜晚出现，轻微闪烁 */
  function paintStars(time) {
    if (current.stars <= 0.01) return;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      var tw = 0.55 + 0.45 * Math.sin(time * s.speed + s.phase);
      var a = current.stars * tw * 0.9;
      if (a <= 0.02) continue;
      ctx.fillStyle = 'rgba(255,255,255,' + a.toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(s.x * W, s.y * H, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  /* 日/月：核心 + 大范围柔光 */
  function paintOrb() {
    var r = current.orbR * Math.min(W, H * 1.15);
    var cx = current.orbX * W, cy = current.orbY * H;
    var halo = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, r * 9);
    halo.addColorStop(0, rgba(current.glow, 0.42));
    halo.addColorStop(0.35, rgba(current.glow, 0.14));
    halo.addColorStop(1, rgba(current.glow, 0));
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, H);

    var core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    core.addColorStop(0, rgba(current.core, 1));
    core.addColorStop(0.72, rgba(current.core, 0.92));
    core.addColorStop(1, rgba(current.core, 0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
  }

  /* 地平线雾 + 四角压暗，保证任何场景下文字都能读 */
  function paintHaze() {
    var hl = 0.55 * H;
    var g = ctx.createLinearGradient(0, hl, 0, H);
    g.addColorStop(0, 'rgba(255,255,255,0)');
    g.addColorStop(1, rgba(current.stops[current.stops.length - 1], current.haze));
    ctx.fillStyle = g;
    ctx.fillRect(0, hl, W, H - hl);

    var v = ctx.createRadialGradient(W * 0.5, H * 0.45, Math.min(W, H) * 0.28, W * 0.5, H * 0.5, Math.max(W, H) * 0.78);
    v.addColorStop(0, 'rgba(0,0,0,0)');
    v.addColorStop(1, 'rgba(2,4,10,0.34)');
    ctx.fillStyle = v;
    ctx.fillRect(0, 0, W, H);
  }

  function drawStatic(time) {
    if (!current) return;
    paintSky();
    paintClouds(time);
    paintStars(time);
    paintOrb();
    paintHaze();
  }

  /* 场景过渡：把 current 逐帧逼近 target */
  function step(ts) {
    rafId = requestAnimationFrame(step);
    if (ts - lastTs < 1000 / 30) return;           // 30fps 足够，省电
    var dt = Math.min((ts - lastTs) / 1000, 0.1);
    lastTs = ts;

    if (anim) {
      var k = easeOut(Math.min((ts - anim.start) / anim.dur, 1));
      current = interpolate(anim.from, anim.to, k);
      if (k >= 1) { current = anim.to; anim = null; }
    }
    drawStatic(ts / 1000);
  }

  function interpolate(a, b, k) {
    var offsets = [], stops = [];
    for (var i = 0; i < a.stops.length; i++) {
      offsets.push(mix(a.offsets[i], b.offsets[i], k));
      stops.push(mixRgb(a.stops[i], b.stops[i], k));
    }
    return {
      offsets: offsets, stops: stops,
      orbX: mix(a.orbX, b.orbX, k), orbY: mix(a.orbY, b.orbY, k), orbR: mix(a.orbR, b.orbR, k),
      core: mixRgb(a.core, b.core, k), glow: mixRgb(a.glow, b.glow, k),
      stars: mix(a.stars, b.stars, k), cloud: mix(a.cloud, b.cloud, k),
      cloudColor: mixRgb(a.cloudColor, b.cloudColor, k), haze: mix(a.haze, b.haze, k)
    };
  }

  function gotoScene(key, source, instant) {
    key = resolveScene(key);
    if (key === sceneKey && current) return;
    sceneKey = key;
    document.documentElement.setAttribute('data-scene', key);
    applyTheme(key);
    if (photos.length) applyPhoto(key);

    var to = toState(key);
    if (!current) {
      current = to; anim = null;
      if (!playing) drawStatic(0);
    } else if (playing && !instant) {
      anim = { from: snapshot(current), to: to, start: performance.now(), dur: Math.max(200, bgCfg().smooth || 1200) };
      drawStatic(performance.now() / 1000);          // 先落一帧，不等 rAF
    } else {
      current = to; anim = null; drawStatic(0);
    }
    document.dispatchEvent(new CustomEvent('site:scenechange', {
      detail: { scene: key, mode: mode, source: source || 'auto' }
    }));
  }

  function snapshot(s) {
    return {
      offsets: s.offsets.slice(),
      stops: s.stops.map(function (c) { return c.slice(); }),
      orbX: s.orbX, orbY: s.orbY, orbR: s.orbR,
      core: s.core.slice(), glow: s.glow.slice(),
      stars: s.stars, cloud: s.cloud, cloudColor: s.cloudColor.slice(), haze: s.haze
    };
  }

  /* ---------------- 照片背景（可选） ---------------- */
  var photoLayers = [], photoTop = 0, photoUrl = '';
  function buildPhotoLayers() {
    if (!photoBox || photoLayers.length) return;
    photoBox.innerHTML = '';
    for (var i = 0; i < 2; i++) {
      var d = document.createElement('div');
      d.className = 'bg-photo__layer';
      photoBox.appendChild(d);
      photoLayers.push(d);
    }
  }
  function applyPhoto(key) {
    if (!photoBox) return;
    buildPhotoLayers();
    var item = null;
    for (var i = 0; i < photos.length; i++) if (photos[i].scene === key) item = photos[i];
    if (!item || item.url === photoUrl) return;
    var url = item.url;
    var img = new Image();
    img.onload = function () {
      var first = !photoUrl;
      var next = photoTop === 0 ? 1 : 0;
      var target = photoLayers[next];
      var previous = photoLayers[photoTop];
      target.style.backgroundImage = 'url("' + url + '")';
      if (first) target.style.transition = 'none';     // 首次直接显示，避免停在半透明
      target.classList.add('is-on');
      if (previous && previous !== target) previous.classList.remove('is-on');
      if (first) requestAnimationFrame(function () { target.style.transition = ''; });
      photoUrl = url;
      photoTop = next;
    };
    img.src = url;
  }

  /* ---------------- 时间驱动 ---------------- */
  function tickClock() {
    if (mode !== 'auto') return;
    var d = new Date();
    var minuteKey = d.getHours() * 60 + d.getMinutes();
    if (minuteKey === lastSecond) return;
    lastSecond = minuteKey;
    var key = sceneByHour(d.getHours(), d.getMinutes());
    if (key !== sceneKey) gotoScene(key, 'time');
  }

  /* ---------------- 对外接口 ---------------- */
  var api = {
    SCENES: SCENES,
    ORDER: ORDER,
    init: function () {
      var cfg = bgCfg();
      canvas = document.getElementById('bg-canvas');
      photoBox = document.getElementById('bg-photo');
      if (!canvas || !canvas.getContext) { document.body.classList.add('no-canvas'); return api; }
      ctx = canvas.getContext('2d');

      /* 配置在这里读取（后台配置是异步取回来的） */
      mode = cfg.mode === 'manual' ? 'manual' : 'auto';
      manualScene = resolveScene(cfg.manualScene || 'day');
      playing = cfg.animate !== false && !reduce;
      photos = Array.isArray(cfg.photos) ? cfg.photos.filter(function (p) { return p && p.url; }) : [];
      if (photos.length) document.body.classList.add('has-photo');

      sizeCanvas();
      /* URL 上的 ?scene= 优先于时间表，且不做过渡（首帧就已经是这个场景） */
      var q = null;
      try { q = new URLSearchParams(window.location.search).get('scene'); } catch (e) { q = null; }
      if (q && q !== 'auto' && SCENES[q]) { mode = 'manual'; manualScene = q; }
      sceneKey = mode === 'manual' ? manualScene : todayScene();
      current = toState(sceneKey);
      document.documentElement.setAttribute('data-scene', sceneKey);
      applyTheme(sceneKey);
      if (photos.length) applyPhoto(sceneKey);
      drawStatic(0);
      document.dispatchEvent(new CustomEvent('site:scenechange', {
        detail: { scene: sceneKey, mode: mode, source: 'init' }
      }));

      if (playing) { lastTs = performance.now(); rafId = requestAnimationFrame(step); }
      /* 首帧后再校准一次尺寸：初次布局可能还没稳定（否则底部会留一条没画到的边） */
      requestAnimationFrame(function () { sizeCanvas(); });
      window.addEventListener('resize', sizeCanvas, { passive: true });
      window.addEventListener('orientationchange', sizeCanvas, { passive: true });
      if (window.ResizeObserver) {
        try { new ResizeObserver(sizeCanvas).observe(document.documentElement); } catch (e) { /* 忽略 */ }
      }
      clockTimer = setInterval(tickClock, 20000);      // 每 20s 校验一次时间区间
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) { cancelAnimationFrame(rafId); rafId = 0; }
        else if (playing && !rafId) { lastTs = performance.now(); rafId = requestAnimationFrame(step); }
      });
      if (window.matchMedia) {
        var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
        var onChange = function (e) {
          reduce = e.matches; playing = bgCfg().animate !== false && !reduce;
          cancelAnimationFrame(rafId); rafId = 0;
          if (playing) { lastTs = performance.now(); rafId = requestAnimationFrame(step); }
          else drawStatic(0);
        };
        if (mq.addEventListener) mq.addEventListener('change', onChange); else if (mq.addListener) mq.addListener(onChange);
      }
      return api;
    },
    setScene: function (key, opts) {
      opts = opts || {};
      key = resolveScene(key);
      if (opts.manual === false) { mode = 'auto'; tickClock(); }
      else { mode = 'manual'; manualScene = key; }
      gotoScene(key, opts.source || (mode === 'manual' ? 'manual' : 'auto'), !!opts.instant);
      return api;
    },
    setMode: function (next, instant) {
      mode = next === 'manual' ? 'manual' : 'auto';
      if (mode === 'auto') { lastSecond = -1; tickClock(); gotoScene(todayScene(), 'auto', !!instant); }
      else gotoScene(manualScene, 'manual', !!instant);
      return api;
    },
    todayScene: todayScene,
    getState: function () { return { scene: sceneKey, mode: mode, reducedMotion: reduce }; }
  };

  window.SiteBackground = api;
})();
