from datetime import datetime
from flask_login import UserMixin
from app import db, login_manager

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(128))
    verified = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_muted = db.Column(db.Boolean, default=False)
    mute_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # 个人资料
    bio = db.Column(db.Text, nullable=True)
    avatar_url = db.Column(db.String(256), nullable=True)
    custom_title = db.Column(db.String(64), nullable=True)
    # 搜索偏好
    search_per_page = db.Column(db.Integer, default=20)
    search_scope = db.Column(db.String(20), default='all')
    search_type = db.Column(db.String(20), default='all')

    posts = db.relationship('Post', backref='author', lazy='dynamic')
    comments = db.relationship('Comment', backref='author', lazy='dynamic')
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy='dynamic')
    received_messages = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy='dynamic')

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

    def is_permanently_muted(self):
        return self.is_muted and self.mute_expires is None

    def is_temporarily_muted(self):
        if self.is_muted and self.mute_expires:
            return self.mute_expires > datetime.utcnow()
        return False

    def can_post(self):
        if not self.is_muted:
            return True
        if self.mute_expires and datetime.utcnow() > self.mute_expires:
            self.is_muted = False
            self.mute_expires = None
            db.session.commit()
            return True
        return False

    @property
    def post_count(self):
        return self.posts.count()

    @property
    def comment_count(self):
        return self.comments.count()

    @property
    def bird_record_count(self):
        return self.posts.filter(Post.bird_name != None, Post.bird_name != '').count()

    def get_rank_title(self):
        if self.is_admin:
            return '幻想乡管理员'
        score = self.post_count * 3 + self.comment_count
        if score >= 200:
            return '幻想之鸟'
        elif score >= 100:
            return '猛禽'
        elif score >= 50:
            return '留鸟'
        elif score >= 20:
            return '候鸟'
        elif score >= 5:
            return '雏鸟'
        else:
            return '初来乍到'

    def get_achievements(self):
        achievements = []
        pc = self.post_count
        cc = self.comment_count
        bc = self.bird_record_count
        if pc >= 1:
            achievements.append({'name': '初啼', 'desc': '发表了第一篇帖子', 'color': 'green'})
        if pc >= 10:
            achievements.append({'name': '博学鸟', 'desc': '发表了10篇帖子', 'color': 'blue'})
        if pc >= 50:
            achievements.append({'name': '著作等身', 'desc': '发表了50篇帖子', 'color': 'gold'})
        if cc >= 10:
            achievements.append({'name': '活跃社员', 'desc': '发表了10条回复', 'color': 'teal'})
        if cc >= 100:
            achievements.append({'name': '幻想之声', 'desc': '发表了100条回复', 'color': 'purple'})
        if bc >= 1:
            achievements.append({'name': '初次目击', 'desc': '记录了第一只鸟', 'color': 'teal'})
        if bc >= 5:
            achievements.append({'name': '观鸟达人', 'desc': '记录了5种鸟类', 'color': 'orange'})
        if bc >= 20:
            achievements.append({'name': '幻想博物志', 'desc': '记录了20种鸟类', 'color': 'red'})
        if self.is_admin:
            achievements.append({'name': '管理员', 'desc': '幻想乡的管理者', 'color': 'dark'})
        return achievements

class Board(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(200))
    posts = db.relationship('Post', backref='board', lazy='dynamic')

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    board_id = db.Column(db.Integer, db.ForeignKey('board.id'))
    bird_name = db.Column(db.String(64))
    location = db.Column(db.String(128))
    photo_url = db.Column(db.String(256))
    is_pinned = db.Column(db.Boolean, default=False)

    comments = db.relationship('Comment', backref='post', lazy='dynamic', cascade='all, delete-orphan')

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'))

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
