from flask import Blueprint, render_template
from sqlalchemy import func

from app import db
from app.models import Post, Board
from app.content_rules import BIRDING_BOARD_NAME

birding_bp = Blueprint('birding', __name__)

@birding_bp.route('/birds')
def bird_list():
    # 统计每种鸟类的帖子数量
    birds = db.session.query(
        Post.bird_name,
        func.count(Post.id).label('count')
    ).join(Board).filter(
        Board.name == BIRDING_BOARD_NAME,
        Post.bird_name.is_not(None),
        Post.bird_name != '',
    ).group_by(Post.bird_name).all()
    return render_template('birding/bird_list.html', birds=birds)

@birding_bp.route('/bird/<bird_name>')
def bird_posts(bird_name):
    posts = Post.query.join(Board).filter(
        Board.name == BIRDING_BOARD_NAME,
        Post.bird_name == bird_name,
    ).order_by(Post.created_at.desc()).all()
    return render_template('birding/bird_posts.html', bird_name=bird_name, posts=posts)
