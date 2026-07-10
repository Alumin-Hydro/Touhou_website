from flask import Blueprint, render_template
from app.models import Post, db
from sqlalchemy import func

birding_bp = Blueprint('birding', __name__)

@birding_bp.route('/birds')
def bird_list():
    # 统计每种鸟类的帖子数量
    birds = db.session.query(
        Post.bird_name,
        func.count(Post.id).label('count')
    ).filter(Post.bird_name != '').group_by(Post.bird_name).all()
    return render_template('birding/bird_list.html', birds=birds)

@birding_bp.route('/bird/<bird_name>')
def bird_posts(bird_name):
    posts = Post.query.filter_by(bird_name=bird_name).order_by(Post.created_at.desc()).all()
    return render_template('birding/bird_posts.html', bird_name=bird_name, posts=posts)
