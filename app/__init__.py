from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from werkzeug.middleware.proxy_fix import ProxyFix
from settings import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问该页面'
csrf = CSRFProtect()

def _enable_sqlite_wal(engine):
    """SQLite 在 gunicorn 多进程下必须开 WAL，否则读会阻塞写、写会阻塞读。"""
    @event.listens_for(engine, 'connect')
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')    # 读写不互相阻塞
        cur.execute('PRAGMA busy_timeout=15000')  # 拿不到锁时等待，而不是立刻报 database is locked
        cur.execute('PRAGMA synchronous=NORMAL')  # WAL 下的推荐值：掉电最多丢最后一个事务，不会损坏库
        cur.close()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # 生产上 gunicorn 只听 127.0.0.1，公网由 nginx 反代进来。没有这一层的话 Flask 看到的
    # 永远是「http + 127.0.0.1」，而 app/utils.py 的邮箱验证链接和找回密码链接都是用
    # url_for(..., _external=True) 拼的 —— 上了 HTTPS 之后发出去的邮件里仍会是 http:// 链接。
    # 眼下靠 301 还能跳到 https，但用户看到的是明文链接，且以后加 HSTS 就会直接失效。
    # 顺带把 X-Real-IP 透进来，否则 request.remote_addr 恒为 127.0.0.1。
    # 只信任 1 层代理：nginx 就在本机、是唯一的中间层，写 1 才不会被伪造的
    # X-Forwarded-For 顶穿（客户端自带的那一段永远排在 nginx 追加的真实 IP 前面）。
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        if db.engine.dialect.name == 'sqlite':
            _enable_sqlite_wal(db.engine)

    # 注册蓝图
    from app.auth import auth_bp
    from app.forum import forum_bp
    from app.birding import birding_bp
    from app.admin import admin_bp
    from app.message import message_bp
    from app.profile import profile_bp
    from app.oss import oss_bp, thumb

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(forum_bp, url_prefix='/forum')
    app.register_blueprint(birding_bp, url_prefix='/birding')
    app.register_blueprint(admin_bp)
    app.register_blueprint(message_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(oss_bp)

    # 模板里用 {{ post.photo_url | thumb(800) }} 取缩略图（OSS 实时生成）
    app.jinja_env.filters['thumb'] = thumb

    # 首页
    @app.route('/')
    def index():
        from app.models import Board, Post
        boards = Board.query.all()
        latest_posts = Post.query.order_by(Post.created_at.desc()).limit(10).all()
        bird_board = Board.query.filter_by(name='观鸟记录').first() or (boards[0] if boards else None)
        return render_template('index.html', boards=boards, latest_posts=latest_posts, bird_board=bird_board)

    # 全局上下文处理器：让所有模板都能访问 boards 和当前用户的未读私信数
    @app.context_processor
    def inject_globals():
        from app.models import Board, Message
        unread_count = 0
        if current_user.is_authenticated:
            unread_count = Message.query.filter_by(receiver_id=current_user.id, is_read=False).count()
        return dict(boards=Board.query.all(), unread_count=unread_count)

    # CSRF 校验失败。WTF_CSRF_TIME_LIMIT=None 之后这只剩下 session 本身没了的情况
    # （关掉浏览器再回来、清了 cookie、轮换了 SECRET_KEY）—— 而那时候用户手里往往正
    # 攥着一篇刚写完的长帖。Flask 默认吐的是英文 400，还不会告诉他正文其实还在。
    @app.errorhandler(CSRFError)
    def csrf_error(e):
        # /oss/sign 是 oss-upload.js 用 fetch 调的：它看见非 JSON 响应会一律当成登录
        # 失效，报一句风马牛不相及的错。这里回 JSON，真实原因才透得过去。
        if request.path.startswith('/oss/'):
            return jsonify(error='页面已过期或登录已失效，请刷新页面后重试'), 400
        return render_template('errors/400.html'), 400

    # 自定义错误页：保持网站和风视觉主题，而非 Flask 默认英文错误页
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(413)
    def file_too_large(e):
        return render_template('errors/413.html'), 413

    return app
