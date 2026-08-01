from __future__ import annotations

import pytest

from app import create_app, db
from app.maintenance import BOARD_CATALOG, initialize_database
from app.models import Board, Post, User, rank_ladder
from settings import Config


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


def test_staff_identity_does_not_replace_contribution_rank(app):
    with app.app_context():
        owner = User(
            username="owner",
            email="owner@example.test",
            verified=True,
            is_site_owner=True,
        )
        admin = User(
            username="admin",
            email="admin@example.test",
            verified=True,
            is_admin=True,
        )
        db.session.add_all([owner, admin])
        db.session.commit()

        assert owner.role_label == "站长"
        assert admin.role_label == "管理员"
        assert owner.score == 0
        assert admin.score == 0
        assert owner.get_rank_title() == "初来乍到"
        assert admin.get_rank_title() == "初来乍到"
        assert [entry["name"] for entry in rank_ladder()] == [
            "初来乍到",
            "雏鸟",
            "候鸟",
            "留鸟",
            "猛禽",
            "幻想之鸟",
        ]


def test_profile_labels_role_and_contribution_as_separate_concepts(app):
    with app.app_context():
        admin = User(
            username="staff-bird",
            email="staff@example.test",
            verified=True,
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()

    html = app.test_client().get("/user/staff-bird").get_data(as_text=True)
    assert "站务身份" in html
    assert "管理员" in html
    assert "贡献等级" in html
    assert "初来乍到" in html
    assert "管理员特权" not in html
    assert "站长身份" not in html


def test_public_copy_treats_birding_records_as_real_observations(app):
    client = app.test_client()
    home = client.get("/").get_data(as_text=True)
    birding = client.get("/birding/birds").get_data(as_text=True)

    assert "真实的鸟类观察记录" in home
    assert "真实或虚构" not in home
    assert "现实中的群鸟" in birding
    assert "现实与东方" not in birding

    with app.app_context():
        bird_board = Board.query.filter_by(name="观鸟记录").one()
        assert bird_board.description == "分享现实鸟类的观察记录"
        assert BOARD_CATALOG[1][2] == "分享现实鸟类的观察记录"


def test_reality_bird_index_and_achievements_exclude_other_boards(app):
    with app.app_context():
        user = User(
            username="mixed-poster",
            email="mixed@example.test",
            verified=True,
        )
        birding_board = Board.query.filter_by(name="观鸟记录").one()
        lore_board = Board.query.filter_by(name="东方鸟类考据").one()
        db.session.add(user)
        db.session.flush()
        real = Post(
            title="现实记录",
            content="实际观察",
            user_id=user.id,
            board_id=birding_board.id,
            bird_name="朱鹭",
            location="陕西省某保护区",
        )
        fantasy = Post(
            title="东方角色考据",
            content="角色与鸟类原型",
            user_id=user.id,
            board_id=lore_board.id,
            bird_name="射命丸文（鸦天狗）",
            location="妖怪之山",
        )
        db.session.add_all([real, fantasy])
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    index_html = client.get("/birding/birds").get_data(as_text=True)
    fantasy_detail = client.get(
        "/birding/bird/射命丸文（鸦天狗）"
    ).get_data(as_text=True)

    assert "朱鹭" in index_html
    assert "射命丸文" not in index_html
    assert "东方角色考据" not in fantasy_detail
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.bird_record_count == 1
        achievement_names = {item["name"] for item in user.get_achievements()}
        assert "初次目击" in achievement_names


def test_birding_post_requires_real_bird_name_and_location_on_server(app):
    with app.app_context():
        user = User(
            username="observer",
            email="observer@example.test",
            verified=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        board_id = Board.query.filter_by(name="观鸟记录").one().id
        general_board_id = Board.query.filter_by(name="综合讨论").one().id

    client = app.test_client()
    _login(client, user_id)
    general_form = client.get(
        f"/forum/new_post/{general_board_id}"
    ).get_data(as_text=True)
    assert "观鸟信息（可选）" not in general_form
    assert "幻想乡妖怪之山" not in general_form

    form_html = client.get(f"/forum/new_post/{board_id}").get_data(as_text=True)
    assert 'name="bird_name"' in form_html and 'name="bird_name" required' in form_html
    assert 'name="location" required' in form_html
    assert "仅填写实际观察到的现实鸟类" in form_html
    assert "幻想乡妖怪之山山顶" not in form_html

    rejected = client.post(
        f"/forum/new_post/{board_id}",
        data={"title": "缺少记录字段", "content": "正文", "bird_name": "", "location": ""},
    )
    assert rejected.status_code == 400
    assert "观鸟记录必须填写鸟种名称和观察地点" in rejected.get_data(as_text=True)
    with app.app_context():
        assert Post.query.filter_by(title="缺少记录字段").count() == 0

    accepted = client.post(
        f"/forum/new_post/{board_id}",
        data={
            "title": "完整记录",
            "content": "正文",
            "bird_name": " 朱鹭 ",
            "location": " 陕西省某保护区 ",
        },
    )
    assert accepted.status_code == 302
    with app.app_context():
        post = Post.query.filter_by(title="完整记录").one()
        post_id = post.id
        assert post.bird_name == "朱鹭"
        assert post.location == "陕西省某保护区"

    rejected_author_edit = client.post(
        f"/forum/edit_post/{post_id}",
        data={"title": "试图清空", "content": "正文", "bird_name": "", "location": ""},
    )
    assert rejected_author_edit.status_code == 400
    with app.app_context():
        post = db.session.get(Post, post_id)
        assert (post.title, post.bird_name, post.location) == (
            "完整记录",
            "朱鹭",
            "陕西省某保护区",
        )
        user = db.session.get(User, user_id)
        user.is_admin = True
        db.session.commit()

    rejected_staff_edit = client.post(
        f"/admin/posts/edit/{post_id}",
        data={"title": "后台试图清空", "content": "正文", "bird_name": "", "location": ""},
    )
    assert rejected_staff_edit.status_code == 400
    with app.app_context():
        post = db.session.get(Post, post_id)
        assert (post.title, post.bird_name, post.location) == (
            "完整记录",
            "朱鹭",
            "陕西省某保护区",
        )


def test_search_labels_staff_identity_and_contribution_separately(app):
    with app.app_context():
        admin = User(
            username="search-staff",
            email="search@example.test",
            verified=True,
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()

    html = app.test_client().get("/forum/search?q=search-staff").get_data(as_text=True)
    assert "站务身份：管理员" in html
    assert "贡献等级：初来乍到" in html


def test_verification_email_copy_separates_real_birding_from_touhou_creation(app, monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "app.utils._send_html_email",
        lambda recipient, subject, html: sent.update(
            recipient=recipient, subject=subject, html=html
        ) or True,
    )
    from app.utils import send_verification_email

    with app.test_request_context("/", base_url="https://example.test"):
        assert send_verification_email("bird@example.test", "bird", "token") is True

    assert "记录现实鸟类、讨论东方创作" in sent["html"]
    assert "观察幻想乡的鸟类" not in sent["html"]


def test_rules_page_is_public_structured_and_linked_from_navigation(app):
    client = app.test_client()
    response = client.get("/rules")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "论坛规则" in html
    assert 'aria-label="规则目录"' in html
    assert "总则" in html
    assert "发帖规范" in html
    assert "交流礼仪" in html
    assert "站务权限与责任" in html
    assert "违规处理" in html
    assert "用户权利" in html
    assert "板块专属规则" in html
    assert "轻微违规" in html and "警告提醒" in html
    assert "摄影交流" in html
    assert "东方二次同人" in html
    assert "记录须基于实际观察" in html
    assert "虚构观鸟记录" not in html
    assert "占位符" not in html

    home = client.get("/").get_data(as_text=True)
    assert 'href="/rules"' in home
    assert "论坛规则" in home
