from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from settings import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问该页面'
csrf = CSRFProtect()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # 注册蓝图
    from app.auth import auth_bp
    from app.forum import forum_bp
    from app.birding import birding_bp
    from app.admin import admin_bp
    from app.message import message_bp
    from app.profile import profile_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(forum_bp, url_prefix='/forum')
    app.register_blueprint(birding_bp, url_prefix='/birding')
    app.register_blueprint(admin_bp)
    app.register_blueprint(message_bp)
    app.register_blueprint(profile_bp)

    # 首页
    @app.route('/')
    def index():
        from app.models import Board, Post
        boards = Board.query.all()
        latest_posts = Post.query.order_by(Post.created_at.desc()).limit(10).all()
        return render_template('index.html', boards=boards, latest_posts=latest_posts)

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
