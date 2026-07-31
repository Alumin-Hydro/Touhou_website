# app/utils.py
import smtplib
import base64
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer

# 图片不再落本地磁盘：上传/删除都走 app/oss.py 的直传链路。

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
    try:
        # 配置和邮件头构造也属于可恢复的发送流程。配置缺失时返回 False，让注册路由
        # 回滚尚未验证的用户，而不是在进入 SMTP try 之前抛异常造成 500。
        smtp_server = current_app.config['MAIL_SERVER']
        smtp_port = current_app.config['MAIL_PORT']
        sender_email = current_app.config['MAIL_USERNAME']
        password = current_app.config['MAIL_PASSWORD']
        if not sender_email or not password:
            raise ValueError('邮件服务账号或密码未配置')

        msg = MIMEText(html, 'html', 'utf-8')
        msg['Subject'] = str(Header(subject, 'utf-8'))
        msg['From'] = formataddr(('幻想博物志', sender_email))
        msg['To'] = str(Header(user_email, 'utf-8'))

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
