#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接口自测脚本（仅供本地联调，部署时不需要）

用法：先启动 server.py，再执行
    python preview/_api_test.py --base http://127.0.0.1:8787 --password 你的密码

它会：登录 → 上传一张测试图与一段测试音频 → 写入配置（图片背景 / 时间轴 / 音乐）
      → 抓取时间轴 → 校验鉴权与 Range 支持，并打印每一步结果。
"""

import argparse
import base64
import io
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import wave
from http.cookiejar import CookieJar

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def make_png(width=1600, height=900):
    """生成一张对角渐变测试图（有 Pillow 就用 Pillow）"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        k = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=(int(18 + 60 * k), int(40 + 90 * k), int(90 + 120 * k)))
    draw.ellipse([width * 0.62, height * 0.12, width * 0.82, height * 0.45], fill=(255, 236, 190))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def make_wav(seconds=2, rate=8000):
    """生成一段 440Hz 的正弦波 WAV"""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as fp:
        fp.setnchannels(1)
        fp.setsampwidth(2)
        fp.setframerate(rate)
        frames = bytearray()
        for i in range(rate * seconds):
            val = int(12000 * __import__('math').sin(2 * 3.141592653589793 * 440 * i / rate))
            frames += struct.pack('<h', val)
        fp.writeframes(bytes(frames))
    return buf.getvalue()


class Client:
    def __init__(self, base):
        self.base = base.rstrip('/')
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, payload=None, raw=False, headers=None):
        data = None
        hdrs = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            hdrs['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with self.opener.open(req, timeout=20) as resp:
                body = resp.read()
                return resp.status, (body if raw else json.loads(body.decode('utf-8')))
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return exc.code, json.loads(body.decode('utf-8'))
            except ValueError:
                return exc.code, {'raw': body[:200].decode('utf-8', 'replace')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8787')
    ap.add_argument('--password', required=True)
    ap.add_argument('--keep-config', action='store_true', help='只测试接口，不改配置')
    args = ap.parse_args()

    c = Client(args.base)
    ok = True

    def step(name, passed, extra=''):
        nonlocal ok
        ok = ok and passed
        print('%-4s %s %s' % ('OK' if passed else 'FAIL', name, extra))

    # 1. 未登录应被拒绝
    code, body = c.call('GET', '/api/site?full=1')
    step('未登录读全量配置被拒', code == 401, '-> %s %s' % (code, body.get('error', '')))
    code, body = c.call('PUT', '/api/site', {'config': {'site': {'name': 'hacked'}}})
    step('未登录写入被拒', code == 401, '-> %s' % code)

    # 2. 登录
    code, body = c.call('POST', '/api/login', {'password': args.password})
    step('登录', code == 200 and body.get('ok'), '-> %s' % code)
    code, body = c.call('POST', '/api/login', {'password': args.password + '-wrong'})
    step('错误密码被拒', code == 401, '-> %s' % code)

    # 3. 上传图片与音频
    image = make_png()
    audio = make_wav()
    img_url = aud_url = ''
    if image:
        code, body = c.call('POST', '/api/upload', {
            'name': 'selftest-bg.png', 'data': base64.b64encode(image).decode('ascii')})
        img_url = body.get('url', '')
        step('上传图片', code == 200 and bool(img_url), '-> %s (%d KB)' % (img_url, len(image) // 1024))
    else:
        print('SKIP 上传图片（未安装 Pillow）')
    code, body = c.call('POST', '/api/upload', {
        'name': 'selftest-tone.wav', 'data': base64.b64encode(audio).decode('ascii')})
    aud_url = body.get('url', '')
    step('上传音频', code == 200 and bool(aud_url), '-> %s (%d KB)' % (aud_url, len(audio) // 1024))

    code, body = c.call('POST', '/api/upload', {'name': 'bad.exe', 'data': base64.b64encode(b'x').decode()})
    step('拒绝不支持的类型', code == 400, '-> %s %s' % (code, body.get('error', '')))

    # 4. Range 支持（音频拖动进度需要的）
    if aud_url:
        req = urllib.request.Request(args.base + aud_url, headers={'Range': 'bytes=0-99'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            step('静态文件支持 Range', resp.status == 206 and len(resp.read()) == 100,
                 '-> %s' % resp.status)

    # 5. 写配置
    if not args.keep_config:
        patch = {'config': {
            'timeline': {
                'feeds': [{'url': args.base + '/preview/sample-feed.xml', 'label': '示例博客'}],
                'limit': 5,
            },
            'music': {
                'enabled': True, 'autoplay': False, 'volume': 0.4,
                'tracks': [{'type': 'file', 'title': '自测音调 440Hz', 'artist': '本地生成',
                            'src': aud_url, 'cover': '', 'id': ''}],
            },
        }}
        if img_url:
            patch['config']['background'] = {'mode': 'manual', 'manualScene': 'day',
                                             'photos': [{'scene': 'day', 'url': img_url, 'theme': 'dark'}]}
        code, body = c.call('PUT', '/api/site', patch)
        step('保存配置', code == 200 and body.get('ok'), '-> %s' % code)

    # 6. 抓取时间轴
    code, body = c.call('GET', '/api/timeline?refresh=1')
    items = body.get('items', [])
    step('RSS 时间轴抓取', code == 200 and len(items) > 0,
         '-> %d 条，首条：%s %s' % (len(items), items[0]['date'][:10] if items else '-',
                                   items[0]['title'][:24] if items else ''))
    if body.get('errors'):
        step('抓取无报错', False, '-> %s' % '；'.join(body['errors']))
    dates = [it['date'] for it in items]
    step('按时间倒序', dates == sorted(dates, reverse=True), '-> %s' % dates[:3])

    # 7. 读取配置回显
    code, body = c.call('GET', '/api/site')
    cfg = body.get('config', {})
    step('配置可回读', cfg.get('music', {}).get('enabled') is True
         and len(cfg.get('timeline', {}).get('feeds', [])) == 1, '-> %s' % cfg.get('site', {}).get('name'))

    # 8. 上传列表
    code, body = c.call('GET', '/api/uploads')
    step('上传文件列表', code == 200 and len(body.get('files', [])) >= 2,
         '-> %d 个文件' % len(body.get('files', [])))

    # 9. 越权路径
    for bad in ('/admin.json', '/site.json', '/server.py', '/../server.py'):
        try:
            with urllib.request.urlopen(args.base + bad, timeout=5) as resp:
                step('敏感文件 %s 不可访问' % bad, False, '-> %s' % resp.status)
        except urllib.error.HTTPError as exc:
            step('敏感文件 %s 不可访问' % bad, exc.code in (403, 404), '-> %s' % exc.code)

    print('\n%s' % ('全部通过' if ok else '存在失败项，请检查上面的 FAIL 行'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
