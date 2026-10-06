/* =============================================================
 * admin.js · 站点后台逻辑（原生 JS，无框架、无装饰）
 * 配置对象在内存里编辑，输入框用 data-path 绑定到配置路径，
 * 保存时统一收集后 PUT /api/site。
 * ============================================================= */
(function () {
  'use strict';

  var CFG = {};
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var ICONS = ['pen', 'cube', 'camera', 'user', 'clock', 'send', 'mail', 'rss', 'github', 'link'];
  var SCENES = [['dawn', '清晨'], ['day', '白天'], ['dusk', '黄昏'], ['night', '夜晚']];

  /* ---------------- 基础工具 ---------------- */
  function esc(val) {
    return String(val == null ? '' : val).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function clone(obj) { return JSON.parse(JSON.stringify(obj)); }
  function get(path) {
    return path.split('.').reduce(function (o, k) { return o == null ? undefined : o[k]; }, CFG);
  }
  function set(path, val, target) {
    var ks = path.split('.'), o = target || CFG;
    for (var i = 0; i < ks.length - 1; i++) {
      var k = ks[i];
      if (o[k] == null) o[k] = /^\d+$/.test(ks[i + 1]) ? [] : {};
      o = o[k];
    }
    o[ks[ks.length - 1]] = val;
    return o;
  }
  function list(path) {
    var arr = get(path);
    if (!Array.isArray(arr)) { set(path, []); arr = get(path); }
    return arr;
  }
  function status(msg, kind) {
    var el = $('#status');
    el.textContent = msg || '';
    el.className = 'status' + (kind ? ' ' + kind : '');
  }
  function api(method, url, body) {
    return fetch(url, {
      method: method,
      credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok || data.ok === false) {
          var err = new Error(data.error || ('HTTP ' + res.status));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  /* ---------------- 表单片段 ---------------- */
  function field(path, label, opts) {
    opts = opts || {};
    var val = get(path);
    if (opts.number) {
      return '<label>' + esc(label) + '<input type="number" ' + (opts.attrs || '')
        + ' data-path="' + path + '" data-number value="' + esc(val == null ? '' : val) + '"></label>';
    }
    return '<label>' + esc(label) + '<input type="' + (opts.type || 'text') + '" ' + (opts.attrs || '')
      + ' data-path="' + path + '" placeholder="' + esc(opts.placeholder || '') + '" value="' + esc(val == null ? '' : val) + '"></label>';
  }
  function area(path, label) {
    return '<label>' + esc(label) + '<textarea rows="2" data-path="' + path + '">' + esc(get(path) || '') + '</textarea></label>';
  }
  function check(path, label) {
    return '<label class="inline"><input type="checkbox" data-path="' + path + '"' + (get(path) ? ' checked' : '') + '> ' + esc(label) + '</label>';
  }
  function select(path, label, options) {
    var cur = get(path);
    var html = options.map(function (opt) {
      var v = Array.isArray(opt) ? opt[0] : opt;
      var t = Array.isArray(opt) ? opt[1] : opt;
      return '<option value="' + esc(v) + '"' + (String(cur) === String(v) ? ' selected' : '') + '>' + esc(t) + '</option>';
    }).join('');
    return '<label>' + esc(label) + '<select data-path="' + path + '">' + html + '</select></label>';
  }
  function uploadRow(path, label, accept) {
    var val = get(path) || '';
    return '<label>' + esc(label)
      + '<input type="text" data-path="' + path + '" value="' + esc(val) + '" placeholder="图片/音频地址，或用下方按钮上传">'
      + '</label><p><button type="button" data-upload="' + path + '" data-accept="' + esc(accept || '') + '">上传文件</button>'
      + (val ? ' <a href="' + esc(val) + '" target="_blank" rel="noopener">查看当前</a>' : '')
      + '</p>';
  }
  function rowHead(index, path, title) {
    return '<div class="row-head"><strong>' + esc(title) + ' #' + (index + 1) + '</strong>'
      + '<button type="button" class="remove" data-del="' + path + '" data-i="' + index + '">删除</button></div>';
  }

  /* ---------------- 各区块渲染 ---------------- */
  function renderSite() {
    $('#siteFields').innerHTML =
      '<div class="grid2">'
      + field('site.name', '站点名称')
      + field('author.name', '姓名')
      + field('author.initials', '头像字母（留空取姓名首字）')
      + field('author.role', '身份/职位')
      + '</div>'
      + area('author.intro', '一句话介绍（留空则不显示）')
      + field('author.status', '状态（留空则不显示）')
      + uploadRow('author.avatar', '头像图片', 'image/*');
  }

  function renderSocial() {
    var items = list('social');
    $('#socialList').innerHTML = items.length ? items.map(function (it, i) {
      return '<div class="row">' + rowHead(i, 'social', '链接')
        + '<div class="grid3">'
        + field('social.' + i + '.label', '显示名')
        + field('social.' + i + '.href', '地址')
        + select('social.' + i + '.icon', '图标', ICONS)
        + '</div></div>';
    }).join('') : '<p class="hint">暂无</p>';
  }

  function renderNav() {
    var items = list('nav');
    $('#navList').innerHTML = items.length ? items.map(function (it, i) {
      return '<div class="row">' + rowHead(i, 'nav', '导航')
        + '<div class="grid2">'
        + field('nav.' + i + '.label', '名称（建议 ≤4 字）')
        + field('nav.' + i + '.caption', '副标题（可留空）')
        + field('nav.' + i + '.href', '链接')
        + select('nav.' + i + '.icon', '图标', ICONS)
        + '</div></div>';
    }).join('') : '<p class="hint">暂无</p>';
  }

  function renderFooter() {
    $('#footerFields').innerHTML =
      field('footer.copyright', '版权行（留空则不显示）')
      + '<div class="grid2">'
      + field('footer.icp.text', 'ICP 备案号（留空则整行隐藏）')
      + field('footer.icp.href', '备案跳转地址')
      + field('footer.police.text', '公安备案号（留空则整行隐藏）')
      + field('footer.police.href', '公安备案跳转地址')
      + '</div>';
    var links = list('footer.links');
    $('#footLinks').innerHTML = links.length ? links.map(function (it, i) {
      return '<div class="row">' + rowHead(i, 'footer.links', '链接')
        + '<div class="grid2">'
        + field('footer.links.' + i + '.label', '显示名')
        + field('footer.links.' + i + '.href', '地址')
        + '</div></div>';
    }).join('') : '<p class="hint">暂无</p>';
  }

  function renderBackground() {
    $('#bgFields').innerHTML =
      '<div class="grid2">'
      + select('background.mode', '模式', [['auto', '跟随时间自动'], ['manual', '固定一个场景']])
      + select('background.manualScene', '固定场景', SCENES)
      + field('background.smooth', '过渡时长（毫秒）', { number: true, attrs: 'min="0" step="100"' })
      + '</div>'
      + check('background.animate', '允许轻微动画（星尘/云带）');

    var sch = list('background.schedule');
    $('#scheduleList').innerHTML = sch.length ? sch.map(function (it, i) {
      return '<div class="row">' + rowHead(i, 'background.schedule', '区间')
        + '<div class="grid3">'
        + field('background.schedule.' + i + '.from', '开始小时', { number: true, attrs: 'min="0" max="29" step="1"' })
        + field('background.schedule.' + i + '.to', '结束小时', { number: true, attrs: 'min="0" max="29" step="1"' })
        + select('background.schedule.' + i + '.scene', '场景', SCENES)
        + '</div></div>';
    }).join('') : '<p class="hint">暂无，将按 5/8/17/20 点的默认区间</p>';

    var photos = list('background.photos');
    $('#photoList').innerHTML = photos.length ? photos.map(function (it, i) {
      return '<div class="row">' + rowHead(i, 'background.photos', '图片')
        + '<div class="grid2">'
        + select('background.photos.' + i + '.scene', '场景', SCENES)
        + select('background.photos.' + i + '.theme', '主题', [['', '自动（按场景）'], ['light', '亮色文字=深色'], ['dark', '暗色文字=亮色']])
        + '</div>'
        + uploadRow('background.photos.' + i + '.url', '图片地址', 'image/*')
        + '</div>';
    }).join('') : '<p class="hint">暂无，使用程序化渐变天空</p>';
  }

  function renderTimeline() {
    var feeds = list('timeline.feeds');
    feeds = feeds.map(function (f) { return typeof f === 'string' ? { url: f, label: '' } : f; });
    set('timeline.feeds', feeds);
    $('#feedList').innerHTML = feeds.length ? feeds.map(function (it, i) {
      return '<div class="row">' + rowHead(i, 'timeline.feeds', '订阅源')
        + '<div class="grid2">'
        + field('timeline.feeds.' + i + '.url', 'RSS / Atom 地址')
        + field('timeline.feeds.' + i + '.label', '来源名（可留空）')
        + '</div></div>';
    }).join('') : '<p class="hint">暂无，时间轴屏将隐藏</p>';
    $('#timelineFields').innerHTML = field('timeline.limit', '最多显示条数', { number: true, attrs: 'min="1" max="40" step="1"' });
  }

  function renderMusic() {
    $('#musicFields').innerHTML =
      check('music.enabled', '启用播放器')
      + check('music.autoplay', '尝试自动播放（浏览器通常需要一次点击）')
      + '<div class="grid2">'
      + field('music.volume', '默认音量 0~1', { number: true, attrs: 'min="0" max="1" step="0.05"' })
      + field('music.neteaseApiBase', '网易云 API 地址', { placeholder: '例如 http://127.0.0.1:3000' })
      + '</div>';

    var tracks = list('music.tracks');
    $('#trackList').innerHTML = tracks.length ? tracks.map(function (it, i) {
      var kind = it.type || 'file';
      return '<div class="row">' + rowHead(i, 'music.tracks', '曲目')
        + '<div class="grid3">'
        + select('music.tracks.' + i + '.type', '类型', [['file', '上传文件'], ['url', '音频直链'], ['netease', '网易云 ID']])
        + field('music.tracks.' + i + '.title', '标题')
        + field('music.tracks.' + i + '.artist', '歌手')
        + '</div>'
        + (kind === 'netease'
          ? '<div class="grid2">' + field('music.tracks.' + i + '.id', '歌曲 ID 或分享链接')
            + '<p><button type="button" data-netease="' + i + '">查询歌曲信息</button></p></div>'
          : uploadRow('music.tracks.' + i + '.src', '音频地址', 'audio/*'))
        + field('music.tracks.' + i + '.cover', '封面地址（可留空）')
        + '</div>';
    }).join('') : '<p class="hint">暂无曲目</p>';
  }

  function renderAccount() {
    $('#pwdFields').innerHTML =
      '<label>原密码<input type="password" id="oldPwd"></label>'
      + '<label>新密码（至少 6 位）<input type="password" id="newPwd"></label>';
  }

  function renderFiles() {
    api('GET', '/api/uploads').then(function (data) {
      var files = data.files || [];
      $('#fileList').innerHTML = (files.length
        ? '<p class="hint">删除前请先确认没有页面正在引用它。</p>'
        : '<p class="hint">还没有上传过文件。</p>')
        + files.map(function (f) {
          var isImg = /\.(png|jpe?g|webp|gif|avif|svg)$/i.test(f.name);
          return '<div class="file-item">'
            + (isImg ? '<img class="thumb" src="' + esc(f.url) + '" alt="">' : '')
            + '<a href="' + esc(f.url) + '" target="_blank" rel="noopener">' + esc(f.name) + '</a>'
            + '<span class="hint">' + Math.round(f.size / 1024) + ' KB</span>'
            + '<button type="button" class="remove" data-rmfile="' + esc(f.name) + '">删除</button>'
            + '</div>';
        }).join('');
    }).catch(function (err) { status('读取文件列表失败：' + err.message, 'err'); });
  }

  function renderAll() {
    renderSite(); renderSocial(); renderNav(); renderFooter();
    renderBackground(); renderTimeline(); renderMusic(); renderAccount();
    /* 支持用 #sec-xxx 直接跳到某个区块（表单是异步渲染的，要等渲染完再滚） */
    if (location.hash) {
      var target = document.querySelector(location.hash);
      if (target) target.scrollIntoView({ block: 'start' });
    }
  }

  /* ---------------- 收集输入 ---------------- */
  function collect() {
    var out = clone(CFG);
    $$('[data-path]').forEach(function (el) {
      var path = el.getAttribute('data-path');
      var val = el.type === 'checkbox' ? el.checked : el.value;
      if (el.hasAttribute('data-number')) {
        var n = parseFloat(val);
        val = isNaN(n) ? 0 : n;
      }
      set(path, val, out);
    });
    return out;
  }

  /* ---------------- 交互 ---------------- */
  function uploadTo(path, accept) {
    var input = document.createElement('input');
    input.type = 'file';
    if (accept) input.accept = accept;
    input.onchange = function () {
      var file = input.files && input.files[0];
      if (!file) return;
      if (file.size > 32 * 1024 * 1024) { status('文件超过 32 MB', 'err'); return; }
      status('正在上传 ' + file.name + ' …');
      var reader = new FileReader();
      reader.onload = function () {
        api('POST', '/api/upload', { name: file.name, data: reader.result }).then(function (data) {
          var box = document.querySelector('[data-path="' + path + '"]');
          if (box) box.value = data.url;
          status('上传完成：' + data.url, 'ok');
          renderFiles();
        }).catch(function (err) { status('上传失败：' + err.message, 'err'); });
      };
      reader.readAsDataURL(file);
    };
    input.click();
  }

  function addRow(path) {
    var defaults = {
      social: { label: '', href: '', icon: 'link' },
      nav: { label: '', caption: '', href: '', icon: 'link' },
      'footer.links': { label: '', href: '' },
      'background.schedule': { from: 8, to: 17, scene: 'day' },
      'background.photos': { scene: 'day', url: '', theme: '' },
      'timeline.feeds': { url: '', label: '' },
      'music.tracks': { type: 'file', title: '', artist: '', src: '', cover: '', id: '' }
    }[path];
    var arr = list(path);
    arr.push(clone(defaults || {}));
    set(path, arr);
    renderAll();
  }

  function delRow(path, index) {
    var arr = list(path);
    arr.splice(index, 1);
    set(path, arr);
    renderAll();
  }

  function bind() {
    document.addEventListener('click', function (ev) {
      var el = ev.target.closest ? ev.target.closest('button, a') : null;
      if (!el) return;

      if (el.hasAttribute('data-add')) { addRow(el.getAttribute('data-add')); return; }
      if (el.hasAttribute('data-del')) { delRow(el.getAttribute('data-del'), +el.getAttribute('data-i')); return; }
      if (el.hasAttribute('data-upload')) { uploadTo(el.getAttribute('data-upload'), el.getAttribute('data-accept')); return; }
      if (el.hasAttribute('data-rmfile')) {
        if (!confirm('删除文件 ' + el.getAttribute('data-rmfile') + '？')) return;
        api('DELETE', '/api/upload?name=' + encodeURIComponent(el.getAttribute('data-rmfile')))
          .then(function () { renderFiles(); status('已删除', 'ok'); })
          .catch(function (err) { status('删除失败：' + err.message, 'err'); });
        return;
      }
      if (el.hasAttribute('data-netease')) {
        var i = +el.getAttribute('data-netease');
        var box = document.querySelector('[data-path="music.tracks.' + i + '.id"]');
        var raw = (box && box.value || '').trim();
        var idMatch = raw.match(/(\d{4,})/);
        if (!idMatch) { status('请填写歌曲 ID 或分享链接', 'err'); return; }
        var id = idMatch[1];
        if (box) box.value = id;
        status('正在查询歌曲 ' + id + ' …');
        api('GET', '/api/netease/song?id=' + encodeURIComponent(id)).then(function (data) {
          var put = function (suffix, val) {
            var target = document.querySelector('[data-path="music.tracks.' + i + '.' + suffix + '"]');
            if (target) target.value = val || '';
          };
          put('title', data.name); put('artist', data.artist); put('cover', data.cover);
          status('已填入歌曲信息' + (data.url ? '' : '（暂未取到播放地址，可能需登录或版权受限）'), data.url ? 'ok' : 'err');
        }).catch(function (err) { status('查询失败：' + err.message, 'err'); });
        return;
      }
      if (el.id === 'saveBtn') {
        status('正在保存 …');
        api('PUT', '/api/site', { config: collect() }).then(function (data) {
          CFG = data.config || CFG;
          renderAll();
          status('已保存，刷新首页即可看到效果', 'ok');
        }).catch(function (err) {
          status('保存失败：' + err.message + (err.status === 401 ? '（登录已过期，请重新登录）' : ''), 'err');
        });
        return;
      }
      if (el.id === 'reloadBtn') { loadConfig(); return; }
      if (el.id === 'logoutBtn') {
        api('POST', '/api/logout').then(function () { location.reload(); });
        return;
      }
      if (el.id === 'pwdBtn') {
        var oldPwd = $('#oldPwd').value, newPwd = $('#newPwd').value;
        api('POST', '/api/password', { old: oldPwd, new: newPwd })
          .then(function () { status('密码已修改', 'ok'); $('#oldPwd').value = ''; $('#newPwd').value = ''; })
          .catch(function (err) { status('修改失败：' + err.message, 'err'); });
        return;
      }
      if (el.id === 'previewBtn') {
        status('正在抓取订阅源 …');
        api('GET', '/api/timeline?refresh=1').then(function (data) {
          var items = data.items || [];
          $('#timelinePreview').innerHTML = '<h3>抓取结果（' + items.length + ' 条）</h3>'
            + (items.length ? '<ol>' + items.map(function (it) {
              return '<li>' + esc(it.date ? it.date.slice(0, 10) : '无日期') + ' ｜ ' + esc(it.source || '')
                + ' ｜ <a href="' + esc(it.link) + '" target="_blank" rel="noopener">' + esc(it.title) + '</a></li>';
            }).join('') + '</ol>' : '<p class="hint">没有抓到条目。</p>')
            + ((data.errors && data.errors.length) ? '<p class="hint">错误：' + data.errors.map(esc).join('；') + '</p>' : '');
          status('抓取完成', 'ok');
        }).catch(function (err) { status('抓取失败：' + err.message, 'err'); });
        return;
      }
      if (el.id === 'refreshFiles') { renderFiles(); return; }
      if (el.id === 'loginBtn') { login(); return; }
    });

    $('#password').addEventListener('keydown', function (ev) { if (ev.key === 'Enter') login(); });
  }

  function login() {
    api('POST', '/api/login', { password: $('#password').value }).then(function () {
      status('');
      loadConfig();
    }).catch(function (err) { status('登录失败：' + err.message, 'err'); });
  }

  function loadConfig() {
    api('GET', '/api/site?full=1').then(function (data) {
      CFG = data.config || {};
      $('#login').hidden = true;
      $('#editor').hidden = false;
      renderAll();
      renderFiles();
      status('配置已载入');
    }).catch(function (err) {
      if (err.status === 401) {
        $('#login').hidden = false;
        $('#editor').hidden = true;
        status('请先登录');
      } else {
        status('载入失败：' + err.message, 'err');
      }
    });
  }

  bind();
  loadConfig();
})();
