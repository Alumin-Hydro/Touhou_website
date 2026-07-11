from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from sqlalchemy import event
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
