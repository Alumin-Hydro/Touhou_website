from datetime import datetime, timezone
from flask_login import UserMixin
from sqlalchemy import Index, false, text
from app import db, login_manager


def utcnow():
    """Return naive UTC for existing SQLite DateTime columns without deprecation."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 成长体系：单一数据源 ──────────────────────────────────────────
# 积分 = 帖子数 × 3 + 回复数（见 User.score）。等级 / 成就都读下面这两份常量，
# 不再在 Python 和模板里各抄一份。
MAX_CUSTOM_TITLE = 64

# 等级阶梯，从高到低；元素为 (积分下限, 名称, 颜色)。
RANKS = [
    (200, '幻想之鸟', 'red'),
    (100, '猛禽', 'orange'),
    (50, '留鸟', 'blue'),
    (20, '候鸟', 'teal'),
    (5, '雏鸟', 'green'),
    (0, '初来乍到', 'gray'),
]
# 站长 / 管理员是身份而非积分档位，单独表示。
ADMIN_RANK_NAME = '幻想乡管理员'
ADMIN_RANK_COLOR = 'dark'
SITE_OWNER_RANK_NAME = '幻想乡站长'
SITE_OWNER_RANK_COLOR = 'red'

# 成就定义：(指标, 阈值, 名称, 描述, 颜色)；指标 ∈ {'post','comment','bird'}。
ACHIEVEMENTS = [
    ('post', 1, '初啼', '发表了第一篇帖子', 'green'),
    ('post', 10, '博学鸟', '发表了10篇帖子', 'blue'),
    ('post', 50, '著作等身', '发表了50篇帖子', 'gold'),
    ('comment', 10, '活跃社员', '发表了10条回复', 'teal'),
    ('comment', 100, '幻想之声', '发表了100条回复', 'purple'),
    ('bird', 1, '初次目击', '记录了第一只鸟', 'teal'),
    ('bird', 5, '观鸟达人', '记录了5种鸟类', 'orange'),
    ('bird', 20, '幻想博物志', '记录了20种鸟类', 'red'),
]


def rank_ladder():
    """从低到高的等级阶梯，含派生的积分区间文案，供模板展示。派生自 RANKS，单一数据源。"""
    ordered = sorted(RANKS, key=lambda r: r[0])  # 按积分下限升序
    ladder = []
    for i, (lo, name, color) in enumerate(ordered):
        hi = ordered[i + 1][0] - 1 if i + 1 < len(ordered) else None
        cond = f'积分 {lo}+' if hi is None else f'积分 {lo}–{hi}'
        ladder.append({'name': name, 'color': color, 'cond': cond})
    ladder.append({'name': ADMIN_RANK_NAME, 'color': ADMIN_RANK_COLOR, 'cond': '管理员特权'})
    ladder.append({'name': SITE_OWNER_RANK_NAME, 'color': SITE_OWNER_RANK_COLOR, 'cond': '站长身份'})
    return ladder


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(128))
    verified = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    # 站长是唯一最高应用角色；不要求同时把 is_admin 设为 True，避免双标记状态漂移。
    is_site_owner = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    is_muted = db.Column(db.Boolean, default=False)
    mute_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    # 个人资料
    bio = db.Column(db.Text, nullable=True)
    avatar_url = db.Column(db.String(256), nullable=True)
    custom_title = db.Column(db.String(MAX_CUSTOM_TITLE), nullable=True)
    # 搜索偏好
    search_per_page = db.Column(db.Integer, default=20)
    search_scope = db.Column(db.String(20), default='all')
    search_type = db.Column(db.String(20), default='all')

    posts = db.relationship('Post', backref='author', lazy='dynamic', passive_deletes=True)
    comments = db.relationship('Comment', backref='author', lazy='dynamic', passive_deletes=True)
    sent_messages = db.relationship(
        'Message',
        foreign_keys='Message.sender_id',
        backref='sender',
        lazy='dynamic',
        passive_deletes=True,
    )
    received_messages = db.relationship(
        'Message',
        foreign_keys='Message.receiver_id',
        backref='receiver',
        lazy='dynamic',
        passive_deletes=True,
    )

    __table_args__ = (
        Index(
            'uq_user_single_site_owner',
            'is_site_owner',
            unique=True,
            sqlite_where=text('is_site_owner = 1'),
            postgresql_where=text('is_site_owner = true'),
        ),
    )

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
            return self.mute_expires > utcnow()
        return False

    def can_post(self):
        if not self.is_muted:
            return True
        if self.mute_expires and utcnow() > self.mute_expires:
            self.is_muted = False
            self.mute_expires = None
            db.session.commit()
            return True
        return False

    @property
    def is_staff(self):
        """可进入管理页并执行日常管理的站务人员。"""
        return bool(self.is_site_owner or self.is_admin)

    @property
    def can_manage_content(self):
        """可越过内容所有权编辑、置顶或删除帖子/回复。"""
        return self.is_staff

    @property
    def role_label(self):
        if self.is_site_owner:
            return '站长'
        if self.is_admin:
            return '管理员'
        return '普通用户'

    @property
    def post_count(self):
        return self.posts.count()

    @property
    def comment_count(self):
        return self.comments.count()

    @property
    def bird_record_count(self):
        return self.posts.filter(Post.bird_name != None, Post.bird_name != '').count()

    @property
    def score(self):
        """贡献积分：驱动等级的唯一数值。公式只此一处。"""
        return self.post_count * 3 + self.comment_count

    def get_rank(self):
        """当前等级 {'name','color'}，读自 RANKS 单一数据源。"""
        if self.is_site_owner:
            return {'name': SITE_OWNER_RANK_NAME, 'color': SITE_OWNER_RANK_COLOR}
        if self.is_admin:
            return {'name': ADMIN_RANK_NAME, 'color': ADMIN_RANK_COLOR}
        score = self.score
        for min_score, name, color in RANKS:
            if score >= min_score:
                return {'name': name, 'color': color}
        last = RANKS[-1]
        return {'name': last[1], 'color': last[2]}

    def get_rank_title(self):
        return self.get_rank()['name']

    def get_achievements(self):
        """已达成的成就，读自 ACHIEVEMENTS 单一数据源。管理员身份由等级体现，不再作为成就。"""
        metrics = {
            'post': self.post_count,
            'comment': self.comment_count,
            'bird': self.bird_record_count,
        }
        return [
            {'name': name, 'desc': desc, 'color': color}
            for metric, threshold, name, desc, color in ACHIEVEMENTS
            if metrics[metric] >= threshold
        ]

class Board(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(200))
    sort_order = db.Column(db.Integer, nullable=False, default=1000, server_default=text('1000'))
    posts = db.relationship('Post', backref='board', lazy='dynamic')

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='RESTRICT'), nullable=False
    )
    board_id = db.Column(
        db.Integer, db.ForeignKey('board.id', ondelete='RESTRICT'), nullable=False
    )
    bird_name = db.Column(db.String(64))
    location = db.Column(db.String(128))
    photo_url = db.Column(db.String(256))
    is_pinned = db.Column(db.Boolean, default=False)

    comments = db.relationship('Comment', backref='post', lazy='dynamic', cascade='all, delete-orphan')

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='RESTRICT'), nullable=False
    )
    post_id = db.Column(
        db.Integer, db.ForeignKey('post.id', ondelete='CASCADE'), nullable=False
    )

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    is_read = db.Column(db.Boolean, default=False)
    sender_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='RESTRICT'), nullable=False
    )
    receiver_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='RESTRICT'), nullable=False
    )
