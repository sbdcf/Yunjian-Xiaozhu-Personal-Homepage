#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py · 极简后台 + 站点托管（只用 Python 标准库，零依赖）

同时做三件事：
  1. 静态托管本站（支持 Range，音频可拖动进度）
  2. 提供配置读写、图片/音乐上传接口（后台用）
  3. 服务端抓取 RSS 生成时间轴；代理网易云音乐取播放地址

启动：
    python server.py                     # 默认 127.0.0.1:8787
    python server.py --port 9000
    python server.py --host 0.0.0.0      # 对外暴露（务必先设强密码！）
    python server.py --reset-password    # 交互式重置后台密码（走 getpass，不出现在命令行）
    python server.py --secure-cookie     # 强制按 HTTPS 下发 Secure Cookie（反代忘了传头时用）
    python server.py --allow-local-netease  # 允许网易云 API 指向本机/内网（默认禁止，见 SSRF 防护）

首次启动会生成 admin.json 并在控制台打印随机密码。
配置存于 site.json（不存在则用 site.default.json 的内容）。
上传文件存于 uploads/。
审计日志写于 audit.log（不含密码）。

安全基线（对应 docs/SECURITY.md）：
  · 全站安全响应头（CSP / nosniff / frame-ancestors / Referrer-Policy …）
  · PBKDF2 口令哈希 + 旧格式透明迁移；登录失败计数与限速；密码下限 12 位
  · 会话 token 带签发时间，改密码/退出登录立即吊销旧会话
  · 静态托管改为目录白名单（源码、JSON、日志、临时文件一律不可下载）
  · RSS / 网易云请求做 SSRF 校验（非公网地址拒绝，禁止跳转到内网）
  · 上传按扩展名 + 文件魔数双重校验；.svg 不再接受
