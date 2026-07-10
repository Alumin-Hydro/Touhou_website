from app import create_app, db
from app.models import User

app = create_app()
with app.app_context():
    username = input("请输入你的用户名: ")
    user = User.query.filter_by(username=username).first()
    if not user:
        print(f"用户 '{username}' 不存在")
    else:
        user.is_admin = True
        db.session.commit()
        print(f"用户 '{username}' 已成为管理员")
