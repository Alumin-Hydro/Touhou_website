"""OSS 配置自检：填完 .env 后跑 `python check_oss.py`。

把真实的一整条链路走一遍——签名 → 直传 → 复核 → 缩略图 → 删除——
并顺带确认「客户端可以 PUT 任意字节」这个漏洞确实被 confirm_upload 堵住了。

这些是本地单测覆盖不到的部分：sign_url 是纯签名运算不联网，而 head_object /
get_object / delete_object 必须有真 bucket 才跑得起来。
"""
import struct
import sys
import zlib

import oss2
import requests

from run import app
from app import oss
from app.oss import OssError

TEST_USER_ID = 999_999  # 不属于任何真实用户，免得跟正常数据混在一起


def make_png(w, h):
    """现造一张 PNG，不引 Pillow（2G 内存的机器上装它没必要）。"""
    raw = b''.join(
        b'\x00' + b''.join(bytes([x * 255 // w, y * 255 // h, 128]) for x in range(w))
        for y in range(h)
    )

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw))
            + chunk(b'IEND', b''))


failed = 0


def check(name, cond, detail=''):
    global failed
    if cond:
        print(f'  ✓ {name}')
    else:
        failed += 1
        print(f'  ✗ {name}  {detail}')


with app.app_context():
    cfg = app.config
    missing = [k for k in ('OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET', 'OSS_BUCKET',
                           'OSS_ENDPOINT', 'OSS_PUBLIC_BASE') if not cfg.get(k)]
    if missing:
        print('.env 里这些还没填：')
        for k in missing:
            print('  -', k)
        print('\n照着 DEPLOYMENT.md 第 1 节配完再跑。')
        sys.exit(1)

    print(f'bucket   : {cfg["OSS_BUCKET"]}')
    print(f'endpoint : {cfg["OSS_ENDPOINT"]}')
    print(f'公开前缀 : {cfg["OSS_PUBLIC_BASE"]}')

    png = make_png(1000, 800)
    print(f'测试图   : 1000x800 PNG, {len(png) / 1024:.0f} KB\n')

    print('== 1. 签名 ==')
    try:
        put_url, key, content_type = oss.sign_upload(
            'post', 'selfcheck.png', len(png), TEST_USER_ID)
        check('签发预签名 PUT URL', True)
        print(f'      key = {key}')
    except OssError as e:
        check('签发预签名 PUT URL', False, str(e))
        sys.exit(1)

    print('\n== 2. 直传（模拟浏览器，不经过后端）==')
    r = requests.put(put_url, data=png, headers={'Content-Type': content_type}, timeout=60)
    check(f'PUT 到 OSS（HTTP {r.status_code}）', r.status_code == 200,
          '\n      403 多半是 AccessKey 或权限策略不对；'
          '\n      浏览器里报 CORS 错则是跨域规则没配（curl/requests 不受 CORS 约束，这里看不出来）')
    if r.status_code != 200:
        print('      ' + r.text[:300])
        sys.exit(1)

    print('\n== 3. 入库前复核 ==')
    try:
        url = oss.confirm_upload(key, TEST_USER_ID)
        check('head_object + 魔术字节复核通过', True)
        print(f'      url = {url}')
    except OssError as e:
        check('head_object + 魔术字节复核通过', False, str(e))
        sys.exit(1)

    print('\n== 4. 公开访问与缩略图 ==')
    r = requests.get(url, timeout=30)
    check(f'原图可公开访问（HTTP {r.status_code}）', r.status_code == 200,
          '403 说明 bucket 读写权限不是「公共读」')
    check('返回的确实是 PNG', r.content[:8] == b'\x89PNG\r\n\x1a\n')

    thumb_url = oss.thumb(url, 800)
    r = requests.get(thumb_url, timeout=30)
    check(f'OSS 实时缩略图（HTTP {r.status_code}）', r.status_code == 200)
    if r.status_code == 200:
        check('缩略图确实比原图小', len(r.content) < len(png),
              f'原图 {len(png)}B，缩略图 {len(r.content)}B')

    print('\n== 5. 安全：客户端 PUT 任意字节，复核必须拦下 ==')
    put_url2, key2, ct2 = oss.sign_upload('post', 'evil.png', 100, TEST_USER_ID)
    # 签名只约束了 key 和 Content-Type，约束不了内容 —— 这里就传一段 HTML 上去
    r = requests.put(put_url2, data=b'<html><script>alert(1)</script></html>',
                     headers={'Content-Type': ct2}, timeout=30)
    check(f'伪装成 PNG 的 HTML 能传上去（HTTP {r.status_code}，符合预期）',
          r.status_code == 200)
    try:
        oss.confirm_upload(key2, TEST_USER_ID)
        check('复核拦下了它', False, '没拦住！非图片内容进了库')
    except OssError as e:
        check(f'复核拦下了它（"{e}"）', True)

    bucket = oss._bucket(internal=True)
    try:
        bucket.head_object(key2)
        check('并且已从 OSS 上删除', False, '对象还在，残留了垃圾')
    except oss2.exceptions.NotFound:
        check('并且已从 OSS 上删除', True)

    print('\n== 6. 删除 ==')
    oss.delete_by_url(url)
    try:
        bucket.head_object(key)
        check('delete_by_url 删掉了对象', False, '对象还在')
    except oss2.exceptions.NotFound:
        check('delete_by_url 删掉了对象', True)

print()
if failed:
    print(f'{failed} 项没过 —— 照着上面的提示查配置。')
    sys.exit(1)
print('全部通过。OSS 链路可用，可以在浏览器里发帖传图了。')
print('注意：CORS 只有浏览器会校验，这个脚本测不出来 —— 务必再用真浏览器发一帖。')
