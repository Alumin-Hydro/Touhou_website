# app/oss.py
"""阿里云 OSS 图片直传。

浏览器凭后端签发的预签名 URL 把图片直接 PUT 到 OSS，图片不经过本服务：
ECS 只有 3M 出向带宽和 40G 盘，扛不住 20MB 原图的分发与存储（见 DEPLOYMENT.md）。

后端只做三件事：签名、入库前复核、删除。
"""
import re
import uuid

import oss2
from flask import Blueprint, current_app, flash, jsonify, request
from flask_login import current_user, login_required

oss_bp = Blueprint('oss', __name__, url_prefix='/oss')


class OssError(Exception):
    """校验失败。消息面向用户，可直接 flash 或返回给前端。"""


# 扩展名白名单即唯一事实来源：Content-Type 由它推导，不采信浏览器给的 file.type。
# OSS 的预签名把 Content-Type 算进签名，前后端对不上就是 403 —— 由后端单方面决定
# 可以消掉这一整类错配。
_EXT_MIME = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp',
    'tiff': 'image/tiff', 'tif': 'image/tiff',
}

# 帖子原图不压缩；头像没必要那么大。
_MAX_BYTES = {'post': 20 * 1024 * 1024, 'avatar': 5 * 1024 * 1024}

# key 形如 post/12/3f9a...c1.jpg —— user_id 编进 key 里。
# 这样入库时才能确认「提交者就是上传者」：否则 A 可以把 B 刚传的 key 填进自己的表单，
# 之后 A 删帖会连带把 B 的图删掉。
_KEY_RE = re.compile(r'^(post|avatar)/(\d+)/[0-9a-f]{32}\.[a-z]+$')

_SIGN_EXPIRES = 300  # 预签名 URL 有效期（秒）


def _endpoint(internal=False):
    """取 endpoint，并补全协议头。

    oss2 对不带协议头的 endpoint 一律补成 http://，于是签出来的预签名 URL 也是 http 的。
    本地开发时页面自己就是 http，测不出任何问题；等站点上了 TLS，浏览器会把 https 页面
    里发往 http 的这个 PUT 当作混合内容直接拦掉 —— 一个只在生产暴露的故障。
    所以这里统一补 https://，不指望 .env 每次都写对。
    """
    cfg = current_app.config
    ep = (cfg['OSS_ENDPOINT_INTERNAL'] if internal else cfg['OSS_ENDPOINT']) or ''
    return ep if ep.startswith(('http://', 'https://')) else f'https://{ep}'


def _bucket(internal=False):
    cfg = current_app.config
    if not cfg.get('OSS_BUCKET'):
        raise OssError('图片服务未配置')
    auth = oss2.Auth(cfg['OSS_ACCESS_KEY_ID'], cfg['OSS_ACCESS_KEY_SECRET'])
    return oss2.Bucket(auth, _endpoint(internal), cfg['OSS_BUCKET'])


def public_url(key):
    return f"{current_app.config['OSS_PUBLIC_BASE'].rstrip('/')}/{key}"


def sign_upload(kind, filename, size, user_id):
    """签发预签名 PUT URL。返回 (put_url, key, content_type)。

    key 一律由后端生成，绝不接受前端指定 —— 否则前端可以覆盖任意对象。
    """
    if kind not in _MAX_BYTES:
        raise OssError('未知的上传类型')
    if not filename or '.' not in filename:
        raise OssError('文件名无效')

    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in _EXT_MIME:
        raise OssError('不支持的图片格式')

    limit = _MAX_BYTES[kind]
    if not isinstance(size, int) or size <= 0 or size > limit:
        raise OssError(f'图片不能超过 {limit // 1024 // 1024}MB')

    content_type = _EXT_MIME[ext]
    key = f'{kind}/{user_id}/{uuid.uuid4().hex}.{ext}'
    put_url = _bucket().sign_url(
        'PUT', key, _SIGN_EXPIRES,
        slash_safe=True,
        headers={'Content-Type': content_type},
    )
    return put_url, key, content_type


