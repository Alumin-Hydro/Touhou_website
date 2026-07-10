import os
from dotenv import load_dotenv
from pathlib import Path

# 获取项目根目录（settings.py 所在目录）
BASE_DIR = Path(__file__).resolve().parent
dotenv_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=dotenv_path)

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///forum.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.126.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 465))
    MAIL_USE_SSL = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = MAIL_USERNAME

    POSTS_PER_PAGE = 20
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 上传限制 20MB（帖子原图不压缩）
