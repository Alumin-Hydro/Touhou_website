from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import User, Message
from datetime import datetime

message_bp = Blueprint('message', __name__, url_prefix='/message')

@message_bp.route('/inbox')
@login_required
def inbox():
    messages = current_user.received_messages.order_by(Message.created_at.desc()).all()
    return render_template('message/inbox.html', messages=messages)

@message_bp.route('/sent')
@login_required
def sent():
    messages = current_user.sent_messages.order_by(Message.created_at.desc()).all()
    return render_template('message/sent.html', messages=messages)

@message_bp.route('/send', methods=['GET', 'POST'])
@login_required
def send():
    if request.method == 'POST':
        receiver_name = request.form.get('receiver')
        content = request.form.get('content')
        receiver = User.query.filter_by(username=receiver_name).first()
        if not receiver:
            flash('收件人不存在，和恋恋一样难以被人看到呢...')
            return redirect(url_for('message.send'))
        if receiver.id == current_user.id:
            flash('不能给自己发私信')
            return redirect(url_for('message.send'))
        msg = Message(content=content, sender_id=current_user.id, receiver_id=receiver.id)
        db.session.add(msg)
        db.session.commit()
        flash('私信已发送')
        return redirect(url_for('message.inbox'))
    return render_template('message/send.html')

@message_bp.route('/view/<int:msg_id>')
@login_required
def view(msg_id):
    msg = Message.query.get_or_404(msg_id)
    if msg.receiver_id != current_user.id and msg.sender_id != current_user.id:
        abort(403)
    if not msg.is_read and msg.receiver_id == current_user.id:
        msg.is_read = True
        db.session.commit()
    return render_template('message/view.html', msg=msg)

@message_bp.route('/delete/<int:msg_id>')
@login_required
def delete(msg_id):
    msg = Message.query.get_or_404(msg_id)
    if msg.receiver_id != current_user.id and msg.sender_id != current_user.id:
        abort(403)
    db.session.delete(msg)
    db.session.commit()
    flash('私信已删除')
    return redirect(request.referrer or url_for('message.inbox'))
