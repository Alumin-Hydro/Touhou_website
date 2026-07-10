#!/usr/bin/env python3
"""独立的命令行工具，用于将指定用户设置为管理员。"""

import sys
import os
from dotenv import load_dotenv
from pathlib import Path

# 加载环境变量
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

# 设置 Flask 环境（避免相对导入问题）
sys.path.insert(0, str(BASE_DIR))

from app import create_app, db
from app.models import User

def set_admin(username_or_email, is_admin=True):
    app = create_app()
    with app.app_context():
        # 先按用户名查找，再按邮箱查找
        user = User.query.filter_by(username=username_or_email).first()
        if not user:
            user = User.query.filter_by(email=username_or_email).first()
        if not user:
            print(f"错误：未找到用户 '{username_or_email}'")
            return False
        if user.is_admin == is_admin:
            print(f"用户 {user.username} 已经是{'管理员' if is_admin else '普通用户'}，无需更改")
            return True
        user.is_admin = is_admin
        db.session.commit()
        print(f"成功将用户 {user.username} (邮箱 {user.email}) 设为{'管理员' if is_admin else '普通用户'}")
        return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python set_admin.py <用户名或邮箱> [1/0]")
        print("示例：python set_admin.py alice 1    # 设为管理员")
        print("示例：python set_admin.py alice 0    # 撤销管理员")
        sys.exit(1)
    target = sys.argv[1]
    admin_flag = True
    if len(sys.argv) >= 3:
        admin_flag = sys.argv[2].lower() in ('1', 'true', 'yes', 'y')
    set_admin(target, admin_flag)
