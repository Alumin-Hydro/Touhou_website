"""OSS 配置自检：填完 .env 后跑 `python check_oss.py`。

把真实的一整条链路走一遍——CORS 预检 → 签名 → 直传 → 复核 → 缩略图 → 删除——
并顺带确认「客户端可以 PUT 任意字节」这个漏洞确实被 confirm_upload 堵住了。

这些是本地单测覆盖不到的部分：sign_url 是纯签名运算不联网，而 head_object /
get_object / delete_object 必须有真 bucket 才跑得起来。
"""
import socket
import struct
import sys
import zlib

import oss2
import requests

from run import app
from app import oss
from app.oss import OssError

TEST_USER_ID = 999_999  # 不属于任何真实用户，免得跟正常数据混在一起

# 本地开发的来源。run.py 起的是 127.0.0.1:5000，可地址栏里多半敲的是 localhost，
# 而浏览器是逐字匹配 Origin 的 —— 两个都得在 CORS 规则里，少一个就挂。
DEV_ORIGINS = ('http://127.0.0.1:5000', 'http://localhost:5000')

# 上线后的来源。现在还用不上（备案中），所以只提示、不算失败。但规则现在就该一起配好：
# 等备案通过了才发现少配一个来源，那是几周以后的事，到时候最难查。
PROD_ORIGINS = ('https://gensoumono.cn', 'https://www.gensoumono.cn')


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
warned = 0


def check(name, cond, detail=''):
    global failed
    if cond:
        print(f'  ✓ {name}')
    else:
        failed += 1
        print(f'  ✗ {name}  {detail}')


def warn(name, cond, detail=''):
    """该配、但现在还用不上的东西：提示，不算失败。"""
    global warned
    if cond:
        print(f'  ✓ {name}')
    else:
        warned += 1
        print(f'  ! {name}  {detail}')


