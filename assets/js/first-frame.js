/* =============================================================
 * first-frame.js · 首帧定场景
 * -------------------------------------------------------------
 * 原本内联在 index.html <head> 里，为了让 CSP 能收紧到
 * script-src 'self'（禁止一切内联脚本）而外移到这里。
 * 必须紧跟在 config.js 之后、任何渲染脚本之前加载。
 * 时间表与 config.js 的 background.schedule 保持同一份数据。
 * ============================================================= */
(function () {
  'use strict';

  var bg = (window.SITE_CONFIG || {}).background || {};
  var q = new URLSearchParams(location.search).get('scene');
  var key = null;
  if (q && q !== 'auto' && ['dawn', 'day', 'dusk', 'night'].indexOf(q) >= 0) key = q;
  if (!key && bg.mode === 'manual' && bg.manualScene) key = bg.manualScene;
  if (!key) {
    var d = new Date(), t = d.getHours() + d.getMinutes() / 60, table = bg.schedule || [];
    key = 'night';
    for (var i = 0; i < table.length; i++) {
      var s = table[i];
      if ((t >= s.from && t < s.to) || (s.to > 24 && t < s.to - 24)) { key = s.scene; break; }
    }
  }
  document.documentElement.setAttribute('data-scene', key);
  document.documentElement.setAttribute('data-theme', key === 'day' ? 'light' : 'dark');
})();
