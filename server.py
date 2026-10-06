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
    python server.py --host 0.0.0.0      # 对外暴露（务必先改密码！）
    python server.py --password 新密码    # 重置后台密码

首次启动会生成 admin.json 并在控制台打印随机密码。
配置存于 site.json（不存在则用 site.default.json 的内容）。
上传文件存于 uploads/。
"""

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
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

MAX_BODY = 48 * 1024 * 1024          # 请求体上限（base64 上传约可放 30MB 文件）
MAX_UPLOAD = 32 * 1024 * 1024        # 单个文件上限
FEED_TTL = 600                       # RSS 缓存秒数
NETEASE_TTL = 300                    # 网易云播放地址缓存秒数
UA = 'Mozilla/5.0 (compatible; HomepageAdmin/1.0)'
COOKIE_NAME = 'hp_admin'
TOKEN_TTL = 7 * 24 * 3600
ALLOWED_EXT = {
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.svg',        # 图片
    '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.flac',         # 音频
}
BLOCKED_FILES = {'server.py', 'site.json', 'admin.json', 'site.default.json'}

_lock = threading.Lock()


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
    """递归合并：字典合并，列表与标量整体替换"""
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, val in override.items():
            out[key] = _merge(base.get(key), val) if key in base else val
        return out
    return override


def load_config():
    """默认配置 + site.json 覆盖（site.json 缺失时自动生成一份）"""
    default = _read_json(DEFAULT_JSON, {})
    if not os.path.exists(SITE_JSON):
        _write_json(SITE_JSON, {})
    return _merge(default, _read_json(SITE_JSON, {}))


def save_config(patch):
    """把 patch 合并进 site.json（只写差异不必要，直接全量存更直观）"""
    with _lock:
        stored = _read_json(SITE_JSON, {})
        merged = _merge(stored, patch)
        _write_json(SITE_JSON, merged)
        return merged


# ---------------------------------------------------------------- 登录鉴权
def load_admin():
    return _read_json(ADMIN_JSON, {})


def save_admin(data):
    _write_json(ADMIN_JSON, data)
    try:
        os.chmod(ADMIN_JSON, 0o600)
    except OSError:
        pass


def hash_password(password, salt):
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()


def init_admin(password=None):
    """生成 admin.json；返回本次是否新建、以及明文密码（仅新建时）"""
    data = load_admin()
    plain = None
    if not data.get('password_hash') or password:
        data = data or {}
        data['salt'] = data.get('salt') or secrets.token_hex(8)
        plain = password or secrets.token_urlsafe(9)
        data['password_hash'] = hash_password(plain, data['salt'])
        data['secret'] = data.get('secret') or secrets.token_hex(32)
        save_admin(data)
        return True, plain
    if not data.get('secret'):
        data['secret'] = secrets.token_hex(32)
        save_admin(data)
    return False, None


def verify_password(password):
    data = load_admin()
    if not data.get('password_hash'):
        return False
    return hmac.compare_digest(hash_password(password, data['salt']), data['password_hash'])


def make_token():
    exp = int(time.time()) + TOKEN_TTL
    secret = load_admin().get('secret', '').encode('utf-8')
    sig = hmac.new(secret, str(exp).encode('utf-8'), hashlib.sha256).hexdigest()
    return '%d.%s' % (exp, sig)


def check_token(token):
    if not token or '.' not in token:
        return False
    exp_s, sig = token.split('.', 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    secret = load_admin().get('secret', '').encode('utf-8')
    expect = hmac.new(secret, exp_s.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)


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


def fetch_feed(url, label=''):
    """抓取并解析一个 RSS/Atom 源，返回条目列表"""
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/rss+xml, application/xml, text/xml, */*'})
    with urllib.request.urlopen(req, timeout=12) as resp:
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
        link = _child_text(node, {'link', 'id', 'guid'})
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


def build_timeline(cfg, refresh=False):
    tl = cfg.get('timeline') or {}
    feeds = tl.get('feeds') or []
    limit = int(tl.get('limit') or 8)
    items, errors, now = [], [], time.time()
    for entry in feeds:
        if isinstance(entry, str):
            url, label = entry.strip(), ''
        else:
            url, label = (entry.get('url') or '').strip(), (entry.get('label') or '').strip()
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
            msg = '%s：%s' % (label or url, exc.__class__.__name__ + ' ' + str(exc)[:120])
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
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=12) as resp:
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
def safe_path(url_path):
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
    if os.path.basename(full) in BLOCKED_FILES:
        return None
    return full


def guess_type(path):
    ctype, _ = mimetypes.guess_type(path)
    if not ctype:
        ctype = 'application/octet-stream'
    if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
        ctype += '; charset=utf-8'
    return ctype


class Handler(BaseHTTPRequestHandler):
    server_version = 'HomepageAdmin/1.0'
    protocol_version = 'HTTP/1.1'

    # -------------------------------------------------- 基础工具
    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))

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
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def current_config(self):
        return load_config()

    def is_admin(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie') or '')
        except Exception:                                          # noqa: BLE001
            return False
        morsel = cookie.get(COOKIE_NAME)
        return bool(morsel and check_token(morsel.value))

    def require_admin(self):
        if self.is_admin():
            return True
        self.send_json({'ok': False, 'error': '未登录或登录已过期'}, 401)
        return False

    # -------------------------------------------------- 路由
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path == '/api/site':
            cfg = self.current_config()
            if query.get('full') and not self.is_admin():
                return self.send_json({'ok': False, 'error': '未登录'}, 401)
            return self.send_json({'ok': True, 'config': cfg})

        if path == '/api/timeline':
            cfg = self.current_config()
            try:
                data = build_timeline(cfg, refresh=bool(query.get('refresh')))
            except Exception as exc:                               # noqa: BLE001
                return self.send_json({'ok': False, 'error': str(exc)}, 500)
            data['ok'] = True
            data['enabled'] = bool((cfg.get('timeline') or {}).get('feeds'))
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
            if target.startswith('/'):
                target = urllib.parse.quote(target)
            self.send_response(302)
            self.send_header('Location', target)
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
            body = self.read_json()
            if not verify_password(str(body.get('password') or '')):
                time.sleep(0.4)                                    # 轻微拖延，避免暴力试
                return self.send_json({'ok': False, 'error': '密码不正确'}, 401)
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Set-Cookie', '%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d'
                             % (COOKIE_NAME, make_token(), TOKEN_TTL))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == '/api/logout':
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Set-Cookie', '%s=; Path=/; HttpOnly; Max-Age=0' % COOKIE_NAME)
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == '/api/password':
            if not self.require_admin():
                return
            body = self.read_json()
            if not verify_password(str(body.get('old') or '')):
                return self.send_json({'ok': False, 'error': '原密码不正确'}, 401)
            new = str(body.get('new') or '')
            if len(new) < 6:
                return self.send_json({'ok': False, 'error': '新密码至少 6 位'}, 400)
            data = load_admin()
            data['salt'] = secrets.token_hex(8)
            data['password_hash'] = hash_password(new, data['salt'])
            save_admin(data)
            return self.send_json({'ok': True})

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
            return self.send_json({'ok': True})
        self.send_json({'ok': False, 'error': '文件不存在'}, 404)

    # -------------------------------------------------- 上传
    def handle_upload(self):
        body = self.read_json()
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

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        stem = re.sub(r'[^A-Za-z0-9\u4e00-\u9fa5_-]+', '-', os.path.splitext(name)[0])[:32].strip('-') or 'file'
        digest = hashlib.sha1(blob).hexdigest()[:8]
        stored = '%s-%s%s' % (stem, digest, ext)
        with open(os.path.join(UPLOAD_DIR, stored), 'wb') as fp:
            fp.write(blob)
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
def local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('8.8.8.8', 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return '127.0.0.1'


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
    parser.add_argument('--password', default=None, help='重置后台密码')
    args = parser.parse_args()

    created, plain = init_admin(args.password)
    if created:
        print('\n  已生成后台密码： %s' % plain)
        print('  登录地址： http://%s:%d/admin\n' % (args.host if args.host != '0.0.0.0' else '127.0.0.1', args.port))
    elif args.password:
        print('  后台密码已重置')
    else:
        print('  后台已就绪（密码见 admin.json，忘记可用 --password 重置）')

    if not os.path.exists(SITE_JSON):
        _write_json(SITE_JSON, {})
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print('  站点地址： http://%s:%d/' % (args.host if args.host != '0.0.0.0' else '127.0.0.1', args.port))
    if args.host == '0.0.0.0':
        print('  局域网地址： http://%s:%d/   （已对外暴露，请确认密码足够强）' % (local_ip(), args.port))
    print('  Ctrl+C 停止\n')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n  已停止')
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
