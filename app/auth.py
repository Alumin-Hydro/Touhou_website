from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import User
from app.utils import (
    generate_verify_token, confirm_verify_token,
    send_verification_email, send_reset_email,
)
from werkzeug.security import generate_password_hash
from email_validator import EmailNotValidError, validate_email

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        if not username:
            flash('用户名不能为空')
            return redirect(url_for('auth.register'))
        if len(username) > 64:
            flash('用户名不能超过64个字符')
            return redirect(url_for('auth.register'))
        if len(password) < 8:
            flash('密码至少需要8位')
            return redirect(url_for('auth.register'))
        try:
            email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError:
            flash('请输入有效的邮箱地址')
            return redirect(url_for('auth.register'))
        if len(email) > 120:
            flash('邮箱地址不能超过120个字符')
            return redirect(url_for('auth.register'))
        # 简单校验
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return redirect(url_for('auth.register'))
        if User.query.filter_by(email=email).first():
            flash('邮箱已注册')
            return redirect(url_for('auth.register'))
        user = User(username=username, email=email, verified=False)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        token = generate_verify_token(email)
        if send_verification_email(email, username, token):
            flash('注册成功！验证邮件已发送，请查收并激活账户')
        else:
            flash('邮件发送失败，请联系管理员')
            db.session.delete(user)
            db.session.commit()
            return redirect(url_for('auth.register'))
        return redirect(url_for('auth.login'))
    return render_template('auth/register.html')

@auth_bp.route('/verify/<token>')
def verify_email(token):
    email = confirm_verify_token(token)
    if not email:
        flash('验证链接无效或已过期，请重新注册')
        return redirect(url_for('auth.register'))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('用户不存在')
        return redirect(url_for('auth.register'))
    if user.verified:
        flash('邮箱已激活，请直接登录')
    else:
        user.verified = True
        db.session.commit()
        flash('邮箱验证成功！现在可以登录了')
    return redirect(url_for('auth.login'))

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.verified:
                flash('请先验证邮箱')
                return redirect(url_for('auth.login'))
            login_user(user)
            flash(f'欢迎回来，{username}')
            return redirect(url_for('index'))
        flash('用户名或密码错误')
    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已退出登录')
    return redirect(url_for('index'))

@auth_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_verify_token(email, salt='pwd-reset')
            send_reset_email(email, user.username, token)
        # 无论邮箱是否存在都返回同样的提示，避免暴露注册信息
        flash('如果该邮箱已注册，重置链接已发送，请查收')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')

@auth_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    email = confirm_verify_token(token, salt='pwd-reset')
    if not email:
        flash('重置链接无效或已过期，请重新申请')
        return redirect(url_for('auth.forgot_password'))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('用户不存在')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if len(new_password) < 6:
            flash('新密码至少需要6位')
            return render_template('auth/reset_password.html', token=token)
        if new_password != confirm_password:
            flash('两次输入的新密码不一致')
            return render_template('auth/reset_password.html', token=token)
        user.set_password(new_password)
        db.session.commit()
        flash('密码已重置，请使用新密码登录')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', token=token)