def _looks_like_image(head):
    """按魔术字节判断是不是真图片。"""
    return (
        head.startswith(b'\xff\xd8\xff')                  # JPEG
        or head.startswith(b'\x89PNG\r\n\x1a\n')          # PNG
        or head[:6] in (b'GIF87a', b'GIF89a')             # GIF
        or (head[:4] == b'RIFF' and head[8:12] == b'WEBP')  # WebP
        or head.startswith(b'BM')                         # BMP
        or head[:4] in (b'II*\x00', b'MM\x00*')           # TIFF
    )


def confirm_upload(key, user_id):
    """入库前复核 OSS 上的对象，返回可公开访问的 URL。不合规则删除对象并抛错。

    这是整条链路上最关键的一步：预签名 URL 一旦签出，客户端实际 PUT 了什么内容
    后端是完全看不见的（签名只约束了 key 和 Content-Type，约束不了字节）。
    所以必须回查对象本身 —— 大小、类型、以及真实的魔术字节。
    """
    m = _KEY_RE.match(key or '')
    if not m:
        raise OssError('图片标识无效')

    kind, owner_id = m.group(1), int(m.group(2))
    if owner_id != user_id:
        raise OssError('图片标识无效')

    bucket = _bucket(internal=True)
    try:
        head = bucket.head_object(key)
    except oss2.exceptions.NotFound:
        raise OssError('图片尚未上传完成，请重试')

    reason = None
    if head.content_length > _MAX_BYTES[kind]:
        reason = '图片超出大小限制'
    elif not (head.content_type or '').startswith('image/'):
        reason = '只能上传图片'
    elif not _looks_like_image(bucket.get_object(key, byte_range=(0, 11)).read()):
        reason = '文件内容不是有效的图片'

    if reason:
        bucket.delete_object(key)
        raise OssError(reason)

    return public_url(key)


def resolve_upload(key, user_id):
    """表单提交时把 photo_key / avatar_key 换成可入库的 URL；没传图返回 None。

    校验失败只 flash 提示、返回 None，不阻断整个提交 ——
    不该因为一张图就把用户刚写好的正文丢掉。
    """
    if not key:
        return None
    try:
        return confirm_upload(key, user_id)
    except OssError as e:
        flash(f'图片未能保存：{e}')
        return None


def delete_by_url(url):
    """删除 OSS 上的图片。

    非本站 OSS 的 URL（比如历史遗留的 /static/uploads/…，或外链）直接忽略。
    """
    if not url:
        return
    base = current_app.config.get('OSS_PUBLIC_BASE', '').rstrip('/') + '/'
    if base == '/' or not url.startswith(base):
        return

    key = url[len(base):].split('?', 1)[0]
    if not _KEY_RE.match(key):
        return
    try:
        _bucket(internal=True).delete_object(key)
    except (oss2.exceptions.OssError, OssError):
        pass  # 对象已不在、或 OSS 暂时不可达；都不该因此阻断删帖


def thumb(url, width=800):
    """列表与正文里用缩略图。

    OSS 自带图片处理，加个 URL 参数就实时生成并缓存 —— 不要在服务端跑 Pillow：
    一张 6000×4000 的 JPEG 解码成 RGB 位图就是约 72MB，2G 内存必 OOM。
    """
    base = current_app.config.get('OSS_PUBLIC_BASE', '')
    if not url or not base or not url.startswith(base):
        return url  # 历史遗留的本地图片、或未配置 OSS，原样返回
    return f'{url}?x-oss-process=image/resize,w_{width},m_lfit/quality,q_85'


@oss_bp.route('/sign', methods=['POST'])
@login_required
def sign():
    """签发预签名上传 URL。CSRF 由 CSRFProtect 校验请求头 X-CSRFToken。"""
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')

    if kind == 'post' and not current_user.can_post():
        return jsonify(error='您当前被禁言，无法上传图片'), 403

    try:
        put_url, key, content_type = sign_upload(
            kind, data.get('filename'), data.get('size'), current_user.id
        )
    except OssError as e:
        return jsonify(error=str(e)), 400

    return jsonify(put_url=put_url, key=key, content_type=content_type)
