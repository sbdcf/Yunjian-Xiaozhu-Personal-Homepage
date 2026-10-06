/* =============================================================
 * music.js · 背景音乐播放器（原生 <audio>，零依赖）
 * -------------------------------------------------------------
 * 曲目三种来源：
 *   type: 'file'    手动上传的音频（/uploads/xxx.mp3）
 *   type: 'url'     任意音频直链
 *   type: 'netease' 网易云歌曲 ID —— 由服务端 /api/music/stream?i=N
 *                   代理解析播放地址（需自建 NeteaseCloudMusicApi）
 * 无曲目 / 未启用时整个控件隐藏。
 * ============================================================= */
(function () {
  'use strict';

  var STORE_KEY = 'homepage-music';

  function icon(name) {
    return '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
  }
  function $(sel) { return document.querySelector(sel); }

  var player = {
    cfg: null, tracks: [], index: 0, audio: null, box: null, ready: false,

    init: function (cfg) {
      if (!cfg || !cfg.enabled) return;
      var tracks = (cfg.tracks || []).filter(function (t) {
        return t && (t.src || t.id);
      });
      if (!tracks.length) return;

      this.cfg = cfg;
      this.tracks = tracks;
      this.box = $('#music');
      if (!this.box) return;

      var saved = {};
      try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (e) { saved = {}; }
      this.index = Math.min(Math.max(parseInt(saved.index, 10) || 0, 0), tracks.length - 1);

      var volume = typeof saved.volume === 'number' ? saved.volume
        : (typeof cfg.volume === 'number' ? cfg.volume : 0.5);

      this.box.innerHTML =
        '<audio id="musicAudio" preload="none"></audio>'
        + '<button class="music-btn" id="musicPlay" type="button" aria-label="播放/暂停">' + icon('play') + '</button>'
        + '<button class="music-btn music-btn--side" id="musicPrev" type="button" aria-label="上一首">' + icon('prev') + '</button>'
        + '<button class="music-btn music-btn--side" id="musicNext" type="button" aria-label="下一首">' + icon('next') + '</button>'
        + '<span class="music-title" id="musicTitle"></span>'
        + '<label class="music-vol" aria-label="音量">'
        + '<input id="musicVolume" type="range" min="0" max="1" step="0.01" value="' + volume + '"></label>';

      this.audio = $('#musicAudio');
      this.audio.volume = volume;
      this.box.hidden = false;

      var self = this;
      $('#musicPlay').addEventListener('click', function () { self.toggle(); });
      $('#musicPrev').addEventListener('click', function () { self.step(-1); });
      $('#musicNext').addEventListener('click', function () { self.step(1); });
      $('#musicVolume').addEventListener('input', function (ev) {
        self.audio.volume = parseFloat(ev.target.value);
        self.save();
      });
      this.audio.addEventListener('ended', function () { self.step(1); });
      this.audio.addEventListener('error', function () { self.fail(); });
      this.audio.addEventListener('play', function () { self.paint(true); });
      this.audio.addEventListener('pause', function () { self.paint(false); });

      this.load(false);
      this.paint(false);
      this.ready = true;

      /* 浏览器要求用户手势后才能出声：先试一次，失败则等第一次交互 */
      if (cfg.autoplay) this.armAutoplay();
    },

    /* 当前曲目的播放地址 */
    srcOf: function (track) {
      if ((track.type || '').toLowerCase() === 'netease') {
        return '/api/music/stream?i=' + this.index;      // 服务端解析后 302
      }
      return track.src || '';
    },

    load: function (autoplay) {
      var track = this.tracks[this.index];
      if (!track) return;
      var src = this.srcOf(track);
      if (src) this.audio.setAttribute('src', src); else this.audio.removeAttribute('src');
      this.paint(false);
      this.save();
      if (autoplay) this.play();
    },

    play: function () {
      var self = this;
      var track = this.tracks[this.index];
      if (!track) return;
      if (!this.audio.getAttribute('src')) this.load(false);
      var p = this.audio.play();
      if (p && p.catch) {
        p.catch(function () {
          self.armAutoplay();                          // 被浏览器拦下：等一次用户操作
          self.paint(false);
        });
      }
    },

    toggle: function () {
      if (this.audio.paused) this.play(); else this.audio.pause();
    },

    step: function (dir) {
      if (!this.tracks.length) return;
      this.index = (this.index + dir + this.tracks.length) % this.tracks.length;
      this.load(!this.audio.paused || this.pendingAutoplay === true);
    },

    fail: function () {
      /* 单曲失败（例如网易云没取到地址）：提示并自动跳过，只跳一轮 */
      this.failed = (this.failed || 0) + 1;
      if (this.failed < this.tracks.length) { this.step(1); }
      else { this.failed = 0; this.paint(false); }
    },

    armAutoplay: function () {
      var self = this;
      this.pendingAutoplay = true;
      var once = function () {
        document.removeEventListener('click', once, true);
        document.removeEventListener('keydown', once, true);
        document.removeEventListener('touchstart', once, true);
        if (self.pendingAutoplay) self.play();
      };
      document.addEventListener('click', once, true);
      document.addEventListener('keydown', once, true);
      document.addEventListener('touchstart', once, true);
    },

    save: function () {
      try {
        localStorage.setItem(STORE_KEY, JSON.stringify({
          index: this.index, volume: this.audio ? this.audio.volume : 0.5
        }));
      } catch (e) { /* 隐私模式下忽略 */ }
    },

    paint: function (playing) {
      if (!this.box) return;
      var track = this.tracks[this.index] || {};
      var btn = $('#musicPlay');
      if (btn) btn.innerHTML = icon(playing ? 'pause' : 'play');
      var title = $('#musicTitle');
      if (title) {
        var text = track.title || '未命名';
        if (track.artist) text += ' · ' + track.artist;
        title.textContent = text;
        title.title = text + (playing ? '（播放中）' : '');
      }
      this.box.classList.toggle('is-playing', !!playing);
    }
  };

  window.SiteMusic = player;
})();
