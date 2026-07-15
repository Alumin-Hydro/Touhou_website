from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models import User, Post, rank_ladder, MAX_CUSTOM_TITLE
from app.oss import resolve_upload, delete_by_url

profile_bp = Blueprint('profile', __name__)

@profile_bp.route('/user/<username>')
def view_profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    recent_posts = user.posts.order_by(Post.created_at.desc()).limit(5).all()
    return render_template('profile/view.html', profile_user=user, recent_posts=recent_posts, rank_ladder=rank_ladder())

@profile_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        action = request.form.get('action', 'profile')

        if action == 'profile':
            bio = request.form.get('bio', '').strip()
            custom_title = request.form.get('custom_title', '').strip()[:MAX_CUSTOM_TITLE]

            # 头像已由浏览器直传 OSS，表单里只带回一个 key
            new_avatar = resolve_upload(request.form.get('avatar_key'), current_user.id)
            if new_avatar:
                delete_by_url(current_user.avatar_url)
                current_user.avatar_url = new_avatar

            current_user.bio = bio if bio else None
            current_user.custom_title = custom_title if custom_title else None
            db.session.commit()
            flash('个人资料已更新')

        elif action == 'search_prefs':
            per_page = request.form.get('search_per_page', 20, type=int)
            if per_page not in (10, 20, 30, 50):
                per_page = 20
            scope = request.form.get('search_scope', 'all')
            if scope not in ('all', 'title'):
                scope = 'all'
            search_type = request.form.get('search_type', 'all')
            if search_type not in ('all', 'posts', 'users'):
                search_type = 'all'
            current_user.search_per_page = per_page
            current_user.search_scope = scope
            current_user.search_type = search_type
            db.session.commit()
            flash('搜索偏好已更新')

        elif action == 'password':
            current_pw = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            confirm_pw = request.form.get('confirm_password', '')
            if not current_user.check_password(current_pw):
                flash('当前密码不正确')
            elif len(new_pw) < 6:
                flash('新密码至少需要6位')
            elif new_pw != confirm_pw:
                flash('两次输入的新密码不一致')
            else:
                current_user.set_password(new_pw)
                db.session.commit()
                flash('密码已成功修改')

        return redirect(url_for('profile.settings'))

    return render_template('profile/settings.html')
