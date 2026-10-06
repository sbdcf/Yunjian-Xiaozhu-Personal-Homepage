/* =============================================================
 * config.js · 离线默认配置（没有后台时页面用这一份）
 * -------------------------------------------------------------
 * 运行时优先读取后台的 /api/site（数据存在 site.json）。
 *   · 有后台：内容以后台保存的为准，本文件只补缺省项
 *   · 无后台（file:// 打开或纯静态部署）：用本文件的内容
 * 所以改内容推荐用后台；只有行为参数（behavior/debug）需要动这里。
 * ============================================================= */
window.SITE_CONFIG = {
  /* ---------- 站点与个人信息（第一屏） ---------- */
  site: { name: '云间小筑' },
  author: {
    name: '你的名字',
    initials: 'YN',                  // 头像字母（留空则取姓名首字）
    role: '前端工程师 · 独立开发者',
    intro: '',                       // 留空则不显示这一行
    status: '',                      // 留空则不显示状态行
    avatar: ''                       // 头像图片地址，留空用字母占位
  },

  /* ---------- 社交 / 订阅（图标按钮） ---------- */
  social: [
    { label: 'GitHub', href: 'https://github.com/', icon: 'github' },
    { label: '邮箱', href: 'mailto:hi@example.com', icon: 'mail' },
    { label: 'RSS', href: '/feed.xml', icon: 'rss' }
  ],

  /* ---------- 导航（第二屏） ---------- */
  nav: [
    { label: '博客', caption: '文章与笔记', href: '/blog/', icon: 'pen' },
    { label: '项目', caption: '作品与实验', href: '/projects/', icon: 'cube' },
    { label: '相册', caption: '光与影', href: '/photos/', icon: 'camera' },
    { label: '关于', caption: '我是谁', href: '/about/', icon: 'user' },
    { label: '归档', caption: '时间线', href: '/archive/', icon: 'clock' },
    { label: '联系', caption: '说点什么', href: '/contact/', icon: 'send' }
  ],

  /* ---------- 末屏：版权 / 次级链接 / 备案 ---------- */
  footer: {
    copyright: '© 2025 你的名字',    // 留空则不显示
    links: [
      { label: '站点地图', href: '/sitemap.xml' },
      { label: '关于', href: '/about/' },
      { label: 'RSS', href: '/feed.xml' }
    ],
    icp: { text: '', href: 'https://beian.miit.gov.cn/' },   // 留空则整行隐藏
    police: { text: '', href: '' }                           // 留空则整行隐藏
  },

  /* ---------- 背景：时间感知 + 手动切换 ---------- */
  background: {
    mode: 'auto',                    // 'auto' 跟随时间；'manual' 固定 manualScene
    manualScene: 'day',
    smooth: 1200,                    // 场景过渡时长（毫秒）
    animate: true,                   // 星尘 / 云带的轻微动画
    schedule: [
      { from: 5, to: 8, scene: 'dawn' },     // 05:00–07:59
      { from: 8, to: 17, scene: 'day' },     // 08:00–16:59
      { from: 17, to: 20, scene: 'dusk' },   // 17:00–19:59
      { from: 20, to: 29, scene: 'night' }   // 20:00–04:59（+24 便于跨天判断）
    ],
    /* 自定义图片背景：按场景指定；theme 可强制该场景用亮色/暗色文字
       例：{ scene: 'day', url: '/uploads/bg.jpg', theme: 'dark' } */
    photos: []
  },

  /* ---------- 时间轴（RSS）：feeds 为空则整屏隐藏 ---------- */
  timeline: {
    feeds: [],                       // [{ url: 'https://…/feed.xml', label: '博客' }]
    limit: 8
  },

  /* ---------- 背景音乐 ---------- */
  music: {
    enabled: false,
    autoplay: false,                 // 浏览器通常要求先有一次点击
    volume: 0.5,
    neteaseApiBase: '',              // 自建 NeteaseCloudMusicApi 地址
    tracks: []                       // [{type:'file|url|netease', title, artist, src, id, cover}]
  },

  /* ---------- 交互行为 ---------- */
  behavior: {
    snapWheel: true,                 // 滚轮一次切换一屏（触屏/减动效设备自动交回原生滚动）
    wheelThreshold: 32,
    wheelLock: 800
  },

  /* ---------- 调试：文字占比自检（B 键 / ?hud=1） ---------- */
  debug: { textBudget: true, showHud: false }
};
