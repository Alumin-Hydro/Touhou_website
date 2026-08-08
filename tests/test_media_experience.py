from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app, db
from app.maintenance import initialize_database
from app.models import Board, Post, User
from settings import Config


ROOT = Path(__file__).resolve().parents[1]
MEDIA_VERSION = "20260801-media-workflow"


@pytest.fixture()
def app(tmp_path):
    class TestConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        SECRET_KEY = "test-secret"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        OSS_ACCESS_KEY_ID = "test"
        OSS_ACCESS_KEY_SECRET = "test"
        OSS_BUCKET = "test"
        OSS_ENDPOINT = "oss-cn-shanghai.aliyuncs.com"
        OSS_ENDPOINT_INTERNAL = OSS_ENDPOINT
        OSS_PUBLIC_BASE = "https://test.oss-cn-shanghai.aliyuncs.com"

    application = create_app(TestConfig)
    with application.app_context():
        initialize_database()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _seed_post(app) -> tuple[int, int, int]:
    with app.app_context():
        user = User(
            username="media-reader",
            email="media@example.test",
            verified=True,
        )
        board = Board.query.filter_by(name="综合讨论").one()
        db.session.add(user)
        db.session.flush()
        post = Post(
            title="可恢复的图片帖子",
            content="测试正文",
            user_id=user.id,
            board_id=board.id,
            photo_url="https://test.oss-cn-shanghai.aliyuncs.com/post/1/test.png",
        )
        db.session.add(post)
        db.session.commit()
        return user.id, board.id, post.id


def test_post_image_opens_accessible_lightbox_without_direct_navigation(app):
    _, _, post_id = _seed_post(app)

    html = app.test_client().get(f"/forum/post/{post_id}").get_data(as_text=True)

    assert 'class="post-image-trigger js-image-lightbox"' in html
    assert 'data-lightbox-src="https://test.oss-cn-shanghai.aliyuncs.com/post/1/test.png"' in html
    assert "点击放大，长按图片可保存" in html
    assert '<a href="https://test.oss-cn-shanghai.aliyuncs.com/post/1/test.png"' not in html
    assert 'id="image-lightbox"' in html
    assert 'aria-modal="true"' in html
    assert f"media-ui.js?v={MEDIA_VERSION}" in html


def test_upload_forms_offer_original_or_crop_before_oss_upload(app):
    user_id, board_id, post_id = _seed_post(app)
    client = app.test_client()
    _login(client, user_id)

    rendered = [
        client.get(f"/forum/new_post/{board_id}").get_data(as_text=True),
        client.get(f"/forum/edit_post/{post_id}").get_data(as_text=True),
        client.get("/settings").get_data(as_text=True),
    ]
    for html in rendered:
        assert "选图后可选择上传原图或裁剪后上传" in html
        assert f"oss-upload.js?v={MEDIA_VERSION}" in html
        assert 'id="image-crop-dialog"' in html
        assert 'data-crop-action="original"' in html
        assert 'data-crop-action="crop"' in html
        assert 'data-crop-action="confirm"' in html
        assert 'data-crop-ratio' in html
        assert 'data-crop-zoom' in html

    script = (ROOT / "app/static/js/oss-upload.js").read_text(encoding="utf-8")
    assert "chooseOriginalOrCrop" in script
    assert "renderCropPreview" in script
    assert "encodeCrop" in script
    assert "showModal()" in script
    assert "canvas.toBlob" in script


def test_last_post_resume_prompt_uses_safe_local_browser_state(app):
    _, _, post_id = _seed_post(app)

    html = app.test_client().get(f"/forum/post/{post_id}").get_data(as_text=True)
    script = (ROOT / "app/static/js/media-ui.js").read_text(encoding="utf-8")

    assert f'data-resume-post-url="/forum/post/{post_id}"' in html
    assert 'data-resume-post-title="可恢复的图片帖子"' in html
    assert 'id="resume-post-prompt"' in html
    assert "继续上次阅读" in html
    assert "gensoumono:last-post:v1" in script
    assert "gensoumono:resume-shown:v1" in script
    assert "localStorage" in script
    assert "sessionStorage" in script
    assert "pagehide" in script
    assert "visibilitychange" in script
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "/^\\/forum\\/post\\/\\d+$/" in script
