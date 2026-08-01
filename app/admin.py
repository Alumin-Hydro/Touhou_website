from datetime import timedelta
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Comment, Post, User, utcnow
from app.oss import delete_by_url, resolve_upload


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def staff_required(func):
    """Require a live database-backed administrator or station owner role."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_staff:
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def site_owner_required(func):
    """Require the unique station owner role."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_site_owner:
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def _require_ordinary_account(user: User) -> None:
    """Staff accounts cannot be muted or deleted through daily moderation."""

    if user.is_staff:
        abort(403)


# ==================== 仪表盘 ====================
@admin_bp.route("/")
@login_required
@staff_required
def dashboard():
    return render_template(
        "admin/dashboard.html",
        user_count=User.query.count(),
        admin_count=User.query.filter_by(is_admin=True).count(),
        post_count=Post.query.count(),
        comment_count=Comment.query.count(),
    )


# ==================== 用户管理 ====================
@admin_bp.route("/users")
@login_required
@staff_required
def users():
    all_users = User.query.order_by(
        User.is_site_owner.desc(), User.is_admin.desc(), User.id
    ).all()
    return render_template("admin/users.html", users=all_users)


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
@site_owner_required
def set_user_role(user_id):
    """Grant/revoke administrator; station-owner identity is never web-mutable."""

    user = db.get_or_404(User, user_id)
    if user.is_site_owner:
        abort(403)

    role = request.form.get("role", "")
    if role not in {"admin", "member"}:
        abort(400)
    if role == "admin" and user.is_muted:
        abort(409)

    user.is_admin = role == "admin"
    db.session.commit()
    flash(
        f"已将 {user.username} 设为"
        f"{'管理员' if user.is_admin else '普通用户'}"
    )
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/mute/<int:user_id>", methods=["GET", "POST"])
@login_required
@staff_required
def mute_user(user_id):
    user = db.get_or_404(User, user_id)
    _require_ordinary_account(user)
    if request.method == "POST":
        duration = request.form.get("duration", "")
        if duration == "permanent":
            user.is_muted = True
            user.mute_expires = None
            flash(f"用户 {user.username} 已被永久禁言")
        elif duration.isdigit() and int(duration) > 0:
            hours = int(duration)
            user.is_muted = True
            user.mute_expires = utcnow() + timedelta(hours=hours)
            flash(f"用户 {user.username} 已被禁言 {hours} 小时")
        else:
            flash("无效的时长")
            return redirect(url_for("admin.mute_user", user_id=user.id))
        db.session.commit()
        return redirect(url_for("admin.users"))
    return render_template("admin/mute_user.html", user=user)


@admin_bp.route("/users/unmute/<int:user_id>", methods=["POST"])
@login_required
@staff_required
def unmute_user(user_id):
    user = db.get_or_404(User, user_id)
    _require_ordinary_account(user)
    user.is_muted = False
    user.mute_expires = None
    db.session.commit()
    flash(f"用户 {user.username} 已解除禁言")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/delete/<int:user_id>", methods=["POST"])
@login_required
@staff_required
def delete_user(user_id):
    user = db.get_or_404(User, user_id)
    _require_ordinary_account(user)
    has_content = any((
        user.posts.count(),
        user.comments.count(),
        user.sent_messages.count(),
        user.received_messages.count(),
    ))
    if has_content:
        flash("该用户仍有帖子、回复或私信；为避免留下无作者内容，暂不能删除")
        return redirect(url_for("admin.users"))

    avatar_url = user.avatar_url
    username = user.username
    db.session.delete(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("该用户刚刚产生了新的站内内容，删除已取消")
        return redirect(url_for("admin.users"))

    delete_by_url(avatar_url)
    flash(f"用户 {username} 已删除")
    return redirect(url_for("admin.users"))


# ==================== 帖子管理 ====================
@admin_bp.route("/posts")
@login_required
@staff_required
def posts():
    all_posts = Post.query.order_by(
        Post.is_pinned.desc(), Post.created_at.desc()
    ).all()
    return render_template("admin/posts.html", posts=all_posts)


@admin_bp.route("/posts/pin/<int:post_id>", methods=["POST"])
@login_required
@staff_required
def toggle_pin(post_id):
    post = db.get_or_404(Post, post_id)
    post.is_pinned = not post.is_pinned
    db.session.commit()
    flash(f'帖子“{post.title}”已{"置顶" if post.is_pinned else "取消置顶"}')
    return redirect(url_for("admin.posts"))


@admin_bp.route("/posts/edit/<int:post_id>", methods=["GET", "POST"])
@login_required
@staff_required
def edit_post(post_id):
    post = db.get_or_404(Post, post_id)
    if request.method == "POST":
        post.title = request.form["title"]
        post.content = request.form["content"]
        post.bird_name = request.form.get("bird_name", "")
        post.location = request.form.get("location", "")
        # key 里编的是当前管理者的 id，不是原作者的。
        new_photo = resolve_upload(request.form.get("photo_key"), current_user.id)
        if new_photo:
            delete_by_url(post.photo_url)
            post.photo_url = new_photo
        db.session.commit()
        flash("帖子已更新")
        return redirect(url_for("admin.posts"))
    return render_template("admin/edit_post.html", post=post)


@admin_bp.route("/posts/delete/<int:post_id>", methods=["POST"])
@login_required
@staff_required
def delete_post(post_id):
    post = db.get_or_404(Post, post_id)
    delete_by_url(post.photo_url)
    db.session.delete(post)
    db.session.commit()
    flash("帖子已删除")
    return redirect(url_for("admin.posts"))
