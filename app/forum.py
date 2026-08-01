from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import Board, Post, Comment, User
from app.oss import resolve_upload, delete_by_url
from app.content_rules import normalize_post_metadata
from sqlalchemy import or_

forum_bp = Blueprint('forum', __name__)

@forum_bp.route('/board/<int:board_id>')
def board(board_id):
    """板块内帖子列表，置顶帖子优先，再按时间倒序"""
    board = db.get_or_404(Board, board_id)
    page = request.args.get('page', 1, type=int)
    posts = board.posts.order_by(Post.is_pinned.desc(), Post.created_at.desc()).paginate(page=page, per_page=20)
    return render_template('forum/board.html', board=board, posts=posts)

@forum_bp.route('/post/<int:post_id>')
def view_post(post_id):
    """查看单个帖子及其回复"""
    post = db.get_or_404(Post, post_id)
    return render_template('forum/post.html', post=post)

@forum_bp.route('/new_post/<int:board_id>', methods=['GET', 'POST'])
@login_required
def new_post(board_id):
    """发布新帖子（禁言用户无法发帖）"""
    if not current_user.can_post():
        flash('您当前被禁言，无法发布新帖子')
        return redirect(url_for('forum.board', board_id=board_id))

    board = db.get_or_404(Board, board_id)
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        bird_name = request.form.get('bird_name', '')
        location = request.form.get('location', '')
        try:
            bird_name, location = normalize_post_metadata(
                board.name, bird_name, location
            )
        except ValueError as error:
            flash(str(error))
            return render_template('forum/new_post.html', board=board), 400
        # 图片已由浏览器直传 OSS，表单里只带回一个 key
        photo_url = resolve_upload(request.form.get('photo_key'), current_user.id) or ''
        post = Post(
            title=title,
            content=content,
            user_id=current_user.id,
            board_id=board.id,
            bird_name=bird_name,
            location=location,
            photo_url=photo_url
        )
        db.session.add(post)
        db.session.commit()
        flash('帖子发布成功！')
        return redirect(url_for('forum.view_post', post_id=post.id))
    return render_template('forum/new_post.html', board=board)

@forum_bp.route('/edit_post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def edit_post(post_id):
    """编辑帖子：作者本人或管理员可编辑"""
    post = db.get_or_404(Post, post_id)
    if post.author != current_user and not current_user.can_manage_content:
        abort(403)
    if request.method == 'POST':
        bird_name = request.form.get('bird_name', '')
        location = request.form.get('location', '')
        try:
            bird_name, location = normalize_post_metadata(
                post.board.name, bird_name, location
            )
        except ValueError as error:
            flash(str(error))
            return render_template('forum/edit_post.html', post=post), 400
        post.title = request.form['title']
        post.content = request.form['content']
        post.bird_name = bird_name
        post.location = location
        new_photo = resolve_upload(request.form.get('photo_key'), current_user.id)
        if new_photo:
            delete_by_url(post.photo_url)
            post.photo_url = new_photo
        db.session.commit()
        flash('帖子已更新')
        return redirect(url_for('forum.view_post', post_id=post.id))
    return render_template('forum/edit_post.html', post=post)

@forum_bp.route('/delete_post/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    """删除帖子：作者本人或管理员可删除"""
    post = db.get_or_404(Post, post_id)
    if post.author != current_user and not current_user.can_manage_content:
        abort(403)
    board_id = post.board_id
    delete_by_url(post.photo_url)
    db.session.delete(post)
    db.session.commit()
    flash('帖子已删除')
    return redirect(url_for('forum.board', board_id=board_id))

@forum_bp.route('/comment/<int:post_id>', methods=['POST'])
@login_required
def add_comment(post_id):
    """添加回复（禁言用户无法回复）"""
    if not current_user.can_post():
        flash('您当前被禁言，无法回复')
        return redirect(url_for('forum.view_post', post_id=post_id))

    post = db.get_or_404(Post, post_id)
    content = request.form['content']
    if content.strip():
        comment = Comment(content=content, user_id=current_user.id, post_id=post.id)
        db.session.add(comment)
        db.session.commit()
        flash('回复已添加')
    return redirect(url_for('forum.view_post', post_id=post.id))

@forum_bp.route('/delete_comment/<int:comment_id>', methods=['POST'])
@login_required
def delete_comment(comment_id):
    """删除回复：作者本人或管理员可删除"""
    comment = db.get_or_404(Comment, comment_id)
    if comment.author != current_user and not current_user.can_manage_content:
        abort(403)
    post_id = comment.post_id
    db.session.delete(comment)
    db.session.commit()
    flash('回复已删除')
    return redirect(url_for('forum.view_post', post_id=post_id))

@forum_bp.route('/search')
def search():
    """搜索帖子/用户：按用户偏好的范围、数量和类型返回结果"""
    keyword = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)

    per_page = 20
    scope = 'all'
    search_type = 'all'
    if current_user.is_authenticated:
        per_page = current_user.search_per_page or 20
        scope = current_user.search_scope or 'all'
        search_type = current_user.search_type or 'all'

    if not keyword:
        flash('请输入搜索关键词')
        return redirect(url_for('index'))

    matched_users = []
    if search_type in ('all', 'users'):
        matched_users = User.query.filter(
            or_(
                User.username.contains(keyword),
                User.bio.contains(keyword)
            )
        ).limit(10).all()

    posts = None
    if search_type in ('all', 'posts'):
        if scope == 'title':
            post_query = Post.query.filter(Post.title.contains(keyword))
        else:
            post_query = Post.query.filter(
                or_(Post.title.contains(keyword), Post.content.contains(keyword))
            )
        posts = post_query.order_by(Post.created_at.desc()).paginate(page=page, per_page=per_page)

    return render_template('forum/search.html',
                           posts=posts, matched_users=matched_users,
                           keyword=keyword, scope=scope, search_type=search_type)
