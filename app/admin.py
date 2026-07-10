from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import User, Post, Comment
from app.utils import save_post_photo, delete_local_photo
from datetime import datetime, timedelta

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(func):
    """装饰器：仅限管理员访问"""
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

# ==================== 仪表盘 ====================
@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    user_count = User.query.count()
    post_count = Post.query.count()
    comment_count = Comment.query.count()
    return render_template('admin/dashboard.html',
                         user_count=user_count,
                         post_count=post_count,
                         comment_count=comment_count)

# ==================== 用户管理 ====================
@admin_bp.route('/users')
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.id).all()
    return render_template('admin/users.html', users=all_users)

@admin_bp.route('/users/toggle_admin/<int:user_id>')
@login_required
@admin_required
def toggle_admin(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('不能修改自己的管理员权限')
        return redirect(url_for('admin.users'))
    if user.is_admin:
        flash('不能撤销其他管理员的管理员权限')
        return redirect(url_for('admin.users'))
    user.is_admin = not user.is_admin
    db.session.commit()
    flash(f'用户 {user.username} 的管理员权限已{"授予" if user.is_admin else "撤销"}')
    return redirect(url_for('admin.users'))

@admin_bp.route('/users/mute/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def mute_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('不能禁言管理员，管理员内战禁止')
        return redirect(url_for('admin.users'))
    if request.method == 'POST':
        duration = request.form.get('duration')
        if duration == 'permanent':
            user.is_muted = True
            user.mute_expires = None
            flash(f'用户 {user.username} 已被永久禁言')
        elif duration.isdigit():
            hours = int(duration)
            user.is_muted = True
            user.mute_expires = datetime.utcnow() + timedelta(hours=hours)
            flash(f'用户 {user.username} 已被禁言 {hours} 小时')
        else:
            flash('无效的时长')
            return redirect(url_for('admin.mute_user', user_id=user.id))
        db.session.commit()
        return redirect(url_for('admin.users'))
    return render_template('admin/mute_user.html', user=user)

@admin_bp.route('/users/unmute/<int:user_id>')
@login_required
@admin_required
def unmute_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('不能操作管理员')
        return redirect(url_for('admin.users'))
    user.is_muted = False
    user.mute_expires = None
    db.session.commit()
    flash(f'用户 {user.username} 已解除禁言')
    return redirect(url_for('admin.users'))

@admin_bp.route('/users/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('不能删除自己')
        return redirect(url_for('admin.users'))
    if user.is_admin:
        flash('不能删除管理员，不能管理员内战呀！！')
        return redirect(url_for('admin.users'))
    db.session.delete(user)
    db.session.commit()
    flash(f'用户 {user.username} 已删除')
    return redirect(url_for('admin.users'))

# ==================== 帖子管理 ====================
@admin_bp.route('/posts')
@login_required
@admin_required
def posts():
    # 置顶优先，再按发布时间倒序
    all_posts = Post.query.order_by(Post.is_pinned.desc(), Post.created_at.desc()).all()
    return render_template('admin/posts.html', posts=all_posts)

@admin_bp.route('/posts/pin/<int:post_id>')
@login_required
@admin_required
def toggle_pin(post_id):
    post = Post.query.get_or_404(post_id)
    post.is_pinned = not post.is_pinned
    db.session.commit()
    flash(f'帖子 "{post.title}" 已{"置顶" if post.is_pinned else "取消置顶"}')
    return redirect(url_for('admin.posts'))

@admin_bp.route('/posts/edit/<int:post_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if request.method == 'POST':
        post.title = request.form['title']
        post.content = request.form['content']
        post.bird_name = request.form.get('bird_name', '')
        post.location = request.form.get('location', '')
        new_photo = save_post_photo(request.files.get('photo_file'))
        if new_photo:
            delete_local_photo(post.photo_url)
            post.photo_url = new_photo
        db.session.commit()
        flash('帖子已更新')
        return redirect(url_for('admin.posts'))
    return render_template('admin/edit_post.html', post=post)

@admin_bp.route('/posts/delete/<int:post_id>')
@login_required
@admin_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    delete_local_photo(post.photo_url)
    db.session.delete(post)
    db.session.commit()
    flash('帖子已删除')
    return redirect(url_for('admin.posts'))
