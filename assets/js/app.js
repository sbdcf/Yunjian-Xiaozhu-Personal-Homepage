/* =============================================================
 * app.js · 首页运行时
 * -------------------------------------------------------------
 * 1) 取配置：优先后台 /api/site（site.json），失败则用 config.js
 * 2) 渲染个人信息 / 导航 / 时间轴 / 底部信息（留空的一律不显示）
 * 3) 分屏滚动：滚轮一跳一屏 + 键盘 + 触屏原生滚动，右侧指示点
 * 4) 背景切换控件（自动 / 清晨 / 白天 / 黄昏 / 夜晚）
 * 5) 背景音乐播放器（music.js）
 * 6) 文字占比自检（B 键）：占用网格法统计文字覆盖面积，验证 < 50%
 * ============================================================= */
(function () {
  'use strict';

  var DEFAULTS = window.SITE_CONFIG || {};      // config.js：离线默认值
  var C = DEFAULTS;                             // 实际使用的配置（会被后台覆盖）
  var BG = window.SiteBackground;
  var REDUCE = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var COARSE = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
  var root = document.documentElement;

  /* ---------------- 通用小工具 ---------------- */
  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $all(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function icon(name, cls) {
    return '<svg class="ico ' + (cls || '') + '" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
  }
  function text(node, value) { if (node && value != null && value !== '') node.textContent = value; }
  function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }
  function isHttp() { return /^https?:$/.test(location.protocol); }
  function escapeHtml(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  /* 深合并：后台配置覆盖默认值（数组与标量整体替换） */
  function merge(base, over) {
    if (base && over && typeof base === 'object' && typeof over === 'object'
      && !Array.isArray(base) && !Array.isArray(over)) {
      var out = {};
      Object.keys(base).forEach(function (k) { out[k] = base[k]; });
      Object.keys(over).forEach(function (k) {
        out[k] = (k in base) ? merge(base[k], over[k]) : over[k];
      });
      return out;
    }
    return over === undefined ? base : over;
  }

  /* ---------------- 配置来源 ---------------- */
  function loadConfig() {
    if (!isHttp()) return Promise.resolve(DEFAULTS);       // 纯静态：直接用 config.js
    return fetch('/api/site', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.ok || !data.config) return DEFAULTS;
        return merge(DEFAULTS, data.config);
      })
      .catch(function () { return DEFAULTS; });             // 后台挂了也不影响浏览
  }

  /* =============================================================
   * 一、渲染配置内容
   * ============================================================= */
  var content = {
    render: function () {
      var site = C.site || {}, a = C.author || {}, f = C.footer || {};

      if (site.name) {
        document.title = site.name + ' · 首页';
        $all('[data-site-name]').forEach(function (n) { n.textContent = site.name; });
      }

      text($('[data-author-name]'), a.name);
      text($('[data-author-role]'), a.role);
      text($('[data-initials]'), a.initials || (a.name || '').slice(0, 1));

      /* 留空即隐藏：不显示占位或说明性文字 */
      var intro = $('[data-author-intro]');
      if (intro) {
        if (a.intro) { intro.textContent = a.intro; intro.hidden = false; }
        else intro.hidden = true;
      }
      var status = $('#heroStatus');
      if (status) {
        if (a.status) { text($('[data-author-status]', status), a.status); status.hidden = false; }
        else status.hidden = true;
      }

      var avatar = $('#avatar');
      if (avatar && a.avatar) {
        avatar.classList.add('has-img');
        avatar.style.backgroundImage = 'url("' + a.avatar + '")';
        var span = $('[data-initials]', avatar);
        if (span) span.style.visibility = 'hidden';
      }

      /* 社交图标按钮 */
      var social = $('#social');
      if (social) {
        var links = (C.social || []).filter(function (s) { return s && s.href; });
        social.innerHTML = links.map(function (s) {
          return '<li><a class="social-btn" href="' + s.href + '" title="' + s.label + '" aria-label="' + s.label + '"'
            + (/^https?:/.test(s.href) ? ' target="_blank" rel="noopener"' : '') + '>' + icon(s.icon || 'link') + '</a></li>';
        }).join('');
        social.hidden = !links.length;
      }

      /* 导航磁贴 */
      var grid = $('#navGrid');
      if (grid) {
        grid.innerHTML = (C.nav || []).filter(function (n) { return n && n.href; }).map(function (n) {
          return '<li class="nav-item"><a class="nav-tile" href="' + n.href + '">'
            + '<span class="tile-ico">' + icon(n.icon || 'link') + '</span>'
            + '<span class="tile-label">' + n.label + '</span>'
            + (n.caption ? '<span class="tile-caption">' + n.caption + '</span>' : '')
            + '</a></li>';
        }).join('');
      }

      /* 底部信息 */
      var copy = $('[data-copyright]');
      if (copy) {
        if (f.copyright) copy.textContent = f.copyright; else copy.hidden = true;
      }
      var fl = $('#footLinks');
      if (fl) {
        var flinks = (f.links || []).filter(function (l) { return l && l.href; });
        fl.innerHTML = flinks.map(function (l) {
          return '<li><a class="foot-link" href="' + l.href + '">' + icon('link', 'ico-xs') + '<span>' + l.label + '</span></a></li>';
        }).join('');
      }
      var icpLine = $('#icpLine');
      if (icpLine) {
        if (f.icp && f.icp.text) {
          var icp = $('#icpLink');
          icp.textContent = f.icp.text;
          icp.href = f.icp.href || 'https://beian.miit.gov.cn/';
          icpLine.hidden = false;
        } else {
          icpLine.hidden = true;
        }
      }
      var pline = $('#policeLine');
      if (pline) {
        if (f.police && f.police.text) {
          var pl = $('#policeLink');
          pl.textContent = f.police.text;
          pl.href = f.police.href || '#';
          pline.hidden = false;
        } else {
          pline.hidden = true;
        }
      }
    }
  };

  /* =============================================================
   * 一·B、时间轴（数据来自后台抓取的 RSS；没有条目就整屏隐藏）
   * ============================================================= */
  var timeline = {
    data: null,

    /* 后台可用且配置了订阅源时才有内容 */
    load: function () {
      if (!isHttp()) return Promise.resolve(null);
      return fetch('/api/timeline', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || !data.ok || !(data.items || []).length) return null;
          return data;
        })
        .catch(function () { return null; });
    },

    render: function (data) {
      var section = $('#timeline'), listEl = $('#timelineList');
      if (!section || !listEl) return;
      if (!data || !data.items.length) { section.hidden = true; return; }
      this.data = data;
      listEl.innerHTML = data.items.map(function (it) {
        var date = (it.date || '').slice(0, 10);
        var href = it.link || '#';
        var inner = '<span class="tl-date">' + (date || '—') + '</span>'
          + '<span class="tl-title">' + escapeHtml(it.title || '') + '</span>'
          + (it.source ? '<span class="tl-src">' + escapeHtml(it.source) + '</span>' : '');
        return '<li class="tl-item">'
          + (/^https?:/.test(href)
            ? '<a class="tl-link" href="' + href + '" target="_blank" rel="noopener">' + inner + '</a>'
            : '<span class="tl-link">' + inner + '</span>')
          + '</li>';
      }).join('');
      section.hidden = false;
    }
  };

  /* =============================================================
   * 二、分屏滚动
   * ============================================================= */
  var pager = {
    pagesEl: null, pages: [], rail: null, index: 0,
    lock: 0, acc: 0,

    init: function () {
      this.pagesEl = $('#pages');
      /* 只把可见分屏计入：时间轴未配置时它带着 hidden，不参与翻屏与指示点 */
      this.pages = $all('.page', this.pagesEl).filter(function (p) { return !p.hidden; });
      this.labels = this.pages.map(function (p, i) {
        return p.getAttribute('data-label') || ('第 ' + (i + 1) + ' 屏');
      });
      this.rail = $('#rail');
      var self = this;

      /* 右侧指示点 */
      if (this.rail) {
        this.rail.innerHTML = this.pages.map(function (p, i) {
          return '<button class="rail-dot" type="button" data-i="' + i + '" data-label="' + self.labels[i]
            + '" aria-label="' + self.labels[i] + '"><span class="rail-fill"></span></button>';
        }).join('');
        this.rail.addEventListener('click', function (e) {
          var b = e.target.closest('.rail-dot');
          if (b) self.goTo(+b.dataset.i);
        });
      }

      $('#toTop') && $('#toTop').addEventListener('click', function () { self.goTo(0); });

      var ticking = false;
      this.pagesEl.addEventListener('scroll', function () {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(function () { ticking = false; self.sync(); });
      }, { passive: true });

      this.bindKeys();
      this.bindWheel();
      this.goTo(0, 'instant');
      this.sync();
    },

    /* behavior: 'instant' 立即跳转；缺省为平滑滚动（reduced-motion 下自动瞬时）
     * 注意：这里必须用 'instant'，因为 CSS 的 scroll-behavior:smooth 会让 'auto' 也变成平滑滚动 */
    goTo: function (i, behavior) {
      i = clamp(i, 0, this.pages.length - 1);
      var p = this.pages[i];
      if (!p) return;
      p.scrollIntoView({ behavior: behavior || (REDUCE ? 'instant' : 'smooth'), block: 'start' });
    },

    next: function (dir) {
      var now = performance.now();
      if (now < this.lock) return;
      this.lock = now + ((C.behavior && C.behavior.wheelLock) || 800);
      this.goTo(this.index + dir);
    },

    bindKeys: function () {
      var self = this;
      window.addEventListener('keydown', function (e) {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
        if (typing) return;
        var onControl = e.target.closest && e.target.closest('button,a');
        switch (e.key) {
          case 'ArrowDown': case 'PageDown': self.next(1); break;
          case 'ArrowUp': case 'PageUp': self.next(-1); break;
          case ' ': if (onControl) return; self.next(e.shiftKey ? -1 : 1); break;
          case 'Home': self.goTo(0); break;
          case 'End': self.goTo(self.pages.length - 1); break;
          default:
            if ((e.key === 'b' || e.key === 'B') && C.debug && C.debug.textBudget) hud.toggle();
            return;
        }
        e.preventDefault();
      });
    },

    /* 滚轮：一次滑动 = 一屏（触屏与减动效设备交回原生滚动） */
    bindWheel: function () {
      var b = C.behavior || {};
      if (b.snapWheel === false || REDUCE || COARSE) return;
      var self = this, threshold = b.wheelThreshold || 32;
      window.addEventListener('wheel', function (e) {
        if (e.ctrlKey || (e.target.closest && e.target.closest('.hud'))) return;   // 让浏览器缩放/浮层正常工作
        e.preventDefault();
        var now = performance.now();
        if (now < self.lock) return;
        if ((e.deltaY > 0 && self.index >= self.pages.length - 1)
          || (e.deltaY < 0 && self.index <= 0)) { self.acc = 0; return; }
        /* 归一化：Firefox 等按"行/页"上报 deltaMode */
        var d = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1);
        self.acc += d;
        if (Math.abs(self.acc) < threshold) return;
        var dir = self.acc > 0 ? 1 : -1;
        self.acc = 0;
        self.next(dir);
      }, { passive: false });
    },

    sync: function () {
      var h = this.pagesEl.clientHeight || 1;
      var i = clamp(Math.round(this.pagesEl.scrollTop / h), 0, this.pages.length - 1);
      if (i === this.index) { this.paint(); return; }
      this.index = i;
      this.paint();
      document.dispatchEvent(new CustomEvent('site:pagechange', { detail: { index: i } }));
    },

    paint: function () {
      for (var i = 0; i < this.pages.length; i++) {
        this.pages[i].classList.toggle('is-active', i === this.index);
      }
      if (this.rail) {
        $all('.rail-dot', this.rail).forEach(function (d, i) {
          d.classList.toggle('is-active', i === pager.index);
          if (i === pager.index) d.setAttribute('aria-current', 'true'); else d.removeAttribute('aria-current');
        });
      }
      document.body.setAttribute('data-section', String(this.index));
      var bar = $('#topbar');
      if (bar) bar.classList.toggle('is-visible', this.index > 0);
    }
  };

  /* =============================================================
   * 三、背景切换控件 + 状态回显
   * ============================================================= */
  var bgUI = {
    init: function () {
      var box = $('#bgSwitch');
      if (!box || !BG) return;
      var scenes = BG.SCENES, order = BG.ORDER;
      var html = '<button class="bg-btn bg-btn--auto" type="button" data-mode="auto" title="跟随时间自动切换" aria-label="跟随时间自动切换">'
        + icon('auto') + '</button><span class="bg-sep" aria-hidden="true"></span>';
      html += order.map(function (k) {
        return '<button class="bg-btn" type="button" data-scene="' + k + '" title="' + scenes[k].label
          + '" aria-label="' + scenes[k].label + '背景">' + icon(scenes[k].icon) + '</button>';
      }).join('');
      box.innerHTML = html;

      box.addEventListener('click', function (e) {
        var b = e.target.closest('.bg-btn');
        if (!b) return;
        if (b.dataset.mode === 'auto') BG.setMode('auto');
        else BG.setScene(b.dataset.scene);
        bgUI.paint();
      });

      document.addEventListener('site:scenechange', function () { bgUI.paint(); });
      this.paint();
    },

    paint: function () {
      if (!BG) return;
      var st = BG.getState();
      var box = $('#bgSwitch');
      if (box) {
        $all('.bg-btn', box).forEach(function (b) {
          var on = (b.dataset.mode === 'auto' && st.mode === 'auto')
            || (b.dataset.scene === st.scene && st.mode === 'manual');
          b.classList.toggle('is-active', !!on);
          b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
      }
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', st.scene === 'day' ? '#cfe6f8' : '#070b14');
    }
  };

  /* =============================================================
   * 四、文字占比自检（占用网格法）
   *   统计当前视口内"可见文字"覆盖的面积 ÷ 视口面积。
   *   用网格去重，重叠文字不会被重复计算。
   * ============================================================= */
  var hud = {
    el: null, visible: false, cell: 12,
    minRatio: 0.5,

    init: function () {
      this.el = $('#hud');
      if (!this.el) return;
      if (C.debug && C.debug.showHud) this.toggle(true);
      document.addEventListener('site:pagechange', function () { if (hud.visible) hud.update(); });
      document.addEventListener('site:scenechange', function () { if (hud.visible) hud.update(); });
      window.addEventListener('resize', function () { if (hud.visible) hud.update(); }, { passive: true });
    },

    toggle: function (force) {
      if (!this.el) return;
      this.visible = typeof force === 'boolean' ? force : !this.visible;
      this.el.hidden = !this.visible;
      if (this.visible) this.update();
    },

    /* 按分屏收集文字矩形（坐标相对各自那一屏，且裁掉屏外部分）
     * 说明：只排除 display:none / visibility:hidden / aria-hidden 与浮层控件，
     *       不排除"正在淡入"的元素——占比统计的是版面占用，与动画无关。 */
    collect: function () {
      var pages = pager.pages || [];
      var boxes = pages.map(function (p) {
        var r = p.getBoundingClientRect();
        return { x: r.left, y: r.top, w: p.clientWidth || r.width, h: p.clientHeight || r.height, rects: [] };
      });

      var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: function (node) {
          if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
          var p = node.parentElement;
          if (!p || !p.closest('.page')) return NodeFilter.FILTER_REJECT;      // 浮层控件不计入
          if (p.closest('.hud, .sprite, .rail, .bg-switch')) return NodeFilter.FILTER_REJECT;
          if (p.getAttribute('aria-hidden') === 'true') return NodeFilter.FILTER_REJECT;
          var cs = getComputedStyle(p);
          if (cs.display === 'none' || cs.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        }
      });

      var n;
      while ((n = walker.nextNode())) {
        var pageEl = n.parentElement.closest('.page');
        var idx = pages.indexOf(pageEl);
        if (idx < 0) continue;
        var box = boxes[idx];
        var range = document.createRange();
        range.selectNodeContents(n);
        var list = range.getClientRects();
        for (var i = 0; i < list.length; i++) {
          var r = list[i];
          if (r.width < 1 || r.height < 1) continue;
          var x0 = Math.max(r.left - box.x, 0), y0 = Math.max(r.top - box.y, 0);
          var x1 = Math.min(r.right - box.x, box.w), y1 = Math.min(r.bottom - box.y, box.h);
          if (x1 <= x0 || y1 <= y0) continue;
          box.rects.push([x0, y0, x1, y1]);
        }
      }
      return boxes;
    },

    /* 占用网格法：统计"被文字覆盖的面积"，重叠不会重复计算 */
    ratioOf: function (box) {
      var cols = Math.max(1, Math.ceil(box.w / this.cell));
      var rows = Math.max(1, Math.ceil(box.h / this.cell));
      var seen = new Uint8Array(cols * rows), count = 0;
      for (var i = 0; i < box.rects.length; i++) {
        var r = box.rects[i];
        var c0 = Math.floor(r[0] / this.cell), c1 = Math.ceil(r[2] / this.cell);
        var r0 = Math.floor(r[1] / this.cell), r1 = Math.ceil(r[3] / this.cell);
        for (var y = r0; y < r1; y++) {
          if (y < 0 || y >= rows) continue;
          for (var x = c0; x < c1; x++) {
            if (x < 0 || x >= cols) continue;
            var k = y * cols + x;
            if (!seen[k]) { seen[k] = 1; count++; }
          }
        }
      }
      return { ratio: count / (cols * rows), blocks: box.rects.length };
    },

    /* 三屏各自的文字占比 */
    measure: function () {
      var self = this;
      return this.collect().map(function (box) { return self.ratioOf(box); });
    },

    update: function () {
      if (!this.el) return;
      var m = this.measure();
      var i = clamp(pager.index || 0, 0, Math.max(m.length - 1, 0));
      var cur = m[i] ? m[i].ratio * 100 : 0;
      var worst = m.reduce(function (a, x) { return Math.max(a, x.ratio); }, 0) * 100;
      var st = BG ? BG.getState() : { scene: 'night', mode: '-' };
      var label = BG ? (BG.SCENES[st.scene] || {}).label : '';
      var name = (pager.labels || [])[i] || '本屏';
      var ok = worst < this.minRatio * 100;
      var detail = m.map(function (x, k) {
        return ((pager.labels || [])[k] || k) + ' ' + (x.ratio * 100).toFixed(1) + '%';
      }).join(' / ');

      this.el.innerHTML =
        '<span class="hud-key">背景</span>' + label + ' · ' + (st.mode === 'auto' ? '自动' : '手动')
        + '<span class="hud-sep">|</span>'
        + '<span class="hud-key">' + name + '文字占比</span>'
        + '<b class="' + (ok ? 'is-ok' : 'is-bad') + '">' + cur.toFixed(1) + '%</b>'
        + '<span class="hud-sep">|</span>上限 50%'
        + '<span class="hud-sep">|</span>三屏 ' + detail
        + '<span class="hud-sep">|</span>B 键关闭';
      this.el.dataset.ratio = cur.toFixed(2);
      this.el.dataset.ratios = m.map(function (x) { return (x.ratio * 100).toFixed(2); }).join(',');
      this.el.dataset.blocks = m.map(function (x) { return x.blocks; }).join(',');
      this.el.dataset.worst = worst.toFixed(2);
    }
  };

  /* =============================================================
   * 启动
   * ============================================================= */
  /* URL 参数（便于分享指定背景 / 联调自检）
   * ?scene=dawn|day|dusk|night|auto  ?page=0|1|2  ?hud=1
   * 指定 scene 时直接落到该场景（不做过渡），方便分享与截图 */
  function applyQuery() {
    var q = new URLSearchParams(window.location.search);
    var scene = q.get('scene');
    if (scene && BG) {
      if (scene === 'auto') { BG.setMode('auto', true); }
      else if (BG.SCENES[scene]) { BG.setScene(scene, { instant: true }); }
      bgUI.paint();
    }
    var page = parseInt(q.get('page'), 10);
    if (!isNaN(page)) { pager.goTo(page, 'instant'); pager.sync(); }
    if (q.get('hud') && C.debug && C.debug.textBudget) hud.toggle(true);
    if (q.get('diag')) diag();
  }

  /* ?diag=1：把关键盒模型数值打到页面上，便于真机/无头环境排查布局 */
  function diag() {
    var box = function (sel) {
      var e = document.querySelector(sel);
      if (!e) return sel + ' : none';
      var r = e.getBoundingClientRect();
      return sel + ' : x' + Math.round(r.left) + ' y' + Math.round(r.top) + ' ' + Math.round(r.width) + '×' + Math.round(r.height);
    };
    var pre = document.createElement('pre');
    pre.id = 'diag';
    pre.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:99;margin:0;padding:8px 10px;border-radius:8px;'
      + 'background:rgba(0,0,0,.72);color:#fff;font:12px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;max-width:96vw';
    pre.textContent = [
      'viewport ' + window.innerWidth + '×' + window.innerHeight + ' dpr' + window.devicePixelRatio,
      'html.scrollW ' + document.documentElement.scrollWidth + ' / pages clientW ' + pager.pagesEl.clientWidth,
      box('.page--nav .page-inner'), box('#navGrid'), box('#navGrid .nav-tile'), box('.site-foot'), box('.hero-name')
    ].join('\n');
    document.body.appendChild(pre);
  }

  function boot() {
    content.render();
    /* 时间轴先决定显隐，再初始化分屏（屏数随配置变化） */
    timeline.render(timelineData);

    root.classList.add('is-boot');          // 仅首屏播放一次入场动画
    if (BG) BG.init();
    pager.init();
    bgUI.init();
    hud.init();
    if (window.SiteMusic) window.SiteMusic.init(C.music);
    root.classList.add('is-ready');         // 打开分屏过渡规则
    applyQuery();
    setTimeout(function () { root.classList.remove('is-boot'); }, 1500);
  }

  /* 先取后台配置与时间轴，再启动（失败一律回退到 config.js） */
  var timelineData = null;
  Promise.all([loadConfig(), timeline.load()]).then(function (res) {
    C = res[0] || DEFAULTS;
    window.SITE_CONFIG = C;                 // 供 background.js 等后续模块读取
    timelineData = res[1];
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
  });

  /* 供自动化测试/调试使用 */
  window.SiteHome = {
    pager: pager, hud: hud, bgUI: bgUI, timeline: timeline,
    config: function () { return C; },
    /* 各分屏文字占比数组（0~1） */
    textRatios: function () { return hud.measure().map(function (m) { return m.ratio; }); }
  };
})();