def preflight(origin, url):
    """把浏览器 PUT 之前必发的那个 OPTIONS 原样发一遍，返回 (是否放行, 详情)。

    oss-upload.js 在 PUT 时设了 Content-Type: image/xxx，而这个值不属于 CORS 安全列表
    （安全列表只有 x-www-form-urlencoded / form-data / text/plain），所以浏览器必定
    先发一个预检。requests 自己不*执行* CORS 检查，但 OSS 会*回答*预检 —— 读它的响应头
    就能验出跨域规则配没配对。匿名请求，不需要签名，对象也不必存在。
    """
    r = requests.options(url, timeout=15, headers={
        'Origin': origin,
        'Access-Control-Request-Method': 'PUT',
        'Access-Control-Request-Headers': 'content-type',
    })
    allow_origin = r.headers.get('Access-Control-Allow-Origin', '')
    allow_methods = r.headers.get('Access-Control-Allow-Methods', '')
    ok = (r.status_code == 200
          and allow_origin in (origin, '*')
          and 'PUT' in allow_methods.upper())
    return ok, (f'（HTTP {r.status_code}；Allow-Origin={allow_origin or "无"}；'
                f'Allow-Methods={allow_methods or "无"}）')


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
    print(f'endpoint : {oss._endpoint()}')
    print(f'公开前缀 : {cfg["OSS_PUBLIC_BASE"]}')

    # 内网 endpoint 只在阿里云 VPC 内部通得了。要是在本地填了它，confirm_upload
    # （走 internal=True）会一路连到超时才失败，报出来的样子像权限问题，其实跟权限
    # 毫无关系。先探一下，早点把话说清楚，省得对着 traceback 查半天。
    internal = oss._endpoint(internal=True)
    if internal != oss._endpoint():
        host = internal.split('://', 1)[1]
        try:
            socket.create_connection((host, 443), timeout=5).close()
        except OSError:
            print(f'\n✗ 连不上内网 endpoint：{host}')
            print('  内网 endpoint 只在阿里云 VPC 内部解析得了，本地连不上。')
            print('  把 .env 的 OSS_ENDPOINT_INTERNAL 留空（会自动回落到公网 endpoint），')
            print('  只有部署到 ECS 上时才填它。')
            sys.exit(1)

    png = make_png(1000, 800)
    print(f'测试图   : 1000x800 PNG, {len(png) / 1024:.0f} KB\n')

    print('== 0. 配置与 CORS 预检（浏览器直传的前置条件）==')
    # 图片 URL 会带着这个前缀原样入库。写成 http 的话，等站点上了 TLS，每张历史图片
    # 都是 https 页面里的 http 子资源 —— 浏览器直接拦成混合内容。
    check('OSS_PUBLIC_BASE 是 https', cfg['OSS_PUBLIC_BASE'].startswith('https://'),
          f'\n      当前是 {cfg["OSS_PUBLIC_BASE"]}；图片 URL 原样入库，http 会在上线后被当混合内容拦掉')

    scheme, host = oss._endpoint().split('://', 1)
    probe_url = f'{scheme}://{cfg["OSS_BUCKET"]}.{host}/cors-probe'
    cors_hint = '\n      bucket → 数据安全 → 跨域设置：加上这个来源，Methods 勾 PUT/GET/HEAD'
    for origin in DEV_ORIGINS:
        ok, detail = preflight(origin, probe_url)
        check(f'{origin} 允许直传', ok, detail + cors_hint)
    for origin in PROD_ORIGINS:
        ok, detail = preflight(origin, probe_url)
        warn(f'{origin} 允许直传（上线才用得上）', ok, detail + cors_hint)

    print('\n== 1. 签名 ==')
    try:
        put_url, key, content_type = oss.sign_upload(
            'post', 'selfcheck.png', len(png), TEST_USER_ID)
        check('签发预签名 PUT URL', True)
        print(f'      key = {key}')
    except OssError as e:
        check('签发预签名 PUT URL', False, str(e))
        sys.exit(1)

    # 上了 TLS 之后，https 页面里发往 http 的 PUT 会被浏览器当混合内容拦掉；而本地页面
    # 自己就是 http，这事测不出来。在这儿钉一颗钉子，防止 _endpoint() 的补全逻辑被改坏。
    check('签出的是 https URL', put_url.startswith('https://'),
          f'\n      得到的是 {put_url.split("://", 1)[0]}://…，上线后会被浏览器当混合内容拦掉')

    # 超限时必须把限额一起带回去：oss-upload.js 凭 max_bytes 弹「压缩后上传」的确认框。
    # 这个字段没了，用户随手一张 8MB 手机原图就只剩一句干巴巴的报错。
    # 限额只在 _MAX_BYTES 定义这一处，前端不硬编码第二份跟着漂移。
    try:
        oss.sign_upload('avatar', 'huge.jpg', 6 * 1024 * 1024, TEST_USER_ID)
        check('头像超限时拒绝签名', False, '6MB 的头像竟然签出来了')
    except OssError as e:
        check(f'头像超限时拒绝签名（"{e}"）', True)
        check('并带回 max_bytes 供前端提出压缩',
              e.max_bytes == 5 * 1024 * 1024,
              f'\n      拿到的是 {e.max_bytes!r}，前端就弹不出压缩确认框了')

    # AVIF 的字节，后端是**故意不认**的 —— 魔术字节白名单是道安全阀（挡住把 HTML/SVG
    # 伪装成图片存进去），不能为了一个格式就放开。归一化是浏览器的活：oss-upload.js 在
    # 选图时嗅格式，AVIF/HEIC 用 canvas 转成 JPEG 再传。
    # 钉住这个前提：谁要是哪天「顺手」把 AVIF 加进白名单，存下来的对象会是
    # Content-Type: image/jpeg 而字节是 AVIF —— OSS 图片处理不认，缩略图当场裂。
    check('AVIF 字节仍被后端拒收（转码是浏览器的责任）',
          not oss._looks_like_image(b'\x00\x00\x00\x1cftypavif'),
          '\n      白名单被放开了？那 OSS 的 x-oss-process 会处理不了，缩略图要裂')

    print('\n== 2. 直传（模拟浏览器，不经过后端）==')
    r = requests.put(put_url, data=png, headers={'Content-Type': content_type}, timeout=60)
    check(f'PUT 到 OSS（HTTP {r.status_code}）', r.status_code == 200,
          '\n      403 SignatureDoesNotMatch：AccessKey 填错，或 endpoint 地域与 bucket 不符'
          '\n      403 AccessDenied：RAM 策略缺 oss:PutObject，或策略里的 bucket 名写错')
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
    check(f'OSS 实时缩略图（HTTP {r.status_code}）', r.status_code == 200,
          '\n      400 说明 OSS 不认这串 x-oss-process 参数（比如 auto-orient 写错了位置或值）')
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

    print('\n== 7. 换域名之后，老图不能失效（备案通过那天就会踩）==')
    # 那天要把 OSS_PUBLIC_BASE 从 bucket 默认域名换成 https://img.gensoumono.cn，
    # 而已入库的 URL 不会跟着变。只认当前前缀的话，此前上传的每一张图会同时丢掉缩略图
    # 和删除能力 —— 两条都不报错。这个故障浏览器里测不出来（要等到真换域名那天），
    # 只有在这儿把那天预演一遍才钉得住。
    put_url3, key3, ct3 = oss.sign_upload('post', 'legacy.png', len(png), TEST_USER_ID)
    requests.put(put_url3, data=png, headers={'Content-Type': ct3}, timeout=60)
    legacy_url = f'{scheme}://{cfg["OSS_BUCKET"]}.{host}/{key3}'  # 老图：带默认域名前缀入的库

    real_base = cfg['OSS_PUBLIC_BASE']
    cfg['OSS_PUBLIC_BASE'] = 'https://img.gensoumono.cn'  # 假装备案过了、域名已经换掉
    try:
        t = oss.thumb(legacy_url, 800)
        check('换域名后，老图仍然走缩略图', 'x-oss-process' in t,
              '\n      老图不再匹配新前缀 —— 帖子列表会直接加载 20MB 原图，'
              '导航栏会拿 5MB 头像去填 22×22 的框')
        check('缩略图带 auto-orient（原生上传的手机照片才不会躺倒）', 'auto-orient' in t)
        oss.delete_by_url(legacy_url)
    finally:
        cfg['OSS_PUBLIC_BASE'] = real_base

    try:
        bucket.head_object(key3)
        check('换域名后，老图仍然删得掉', False,
              '\n      delete_by_url 静默跳过了它 —— 删帖不再删 OSS 对象，'
              '垃圾永久留在 bucket 里付存储费')
    except oss2.exceptions.NotFound:
        check('换域名后，老图仍然删得掉', True)

print()
if failed:
    print(f'{failed} 项没过 —— 照着上面的提示查配置。')
    sys.exit(1)
if warned:
    print(f'{warned} 项待办：上线前配好，不影响现在本地跑。\n')
print('全部通过。OSS 链路可用，可以在浏览器里发帖传图了。')
print('注意：预检只验了跨域规则本身配没配对，CORS 的实际执行、以及 JS / 进度条 /')
print('      表单流程，仍然只有真浏览器说了算 —— 务必再手工发一帖。')
