from __future__ import annotations

from email import policy
from email.parser import Parser
from email.utils import parseaddr
import importlib
import os

import pytest

from app import create_app, db
from app.models import User
from settings import Config


@pytest.fixture()
def app(tmp_path):
    class TestConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        SECRET_KEY = "test-secret"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        MAIL_SERVER = "smtp.example.test"
        MAIL_PORT = 465
        MAIL_USERNAME = "verify@example.test"
        MAIL_PASSWORD = "test-password"
        OSS_ACCESS_KEY_ID = "test"
        OSS_ACCESS_KEY_SECRET = "test"
        OSS_BUCKET = "test"
        OSS_ENDPOINT = "oss-cn-shanghai.aliyuncs.com"
        OSS_ENDPOINT_INTERNAL = OSS_ENDPOINT
        OSS_PUBLIC_BASE = "https://test.oss-cn-shanghai.aliyuncs.com"

    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def test_registration_rejects_short_password_before_creating_user(app, monkeypatch):
    monkeypatch.setattr("app.auth.send_verification_email", lambda *args: True)

    response = app.test_client().post(
        "/auth/register",
        data={"username": "shortpass", "email": "short@example.test", "password": "12345"},
        follow_redirects=True,
    )

    assert "密码至少需要8位" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.filter_by(username="shortpass").first() is None


def test_registration_rejects_malformed_email_before_creating_user(app, monkeypatch):
    monkeypatch.setattr("app.auth.send_verification_email", lambda *args: True)

    response = app.test_client().post(
        "/auth/register",
        data={"username": "badmail", "email": "not-an-email", "password": "correct-horse"},
        follow_redirects=True,
    )

    assert "请输入有效的邮箱地址" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.filter_by(username="badmail").first() is None


def test_registration_rejects_username_that_is_empty_after_trimming(app, monkeypatch):
    monkeypatch.setattr("app.auth.send_verification_email", lambda *args: True)

    response = app.test_client().post(
        "/auth/register",
        data={"username": "   ", "email": "blank@example.com", "password": "correct-horse"},
        follow_redirects=True,
    )

    assert "用户名不能为空" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == 0


def test_registration_rejects_username_longer_than_model_limit(app, monkeypatch):
    monkeypatch.setattr("app.auth.send_verification_email", lambda *args: True)
    username = "用" * 65

    response = app.test_client().post(
        "/auth/register",
        data={"username": username, "email": "longname@example.com", "password": "correct-horse"},
        follow_redirects=True,
    )

    assert "用户名不能超过64个字符" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == 0


def test_registration_rejects_normalized_email_longer_than_model_limit(app, monkeypatch):
    monkeypatch.setattr("app.auth.send_verification_email", lambda *args: True)
    email = f"{'a' * 64}@{'b' * 60}.com"
    assert len(email) == 129

    response = app.test_client().post(
        "/auth/register",
        data={"username": "longmail", "email": email, "password": "correct-horse"},
        follow_redirects=True,
    )

    assert "邮箱地址不能超过120个字符" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.filter_by(username="longmail").first() is None


def test_registration_preserves_email_local_part_case_after_normalization(app, monkeypatch):
    sent_to = []
    monkeypatch.setattr(
        "app.auth.send_verification_email",
        lambda email, *args: sent_to.append(email) or True,
    )

    response = app.test_client().post(
        "/auth/register",
        data={
            "username": "mixedcase",
            "email": "Mixed.Case@EXAMPLE.COM",
            "password": "correct-horse",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username="mixedcase").one()
        assert user.email == "Mixed.Case@example.com"
    assert sent_to == ["Mixed.Case@example.com"]


def test_registration_form_exposes_password_requirement_without_provider_brand(app):
    html = app.test_client().get("/auth/register").get_data(as_text=True)
    assert 'minlength="8"' in html
    assert "基于126 SMTP" not in html


def test_smtp_message_from_header_contains_configured_sender_address(app, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            return 250, b"ok"

        def docmd(self, command, args=""):
            if command == "AUTH":
                return 334, b"username"
            if not hasattr(self, "_username_seen"):
                self._username_seen = True
                return 334, b"password"
            return 235, b"authenticated"

        def sendmail(self, sender, recipients, message):
            sent.update(sender=sender, recipients=recipients, message=message)

    monkeypatch.setattr("app.utils.smtplib.SMTP_SSL", FakeSMTP)
    from app.utils import _send_html_email

    with app.app_context():
        assert _send_html_email("user@example.test", "subject", "<p>hello</p>") is True

    parsed = Parser(policy=policy.default).parsestr(sent["message"])
    display_name, address = parseaddr(parsed["From"])
    assert display_name == "幻想博物志"
    assert address == "verify@example.test"
    assert sent["sender"] == "verify@example.test"


def test_smtp_missing_sender_configuration_returns_false(app):
    from app.utils import _send_html_email

    with app.app_context():
        app.config["MAIL_USERNAME"] = None
        assert _send_html_email("user@example.test", "subject", "<p>hello</p>") is False


def test_production_cookie_defaults_are_https_safe():
    import settings

    original_value = os.environ.pop("SESSION_COOKIE_SECURE", None)
    try:
        reloaded = importlib.reload(settings)
        assert reloaded.Config.SESSION_COOKIE_SECURE is True
        assert reloaded.Config.SESSION_COOKIE_HTTPONLY is True
        assert reloaded.Config.SESSION_COOKIE_SAMESITE == "Lax"
        assert reloaded.Config.PREFERRED_URL_SCHEME == "https"
    finally:
        if original_value is None:
            os.environ.pop("SESSION_COOKIE_SECURE", None)
        else:
            os.environ["SESSION_COOKIE_SECURE"] = original_value
        importlib.reload(settings)
