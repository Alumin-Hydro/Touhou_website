import os
from dotenv import load_dotenv
from pathlib import Path

# 获取项目根目录（settings.py 所在目录）
BASE_DIR = Path(__file__).resolve().parent
dotenv_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=dotenv_path)

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')

    # Flask-WTF 默认让 CSRF token 一小时后过期。观鸟记录常常是写一两个小时的长帖，
    # 页面就那么一直开着 —— 到点一提交就是 400，正文全丢（2026-07-13 实测撞到过）。
    # 设成 None 后 token 与 session 同寿：本站没开 remember-me，session cookie 就是
    # 浏览器会话 cookie，token 活不过登录本身。token 仍由 SECRET_KEY 签名、并与
    # session['csrf_token'] 绑定，去掉的只是那个多余的时限，不放宽任何安全边界。
    WTF_CSRF_TIME_LIMIT = None

    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///forum.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # SQLite 在 gunicorn 多进程下会有并发写竞争。给锁留出等待时间，
    # 否则拿不到锁会立刻抛 "database is locked" 而不是稍等重试。
    SQLALCHEMY_ENGINE_OPTIONS = (
        {'connect_args': {'timeout': 15}}
        if SQLALCHEMY_DATABASE_URI.startswith('sqlite')
        else {}
    )

    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.126.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 465))
    MAIL_USE_SSL = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = MAIL_USERNAME

    POSTS_PER_PAGE = 20

    # 图片由浏览器直传 OSS，不再经过后端，所以这里只需要够普通表单用。
    # （3M 出向带宽 + 40G 盘扛不住 20MB 原图的分发与存储，见 DEPLOYMENT.md）
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # ── 阿里云 OSS（图片直传）────────────────────────────────────
    # 用 RAM 子账号，且只授权这一个 bucket；主账号 AccessKey 泄露 = 整个云账号沦陷。
    OSS_ACCESS_KEY_ID = os.getenv('OSS_ACCESS_KEY_ID')
    OSS_ACCESS_KEY_SECRET = os.getenv('OSS_ACCESS_KEY_SECRET')
    OSS_BUCKET = os.getenv('OSS_BUCKET')

    # 公网 endpoint：用于签发给浏览器的预签名 PUT URL。
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT')

    # 后端自用（head_object / delete_object）。ECS 上填同地域的内网 endpoint，
    # 走内网免流量费、也不占那 3M 出向带宽；本地开发不填，自动回落到公网。
    OSS_ENDPOINT_INTERNAL = os.getenv('OSS_ENDPOINT_INTERNAL') or OSS_ENDPOINT

    # 图片对外的访问前缀，存进 photo_url / avatar_url 的就是它。
    # 备案通过后换成自定义域名 https://img.gensoumono.cn （OSS 默认域名会给图片
    # 强制加 Content-Disposition: attachment，导致「点开看原图」变成下载）。
    OSS_PUBLIC_BASE = os.getenv('OSS_PUBLIC_BASE', '')

    # ── 合规 ────────────────────────────────────────────────────
    # ICP 备案号，形如「沪ICP备2026XXXXXXX号」，在阿里云备案控制台查。工信部要求
    # 大陆站点在页脚展示备案号并链到 beian.miit.gov.cn，不放会被巡查。
    # 走环境变量而不是写死在模板里：占位号一旦硬编码就很容易跟着代码发到生产上，
    # 而挂一个错号比不挂更糟。留空时页脚不渲染这一行，本地开发因此无需配置。
    ICP_BEIAN = os.getenv('ICP_BEIAN', '')
