# app/utils.py
import os
import uuid
import smtplib
import base64
from email.mime.text import MIMEText
from email.header import Header
from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer

_POST_PHOTO_ALLOWED = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff', 'tif'}

def save_post_photo(file):
    """将上传的帖子照片原图保存到 static/uploads/，返回 URL 路径，失败返回 None。"""
    if not (file and file.filename and '.' in file.filename):
        return None
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in _POST_PHOTO_ALLOWED:
        return None
    filename = f"post_{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, filename))
    return f"/static/uploads/{filename}"

def delete_local_photo(url):
    """若 url 指向本地上传文件则删除，外链或空值直接忽略。"""
    if not url:
        return
    for prefix in ('/static/uploads/', '/static/avatars/'):
        if url.startswith(prefix):
            path = os.path.join(current_app.root_path,
                                url.lstrip('/').replace('/', os.sep))
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return

def generate_verify_token(email, salt='email-verify'):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return serializer.dumps(email, salt=salt)

def confirm_verify_token(token, expiration=3600, salt='email-verify'):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt=salt, max_age=expiration)
    except Exception:
        return None
    return email

def _send_html_email(user_email, subject, html):
    """发送 HTML 邮件的底层 SMTP 逻辑（增强版，强制 AUTH LOGIN），成功返回 True。"""
    msg = MIMEText(html, 'html', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = Header('幻想博物志', 'utf-8')
    msg['To'] = Header(user_email, 'utf-8')

    # 从配置读取
    smtp_server = current_app.config['MAIL_SERVER']
    smtp_port = current_app.config['MAIL_PORT']
    sender_email = current_app.config['MAIL_USERNAME']
    password = current_app.config['MAIL_PASSWORD']

    try:
        # 方法一：使用 SSL 直接连接（端口465）
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30) as smtp:
                # 手动进行 AUTH LOGIN
                smtp.ehlo()
                code, resp = smtp.docmd("AUTH", "LOGIN")
                if code == 334:
                    # 发送 base64 编码的用户名
                    smtp.docmd(base64.b64encode(sender_email.encode()).decode())
                    code, resp = smtp.docmd(base64.b64encode(password.encode()).decode())
                    if code != 235:
                        raise smtplib.SMTPAuthenticationError(code, resp)
                else:
                    # 回退到普通 login
                    smtp.login(sender_email, password)
                smtp.sendmail(sender_email, [user_email], msg.as_string())
        else:
            # 方法二：使用 STARTTLS（端口587）
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(sender_email, password)
                smtp.sendmail(sender_email, [user_email], msg.as_string())
        print(f"邮件发送至 {user_email} 成功")
        return True
    except Exception as e:
        print(f"邮件发送失败，详细错误：{e}")
        # 打印更详细的信息
        import traceback
        traceback.print_exc()
        return False

def send_verification_email(user_email, username, token):
    """发送邮箱验证邮件"""
    verify_url = url_for('auth.verify_email', token=token, _external=True)
    html = f"""
    <html>
    <body>
        <h2>欢迎 {username} 加入 东方Project × 观鸟 论坛——幻想博物志</h2>
        <p>请点击以下链接验证您的邮箱（1小时内有效）：</p>
        <p><a href="{verify_url}">{verify_url}</a></p>
        <p>如果无法点击，请复制链接到浏览器打开。</p>
        <p style="color:green;">—— 诚邀您一同观察幻想乡的鸟类！ ——</p>
    </body>
    </html>
    """
    return _send_html_email(user_email, '[幻想博物志] 验证您的邮箱', html)

def send_reset_email(user_email, username, token):
    """发送密码重置邮件"""
    reset_url = url_for('auth.reset_password', token=token, _external=True)
    html = f"""
    <html>
    <body>
        <h2>{username}，找回你的幻想博物志账号</h2>
        <p>请点击以下链接重置密码（1小时内有效）：</p>
        <p><a href="{reset_url}">{reset_url}</a></p>
        <p>如果无法点击，请复制链接到浏览器打开。</p>
        <p>如果这不是你本人的操作，请忽略此邮件。</p>
    </body>
    </html>
    """
    return _send_html_email(user_email, '[幻想博物志] 重置您的密码', html)