"""

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- 路径与常量
ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_JSON = os.path.join(ROOT, 'site.json')
DEFAULT_JSON = os.path.join(ROOT, 'site.default.json')
ADMIN_JSON = os.path.join(ROOT, 'admin.json')
UPLOAD_DIR = os.path.join(ROOT, 'uploads')
AUDIT_LOG = os.path.join(ROOT, 'audit.log')

MAX_BODY = 48 * 1024 * 1024          # 请求体上限（base64 上传约可放 30MB 文件）
MAX_UPLOAD = 32 * 1024 * 1024        # 单个文件上限
MAX_WORKERS = 32                     # 并发处理线程上限，超出直接 503
FEED_TTL = 600                       # RSS 缓存秒数
NETEASE_TTL = 300                    # 网易云播放地址缓存秒数
UA = 'Mozilla/5.0 (compatible; HomepageAdmin/1.0)'
COOKIE_NAME = 'hp_admin'
SECURE_COOKIE_NAME = '__Host-hp_admin'   # 仅在 HTTPS 下使用（Secure; Path=/; 无 Domain）
TOKEN_TTL = 24 * 3600                # 登录态 24 小时（个人后台够用，暴露面更小）
FORCE_SECURE_COOKIE = False          # --secure-cookie
ALLOW_LOCAL_NETEASE = False          # --allow-local-netease

PBKDF2_ITERATIONS = 200_000
PASSWORD_ALGO = 'pbkdf2-sha256'
LEGACY_ALGO = 'sha256'
MIN_PASSWORD_LEN = 12
LOGIN_MAX_FAILS = 5                  # 同一 IP 在 10 分钟内允许的失败次数
LOGIN_WINDOW = 600

ALLOWED_EXT = {
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif',                # 图片（不含 .svg）
    '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.flac',         # 音频
}
# 静态托管白名单：只有这些目录/文件可以被 HTTP 读取
SERVE_FILES = {'index.html', 'admin.html'}
SERVE_DIRS = {'assets', 'uploads', 'docs/img'}
BLOCKED_EXT = {'.tmp', '.py', '.json', '.log', '.bak', '.md', '.svg'}
FORBIDDEN_KEYS = {'__proto__', 'constructor', 'prototype'}
CONFIG_KEYS = {'site', 'author', 'social', 'nav', 'footer', 'background', 'timeline', 'music'}
PUBLIC_KEYS = ('site', 'author', 'social', 'nav', 'footer', 'background')

SECURITY_HEADERS = (
    ('X-Content-Type-Options', 'nosniff'),
    ('X-Frame-Options', 'DENY'),
    ('Referrer-Policy', 'no-referrer'),
    ('Permissions-Policy', 'camera=(), microphone=(), geolocation=()'),
    ('Cross-Origin-Opener-Policy', 'same-origin'),
    ('Content-Security-Policy',
     "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
     "script-src 'self'; style-src 'self' 'unsafe-inline'; "
     "img-src 'self' data: https:; media-src 'self' https:; connect-src 'self'"),
)

_lock = threading.Lock()
_login_lock = threading.Lock()
_audit_lock = threading.Lock()
_login_fails = {}


# ---------------------------------------------------------------- 配置存取
def _read_json(path, fallback):
    try:
        with open(path, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
        return data if isinstance(data, (dict, list)) else fallback
    except (OSError, ValueError):
        return fallback


def _write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _merge(base, override):
    """递归合并：字典合并，列表与标量整体替换；危险原型键直接丢弃"""
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, val in override.items():
            if key in FORBIDDEN_KEYS:
                continue
            out[key] = _merge(base.get(key), val) if key in base else val
        return out
    return override


def _sanitize(value):
    """去掉 __proto__ / constructor / prototype 这类会污染原型的键"""
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items() if k not in FORBIDDEN_KEYS}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _sanitize_config(cfg):
    """顶层键白名单 + 递归去除危险键：site.json 一律当不可信输入处理"""
    if not isinstance(cfg, dict):
        return {}
    return {k: _sanitize(v) for k, v in cfg.items() if k in CONFIG_KEYS}


def load_config():
    """默认配置 + site.json 覆盖（site.json 缺失时自动生成一份）"""
    default = _read_json(DEFAULT_JSON, {})
    if not os.path.exists(SITE_JSON):
        _write_json(SITE_JSON, {})
    return _sanitize_config(_merge(default, _read_json(SITE_JSON, {})))


def save_config(patch):
    """把 patch 合并进 site.json；未知顶层键与危险键丢弃"""
    if not isinstance(patch, dict):
        raise ValueError('config 必须是对象')
    with _lock:
        stored = _read_json(SITE_JSON, {})
        merged = _merge(stored, _sanitize(patch))
        merged = _sanitize_config(merged)
        _write_json(SITE_JSON, merged)
        return merged


def public_config(cfg):
    """首页渲染所需字段的白名单投影（不泄露 feeds / 内网 API 地址等运维信息）"""
    out = {k: cfg.get(k) for k in PUBLIC_KEYS if k in cfg}
    tl = cfg.get('timeline') or {}
    out['timeline'] = {'limit': tl.get('limit', 8)}
    music = cfg.get('music') or {}
    out['music'] = {k: music.get(k) for k in ('enabled', 'autoplay', 'volume', 'tracks')
                    if k in music}
    return out


# ---------------------------------------------------------------- 审计日志
def audit(action, detail='', ip='-'):
    """写一行审计日志：时间 / IP / 动作 / 细节；绝不写密码"""
    detail = re.sub(r'[\t\r\n]+', ' ', str(detail or ''))[:300]
    line = '%s\t%s\t%s\t%s\n' % (
        datetime.now(timezone.utc).isoformat(timespec='seconds'), ip or '-', action, detail)
    try:
        with _audit_lock:
            with open(AUDIT_LOG, 'a', encoding='utf-8') as fp:
                fp.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------- 登录鉴权
def load_admin():
    data = _read_json(ADMIN_JSON, {})
    return data if isinstance(data, dict) else {}


def _tighten_admin_permissions():
    """Linux: 0600；Windows: chmod 静默失效，用 icacls 只保留当前用户"""
    try:
        os.chmod(ADMIN_JSON, 0o600)
    except OSError:
        pass
    if os.name != 'nt':
        return
    user = os.environ.get('USERNAME') or ''
    if not user:
        return
    try:
        subprocess.run(
            ['icacls', ADMIN_JSON, '/inheritance:r', '/grant:r', user + ':F'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def save_admin(data):
    with _lock:
        _write_json(ADMIN_JSON, data)
        _tighten_admin_permissions()


def hash_password(password, salt, iterations=PBKDF2_ITERATIONS):
    return hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt.encode('utf-8'), iterations).hex()


def _legacy_hash(password, salt):
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()


def _validate_new_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise ValueError('密码至少 %d 位' % MIN_PASSWORD_LEN)


def _set_password(data, password):
    data['salt'] = secrets.token_hex(16)
    data['algo'] = PASSWORD_ALGO
    data['iter'] = PBKDF2_ITERATIONS
    data['password_hash'] = hash_password(password, data['salt'])


def verify_password(password):
    """校验口令；旧版单轮 SHA256 记录校验成功后立即升级为 PBKDF2（用户无感）"""
    data = load_admin()
    stored = data.get('password_hash') or ''
    salt = data.get('salt') or ''
    if not stored or not salt:
        return False
    algo = data.get('algo') or LEGACY_ALGO
    if algo == PASSWORD_ALGO:
        try:
            iterations = int(data.get('iter') or PBKDF2_ITERATIONS)
        except (TypeError, ValueError):
            iterations = PBKDF2_ITERATIONS
        if iterations < 10_000 or iterations > 10_000_000:
            iterations = PBKDF2_ITERATIONS
        ok = hmac.compare_digest(hash_password(password, salt, iterations), stored)
    else:
        ok = hmac.compare_digest(_legacy_hash(password, salt), stored)
    if ok and algo != PASSWORD_ALGO:
        data['algo'] = PASSWORD_ALGO
        data['iter'] = PBKDF2_ITERATIONS
        data['password_hash'] = hash_password(password, salt)
        try:
            save_admin(data)
        except OSError:
            pass
    return ok


def init_admin(password=None):
    """生成/更新 admin.json；返回本次是否写入、以及明文密码（仅新建或重置时）"""
    data = load_admin()
    plain = None
    if not data.get('password_hash') or password:
        if password:
            _validate_new_password(password)
            plain = password
        else:
            plain = secrets.token_urlsafe(18)
        data['secret'] = data.get('secret') or secrets.token_hex(32)
        data.setdefault('token_version', 0)
        if password:
            bump_token_version(data)                 # 重置密码后旧会话立即作废
        _set_password(data, plain)
        save_admin(data)
        return True, plain
    if not data.get('secret'):
        data['secret'] = secrets.token_hex(32)
        save_admin(data)
    return False, None


def _token_version(data):
    try:
        return int(data.get('token_version') or 0)
    except (TypeError, ValueError):
        return 0


def bump_token_version(data):
    """改密码 / 退出登录时让所有旧 token 立即作废（不依赖秒级时钟）"""
    data['token_version'] = _token_version(data) + 1


def make_token():
    now = int(time.time())
    exp = now + TOKEN_TTL
    data = load_admin()
    payload = '%d.%d.%d' % (exp, now, _token_version(data))
    secret = data.get('secret', '').encode('utf-8')
    sig = hmac.new(secret, payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return payload + '.' + sig


def check_token(token):
    if not token or token.count('.') != 3:
        return False
    exp_s, iat_s, ver_s, sig = token.split('.')
    try:
        exp = int(exp_s)
        iat = int(iat_s)
        ver = int(ver_s)
    except ValueError:
        return False
    now = time.time()
    if exp < now or iat > now + 300:
        return False
    data = load_admin()
    if ver != _token_version(data):              # 版本号一变，旧 token 全部失效
        return False
    secret = data.get('secret', '').encode('utf-8')
    if not secret:
        return False
    expect = hmac.new(secret, ('%s.%s.%s' % (exp_s, iat_s, ver_s)).encode('utf-8'),
                      hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)


def _login_recent(ip):
    now = time.time()
    with _login_lock:
        queue = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW]
        if queue:
            _login_fails[ip] = queue
        else:
            _login_fails.pop(ip, None)
        return queue


def login_blocked(ip):
    return len(_login_recent(ip)) >= LOGIN_MAX_FAILS


def note_login_fail(ip):
    with _login_lock:
        _login_fails.setdefault(ip, []).append(time.time())


def clear_login_fails(ip):
    with _login_lock:
        _login_fails.pop(ip, None)


# ---------------------------------------------------------------- SSRF 防护
def _ip_allowed(ip_text, allow_loopback):
    try:
        addr = ipaddress.ip_address(ip_text.split('%', 1)[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped                       # ::ffff:127.0.0.1 这类映射地址按 IPv4 判断
    if addr.is_loopback:
        return allow_loopback
    return not (addr.is_private or addr.is_link_local or addr.is_reserved
                or addr.is_multicast or addr.is_unspecified
                or getattr(addr, 'is_site_local', False))


def is_public_url(url, allow_loopback=False):
    """只允许 http/https，且域名解析出的每个地址都必须是公网地址"""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        return False
    if not 0 < port < 65536:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    ips = [info[4][0] for info in infos]
    return bool(ips) and all(_ip_allowed(ip, allow_loopback) for ip in ips)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """每一跳都重新做 SSRF 校验，禁止 302 到内网/回环"""

    def __init__(self, allow_loopback=False):
        self.allow_loopback = allow_loopback

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        if not is_public_url(newurl, self.allow_loopback):
            raise urllib.error.URLError('拒绝跳转到非公网地址')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(req, timeout=12, allow_loopback=False):
    opener = urllib.request.build_opener(_SafeRedirectHandler(allow_loopback))
    return opener.open(req, timeout=timeout)


# ---------------------------------------------------------------- RSS 时间轴
_feed_cache = {}


def _local_name(tag):
    return tag.rsplit('}', 1)[-1].lower()


def _child_text(node, names):
    for child in node:
        if _local_name(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            if child.get('href'):
                return child.get('href')
    return ''


def strip_html(text, limit=80):
    if not text:
        return ''
    text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', text)
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit] + ('…' if len(text) > limit else '')


def normalize_date(raw):
    """把各种日期格式统一成 ISO8601（带时区则保留）"""
    if not raw:
        return ''
    raw = raw.strip()
    try:                                             # RFC 822：Wed, 02 Oct 2024 08:00:00 GMT
        dt = parsedate_to_datetime(raw)
        if dt:
            return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:                                             # ISO 8601：2024-10-02T08:00:00Z
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    m = re.match(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', raw)
    if m:
        return '%04d-%02d-%02dT00:00:00+00:00' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return ''


def _safe_link(raw):
    """订阅源里的 link 只保留 http/https 绝对地址，其余一律丢弃"""
    if not raw:
        return ''
    raw = str(raw).strip()
    if not raw or re.search(r'[\s"\'<>]', raw):
        return ''
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return ''
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return ''
    return raw


def fetch_feed(url, label=''):
    """抓取并解析一个 RSS/Atom 源，返回条目列表"""
    if not is_public_url(url):
        raise ValueError('订阅源地址不是公网 http(s) 地址')
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/rss+xml, application/xml, text/xml, */*'})
    with safe_urlopen(req, timeout=12) as resp:
        raw = resp.read(4 * 1024 * 1024)
        charset = resp.headers.get_content_charset() or 'utf-8'
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        text = raw.decode(charset, errors='replace')
        root = ET.fromstring(re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', text))

    feed_title = ''
    for node in root.iter():
        if _local_name(node.tag) in ('channel', 'feed'):
            feed_title = _child_text(node, {'title'})
            break

    items = []
    for node in root.iter():
        if _local_name(node.tag) not in ('item', 'entry'):
            continue
        link = _safe_link(_child_text(node, {'link', 'id', 'guid'}))
        if link.startswith('http') is False and _local_name(node.tag) == 'entry':
            link = ''
        title = _child_text(node, {'title'}) or '(无标题)'
        date = normalize_date(_child_text(node, {'pubdate', 'published', 'updated', 'date', 'dc:date', 'created'}))
        summary = strip_html(_child_text(node, {'description', 'summary', 'content', 'encoded'}), 70)
        items.append({
            'title': strip_html(title, 90),
            'link': link,
            'date': date,
            'summary': summary,
            'source': label or feed_title or urllib.parse.urlparse(url).netloc,
        })
        if len(items) >= 40:
            break
    return items, (label or feed_title or url)


def _feed_error_text(label, exc):
    """对外只回分类，细节写 stderr（避免把内网地址/端口回显给调用方）"""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        kind = '抓取超时'
    elif isinstance(exc, urllib.error.HTTPError):
        kind = 'HTTP %d' % exc.code
    elif isinstance(exc, urllib.error.URLError):
        kind = '连接失败'
    elif isinstance(exc, ET.ParseError):
        kind = '内容不是合法 RSS/Atom'
    elif isinstance(exc, ValueError):
        kind = '地址被拒绝'
    else:
        kind = '抓取失败'
    return '%s：%s' % (label or '订阅源', kind)


def build_timeline(cfg, refresh=False):
    tl = cfg.get('timeline') or {}
    feeds = tl.get('feeds') or []
    try:
        limit = max(1, min(40, int(tl.get('limit') or 8)))
    except (TypeError, ValueError):
        limit = 8
    items, errors, now = [], [], time.time()
    for entry in feeds:
        if isinstance(entry, str):
            url, label = entry.strip(), ''
        else:
            url = (entry.get('url') or '').strip()
            label = (entry.get('label') or '').strip()
        if not url:
            continue
        cached = _feed_cache.get(url)
        if cached and not refresh and now - cached['ts'] < FEED_TTL:
            items.extend(cached['items'])
            errors.extend(cached['errors'])
            continue
        try:
            got, resolved_label = fetch_feed(url, label)
            for it in got:
                it['source'] = it.get('source') or resolved_label
            _feed_cache[url] = {'ts': now, 'items': got, 'errors': []}
            items.extend(got)
        except Exception as exc:                                   # noqa: BLE001
            sys.stderr.write('[timeline] %s: %s: %s\n' % (
                label or url, exc.__class__.__name__, str(exc)[:200]))
            msg = _feed_error_text(label or urllib.parse.urlparse(url).netloc, exc)
            _feed_cache[url] = {'ts': now, 'items': [], 'errors': [msg]}
            errors.append(msg)
    items.sort(key=lambda it: it.get('date') or '', reverse=True)
    return {
        'items': items[:limit],
        'total': len(items),
        'errors': errors,
        'updatedAt': datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------- 网易云音乐
_song_cache = {}


def netease_base(cfg):
    music = cfg.get('music') or {}
    return (music.get('neteaseApiBase') or '').strip().rstrip('/')


def netease_get(base, path, params):
    url = '%s%s?%s' % (base, path, urllib.parse.urlencode(params))
    if not is_public_url(url, allow_loopback=ALLOW_LOCAL_NETEASE):
        raise RuntimeError(
            '网易云 API 地址不被允许（默认只允许公网地址；本机部署请启动时加 --allow-local-netease）')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with safe_urlopen(req, timeout=12, allow_loopback=ALLOW_LOCAL_NETEASE) as resp:
        return json.loads(resp.read(2 * 1024 * 1024).decode('utf-8', errors='replace'))


def netease_song(base, song_id):
    """返回 {url, name, artist, cover}；接口不可用时抛异常"""
    info = {'url': '', 'name': '', 'artist': '', 'cover': ''}
    try:
        detail = netease_get(base, '/song/detail', {'ids': song_id})
        songs = detail.get('songs') or []
        if songs:
            s = songs[0]
            info['name'] = s.get('name') or ''
            info['artist'] = ' / '.join(a.get('name', '') for a in (s.get('ar') or []))
            info['cover'] = ((s.get('al') or {}).get('picUrl') or '')
    except Exception:                                              # noqa: BLE001
        pass
    for path, params in (('/song/url/v1', {'id': song_id, 'level': 'standard'}),
                         ('/song/url', {'id': song_id})):
        try:
            data = netease_get(base, path, params)
            rows = data.get('data') or []
            url = (rows[0].get('url') if rows else '') or ''
            if url:
                info['url'] = url
                break
        except Exception:                                          # noqa: BLE001
            continue
    if not info['url'] and not info['name']:
        raise RuntimeError('网易云接口无响应或未配置（需自行部署 NeteaseCloudMusicApi 并填写地址）')
    return info


def resolve_stream(cfg, index):
    """按曲目下标解析出可播放地址"""
    tracks = ((cfg.get('music') or {}).get('tracks') or [])
    if index < 0 or index >= len(tracks):
        raise KeyError('曲目不存在')
    track = tracks[index]
    kind = (track.get('type') or 'url').lower()
    if kind == 'netease':
        song_id = str(track.get('id') or '').strip()
        if not song_id:
            raise KeyError('缺少网易云歌曲 ID')
        base = netease_base(cfg)
        if not base:
            raise RuntimeError('未配置网易云 API 地址')
        key = base + '|' + song_id
        cached = _song_cache.get(key)
        if cached and time.time() - cached['ts'] < NETEASE_TTL:
            return cached['url']
        info = netease_song(base, song_id)
        if not info['url']:
            raise RuntimeError('该歌曲没有可用播放地址（可能需要登录或版权受限）')
        _song_cache[key] = {'ts': time.time(), 'url': info['url']}
        return info['url']
    src = (track.get('src') or '').strip()
    if not src:
        raise KeyError('缺少音频地址')
    return src


# ---------------------------------------------------------------- 静态文件
def _inside_allowed(inside):
    if inside in SERVE_FILES:
        return True
    parts = inside.replace(os.sep, '/').split('/')
    if parts[0] in ('assets', 'uploads') and len(parts) >= 2:
        return True
    if parts[:2] == ['docs', 'img'] and len(parts) >= 3:
        return True
    return False


def safe_path(url_path):
    """目录白名单 + realpath 防穿越：源码、JSON、日志、临时文件都不可下载"""
    rel = urllib.parse.unquote(url_path.split('?', 1)[0]).lstrip('/')
    if not rel:
        rel = 'index.html'
    full = os.path.realpath(os.path.join(ROOT, rel))
    if full != ROOT and not full.startswith(ROOT + os.sep):
        return None                                    # 目录穿越
    inside = os.path.relpath(full, ROOT)
    for part in inside.split(os.sep):
        if part.startswith('.'):
            return None                                # 点文件/点目录
    if os.path.splitext(full)[1].lower() in BLOCKED_EXT:
        return None
    if not _inside_allowed(inside):
        return None                                    # preview/、*.py、*.json、*.log 一并挡掉
    return full


def guess_type(path):
    ctype, _ = mimetypes.guess_type(path)
    if not ctype:
        ctype = 'application/octet-stream'
    if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
        ctype += '; charset=utf-8'
    return ctype


# ---------------------------------------------------------------- 上传校验
def _is_image(blob, ext):
    if ext in ('.jpg', '.jpeg'):
        return blob.startswith(b'\xff\xd8\xff')
    if ext == '.png':
        return blob.startswith(b'\x89PNG\r\n\x1a\n')
    if ext == '.gif':
        return blob.startswith((b'GIF87a', b'GIF89a'))
    if ext == '.webp':
        return len(blob) >= 12 and blob[:4] == b'RIFF' and blob[8:12] == b'WEBP'
    if ext == '.avif':
        return len(blob) >= 12 and blob[4:8] == b'ftyp' and blob[8:12] in (b'avif', b'avis')
    return False


def _is_audio(blob, ext):
    if ext == '.mp3':
        return blob.startswith(b'ID3') or (len(blob) > 1 and blob[0] == 0xFF and (blob[1] & 0xE0) == 0xE0)
    if ext in ('.m4a', '.aac'):
        return len(blob) >= 8 and blob[4:8] == b'ftyp'
    if ext in ('.ogg', '.opus'):
        return blob.startswith(b'OggS')
    if ext == '.wav':
        return len(blob) >= 12 and blob[:4] == b'RIFF' and blob[8:12] == b'WAVE'
    if ext == '.flac':
        return blob.startswith(b'fLaC')
    return False


def file_signature_ok(blob, ext):
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif'):
        return _is_image(blob, ext)
    return _is_audio(blob, ext)


def safe_redirect_target(target):
    """Location 只允许 http(s) 绝对地址或站内路径；拒绝控制字符（防响应头注入）"""
    target = str(target or '').strip()
    if not target or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
        return None
    if target.startswith('//'):
        return None
    if target.startswith('/'):
        return urllib.parse.quote(target, safe='/')
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    return target


# ---------------------------------------------------------------- HTTP 处理
class Handler(BaseHTTPRequestHandler):
    server_version = 'homepage'
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    timeout = 15                                     # 慢速连接 / 慢速发 header 的兜底

    def version_string(self):
        return self.server_version                    # 不暴露 Python 版本

    def end_headers(self):
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        super().end_headers()

    # -------------------------------------------------- 基础工具
    def log_message(self, fmt, *args):
        line = fmt % args
        line = re.sub(r'(?i)([?&](?:p|password|passwd|token|secret|old|new)=)[^&\s"]*',
                      r'\1***', line)
        sys.stderr.write('[%s] %s\n' % (self.log_date_time_string(), line))

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            self._body_too_large = True
            self.close_connection = True
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def peer_ip(self):
        try:
            return self.client_address[0]
        except (IndexError, TypeError):
            return '-'

    def client_ip(self):
        """直连时用对端地址；仅当对端是回环（同机反代）才信任 X-Real-IP / X-Forwarded-For"""
        peer = self.peer_ip()
        if peer in ('127.0.0.1', '::1'):
            real = (self.headers.get('X-Real-IP') or '').strip()
            if real:
                try:
                    return str(ipaddress.ip_address(real))
                except ValueError:
                    pass
            forwarded = (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            if forwarded:
                try:
                    return str(ipaddress.ip_address(forwarded))
                except ValueError:
                    pass
        return peer

    def is_https(self):
        if FORCE_SECURE_COOKIE:
            return True
        proto = (self.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
        return proto == 'https' and self.peer_ip() in ('127.0.0.1', '::1')

    def auth_cookie(self, value, max_age):
        secure = self.is_https()
        name = SECURE_COOKIE_NAME if secure else COOKIE_NAME
        parts = ['%s=%s' % (name, value), 'Path=/', 'HttpOnly',
                 'SameSite=Strict', 'Max-Age=%d' % max_age]
        if secure:
            parts.append('Secure')
        return '; '.join(parts)

    def clear_cookie(self, name):
        secure = self.is_https()
        value = '%s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0' % name
        if secure:
            value += '; Secure'
        return value

    def send_login_ok(self):
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Set-Cookie', self.auth_cookie(make_token(), TOKEN_TTL))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def current_config(self):
        return load_config()

    def is_admin(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie') or '')
        except Exception:                                          # noqa: BLE001
            return False
        for name in (COOKIE_NAME, SECURE_COOKIE_NAME):
            morsel = cookie.get(name)
            if morsel and check_token(morsel.value):
                return True
        return False

    def require_admin(self):
        if self.is_admin():
            return True
        # 未读请求 body 就返回 401，必须断开连接，避免 keep-alive 下残留 body 被当成下一个请求
        self.close_connection = True
        self.send_json({'ok': False, 'error': '未登录或登录已过期'}, 401)
        return False

    # -------------------------------------------------- 路由
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path == '/api/site':
            cfg = self.current_config()
            if query.get('full'):
                if not self.is_admin():
                    return self.send_json({'ok': False, 'error': '未登录'}, 401)
                return self.send_json({'ok': True, 'config': cfg})
            return self.send_json({'ok': True, 'config': public_config(cfg)})

        if path == '/api/timeline':
            wants_refresh = bool(query.get('refresh'))
            if wants_refresh and not self.is_admin():
                return self.send_json({'ok': False, 'error': '刷新缓存需要登录'}, 401)
            cfg = self.current_config()
            try:
                data = build_timeline(cfg, refresh=wants_refresh)
            except Exception as exc:                               # noqa: BLE001
                sys.stderr.write('[timeline] %s: %s\n' % (exc.__class__.__name__, str(exc)[:200]))
                return self.send_json({'ok': False, 'error': '抓取时间轴失败'}, 500)
            data['ok'] = True
            data['enabled'] = bool((cfg.get('timeline') or {}).get('feeds'))
            if wants_refresh:
                audit('timeline_refresh', '', self.client_ip())
            return self.send_json(data)

        if path == '/api/netease/song':
            if not self.require_admin():
                return
            cfg = self.current_config()
            base = netease_base(cfg)
            song_id = (query.get('id') or [''])[0].strip()
            if not base:
                return self.send_json({'ok': False, 'error': '请先填写网易云 API 地址'}, 400)
            if not song_id.isdigit():
                return self.send_json({'ok': False, 'error': '歌曲 ID 必须是数字'}, 400)
            try:
                info = netease_song(base, song_id)
            except Exception as exc:                               # noqa: BLE001
                return self.send_json({'ok': False, 'error': str(exc)}, 502)
            info['ok'] = True
            return self.send_json(info)

        if path == '/api/music/stream':
            cfg = self.current_config()
            try:
                index = int((query.get('i') or ['0'])[0])
            except ValueError:
                return self.send_json({'ok': False, 'error': '参数错误'}, 400)
            try:
                target = resolve_stream(cfg, index)
            except KeyError as exc:
                return self.send_json({'ok': False, 'error': str(exc)}, 404)
            except Exception as exc:                               # noqa: BLE001
                return self.send_json({'ok': False, 'error': str(exc)}, 502)
            safe_target = safe_redirect_target(target)
            if not safe_target:
                return self.send_json({'ok': False, 'error': '音频地址不合法'}, 400)
            self.send_response(302)
            self.send_header('Location', safe_target)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return

        if path == '/api/uploads':
            if not self.require_admin():
                return
            files = []
            if os.path.isdir(UPLOAD_DIR):
                for name in sorted(os.listdir(UPLOAD_DIR), reverse=True):
                    full = os.path.join(UPLOAD_DIR, name)
                    if os.path.isfile(full):
                        files.append({'name': name, 'url': '/uploads/' + name,
                                      'size': os.path.getsize(full)})
            return self.send_json({'ok': True, 'files': files})

        if path in ('/admin', '/admin/'):
            return self.serve_file(os.path.join(ROOT, 'admin.html'))
        return self.serve_file(safe_path(path))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == '/api/login':
            ip = self.client_ip()
            if login_blocked(ip):
                audit('login_blocked', '', ip)
                return self.send_json(
                    {'ok': False, 'error': '尝试过于频繁，请 10 分钟后再试'}, 429)
            body = self.read_json()
            if not verify_password(str(body.get('password') or '')):
                note_login_fail(ip)
                time.sleep(0.4)                                    # 轻微拖延，避免暴力试
                audit('login_fail', '', ip)
                return self.send_json({'ok': False, 'error': '密码不正确'}, 401)
            clear_login_fails(ip)
            audit('login_ok', '', ip)
            return self.send_login_ok()

        if path == '/api/logout':
            # 只有持有效会话的请求才能吊销全部会话，避免匿名请求反复把管理员踢下线
            if self.is_admin():
                data = load_admin()
                if data:
                    bump_token_version(data)         # 服务端吊销，不只是清浏览器 cookie
                    save_admin(data)
            audit('logout', '', self.client_ip())
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Set-Cookie', self.clear_cookie(COOKIE_NAME))
            self.send_header('Set-Cookie', self.clear_cookie(SECURE_COOKIE_NAME))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == '/api/password':
            if not self.require_admin():
                return
            body = self.read_json()
            old = str(body.get('old') or '')
            if not verify_password(old):
                return self.send_json({'ok': False, 'error': '原密码不正确'}, 401)
            new = str(body.get('new') or '')
            if len(new) < MIN_PASSWORD_LEN:
                return self.send_json(
                    {'ok': False, 'error': '新密码至少 %d 位' % MIN_PASSWORD_LEN}, 400)
            if new == old or verify_password(new):
                return self.send_json({'ok': False, 'error': '新密码不能与原密码相同'}, 400)
            data = load_admin()
            data['secret'] = secrets.token_hex(32)                 # 轮换签名密钥
            bump_token_version(data)                               # 所有旧会话立即失效
            _set_password(data, new)
            save_admin(data)
            audit('password_change', '', self.client_ip())
            return self.send_login_ok()

        if path == '/api/upload':
            if not self.require_admin():
                return
            return self.handle_upload()

        self.send_json({'ok': False, 'error': '未知接口'}, 404)

    def do_PUT(self):
        if urllib.parse.urlparse(self.path).path != '/api/site':
            return self.send_json({'ok': False, 'error': '未知接口'}, 404)
        if not self.require_admin():
            return
        body = self.read_json()
        patch = body.get('config')
        if not isinstance(patch, dict):
            return self.send_json({'ok': False, 'error': 'config 必须是对象'}, 400)
        merged = save_config(patch)
        audit('config_save', ','.join(sorted(patch.keys()))[:200], self.client_ip())
        self.send_json({'ok': True, 'config': merged})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != '/api/upload':
            return self.send_json({'ok': False, 'error': '未知接口'}, 404)
        if not self.require_admin():
            return
        query = urllib.parse.parse_qs(parsed.query)
        name = os.path.basename((query.get('name') or [''])[0])
        full = os.path.realpath(os.path.join(UPLOAD_DIR, name))
        if name and full.startswith(UPLOAD_DIR + os.sep) and os.path.isfile(full):
            os.remove(full)
            audit('upload_delete', name, self.client_ip())
            return self.send_json({'ok': True})
        self.send_json({'ok': False, 'error': '文件不存在'}, 404)

    # -------------------------------------------------- 上传
    def handle_upload(self):
        if getattr(self, '_body_too_large', False):
            return self.send_json(
                {'ok': False, 'error': '请求体超过 %d MB' % (MAX_BODY // 1048576)}, 413)
        body = self.read_json()
        if getattr(self, '_body_too_large', False):
            return self.send_json(
                {'ok': False, 'error': '请求体超过 %d MB' % (MAX_BODY // 1048576)}, 413)
        raw = str(body.get('data') or '')
        name = os.path.basename(str(body.get('name') or 'file'))
        if ',' in raw and raw.lstrip().startswith('data:'):
            head, raw = raw.split(',', 1)
            m = re.match(r'data:([^;]+)', head)
            if m and not name:
                name = 'file'
        try:
            blob = base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError):
            return self.send_json({'ok': False, 'error': '文件内容不是合法的 base64'}, 400)
        if not blob:
            return self.send_json({'ok': False, 'error': '文件为空'}, 400)
        if len(blob) > MAX_UPLOAD:
            return self.send_json({'ok': False, 'error': '文件超过 %d MB' % (MAX_UPLOAD // 1048576)}, 413)

        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_EXT:
            return self.send_json({'ok': False, 'error': '不支持的文件类型：%s' % (ext or '未知')}, 400)
        if not file_signature_ok(blob, ext):
            return self.send_json({'ok': False, 'error': '文件内容与扩展名不符'}, 400)

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        stem = re.sub(r'[^A-Za-z0-9\u4e00-\u9fa5_-]+', '-', os.path.splitext(name)[0])[:32].strip('-') or 'file'
        digest = hashlib.sha1(blob).hexdigest()[:8]
        stored = '%s-%s%s' % (stem, digest, ext)
        with open(os.path.join(UPLOAD_DIR, stored), 'wb') as fp:
            fp.write(blob)
        audit('upload', stored, self.client_ip())
        self.send_json({'ok': True, 'url': '/uploads/' + stored, 'name': stored, 'size': len(blob)})

    # -------------------------------------------------- 静态文件（含 Range）
    def serve_file(self, full):
        if not full or not os.path.isfile(full):
            return self.send_json({'ok': False, 'error': '404'}, 404)
        size = os.path.getsize(full)
        ctype = guess_type(full)
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get('Range') or ''
        m = re.match(r'bytes=(\d*)-(\d*)$', range_header.strip())
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2) or 0))
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */%d' % size)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            status = 206

        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(max(0, end - start + 1)))
        if status == 206:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        # 上传目录里的文件再套一层沙箱：即使将来混进可执行格式，也拿不到同源脚本能力
        if os.path.realpath(full).startswith(UPLOAD_DIR + os.sep):
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; style-src 'unsafe-inline'; sandbox")
        if full.endswith(('.html', '.json')) or os.sep + 'assets' + os.sep in full:
            self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with open(full, 'rb') as fp:
            fp.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fp.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def do_HEAD(self):
        self.do_GET()


# ---------------------------------------------------------------- 入口
class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    max_workers = MAX_WORKERS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sem = threading.BoundedSemaphore(self.max_workers)

    def process_request(self, request, client_address):
        if not self._sem.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\n'
                                b'Content-Type: text/plain\r\nContent-Length: 12\r\n'
                                b'Connection: close\r\n\r\nserver busy\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._sem.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._sem.release()


def local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('8.8.8.8', 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return '127.0.0.1'


def read_new_password_interactive():
    """从环境变量或 getpass 读新密码，避免出现在命令行历史 / 进程列表里"""
    env = os.environ.get('HP_ADMIN_PASSWORD')
    if env:
        password = env
    else:
        if not sys.stdin.isatty():
            raise SystemExit('非交互环境无法提示输入密码，请设置 HP_ADMIN_PASSWORD 环境变量')
        password = getpass.getpass('新密码（至少 %d 位）：' % MIN_PASSWORD_LEN)
        confirm = getpass.getpass('再输入一次：')
        if password != confirm:
            raise SystemExit('两次输入的密码不一致')
    _validate_new_password(password)
    return password


def main():
    # Windows 控制台默认不是 UTF-8，中文提示会变乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description='本站极简后台服务')
    parser.add_argument('--host', default='127.0.0.1', help='监听地址（默认仅本机）')
    parser.add_argument('--port', type=int, default=8787, help='监听端口（默认 8787）')
    parser.add_argument('--reset-password', action='store_true',
                        help='交互式重置后台密码（也可用 HP_ADMIN_PASSWORD 环境变量）')
    parser.add_argument('--secure-cookie', action='store_true',
                        help='强制按 HTTPS 下发 Secure Cookie（反代未传 X-Forwarded-Proto 时使用）')
    parser.add_argument('--allow-local-netease', action='store_true',
                        help='允许网易云 API 指向本机/内网（默认禁止，防 SSRF）')
    args = parser.parse_args()

    global FORCE_SECURE_COOKIE, ALLOW_LOCAL_NETEASE
    FORCE_SECURE_COOKIE = bool(args.secure_cookie)
    ALLOW_LOCAL_NETEASE = bool(args.allow_local_netease)

    password = None
    try:
        password = read_new_password_interactive() if args.reset_password else None
        created, plain = init_admin(password)
    except ValueError as exc:
        raise SystemExit('密码不合格：%s' % exc)
    if created and not args.reset_password:
        print('\n  已生成后台密码： %s' % plain)
        print('  登录地址： http://%s:%d/admin\n' % (args.host if args.host != '0.0.0.0' else '127.0.0.1', args.port))
    elif args.reset_password:
        print('  后台密码已重置')
    else:
        print('  后台已就绪（密码见 admin.json，忘记可用 --reset-password 重置）')

    if not os.path.exists(SITE_JSON):
        _write_json(SITE_JSON, {})
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    httpd = BoundedThreadingHTTPServer((args.host, args.port), Handler)
    print('  站点地址： http://%s:%d/' % (args.host if args.host != '0.0.0.0' else '127.0.0.1', args.port))
    if args.host == '0.0.0.0':
        print('  局域网地址： http://%s:%d/   （已对外暴露，请确认密码足够强）' % (local_ip(), args.port))
        print('  公网部署请务必放在 HTTPS 反代之后，并加 --secure-cookie')
    print('  Ctrl+C 停止\n')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n  已停止')
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
