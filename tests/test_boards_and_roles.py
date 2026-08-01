from __future__ import annotations

import sqlite3
from html.parser import HTMLParser

import pytest
from sqlalchemy import inspect

from app import create_app, db
from app.models import Board, Comment, Post, User
from settings import Config


REQUIRED_BOARD_NAMES = [
    "综合讨论",
    "观鸟记录",
    "东方鸟类考据",
    "绘画与创作",
    "摄影交流",
    "东方二次同人",
]


class _InlineHandlerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.handlers: list[str] = []

    def handle_starttag(self, tag, attrs):
        del tag
        self.handlers.extend(
            value
            for name, value in attrs
            if name.startswith("on") and value is not None
        )


def _inline_handlers(html: str) -> list[str]:
    parser = _InlineHandlerParser()
    parser.feed(html)
    return parser.handlers


@pytest.fixture()
def app(tmp_path):
    class TestConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        SECRET_KEY = "roles-and-boards-test"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        SESSION_COOKIE_SECURE = False

    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _add_users(app):
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
        member = User(
            username="member",
            email="member@example.test",
            verified=True,
        )
        peer_admin = User(
            username="peer-admin",
            email="peer-admin@example.test",
            verified=True,
            is_admin=True,
        )
        db.session.add_all([owner, admin, member, peer_admin])
        db.session.commit()
        return owner.id, admin.id, member.id, peer_admin.id


def test_role_and_board_schema_exposes_required_capabilities():
    assert hasattr(User, "is_site_owner")
    assert hasattr(User, "is_staff")
    assert hasattr(User, "can_manage_content")
    assert hasattr(User, "role_label")
    assert hasattr(Board, "sort_order")


def test_catalog_sync_adds_missing_boards_idempotently_to_nonempty_database(app):
    from app.maintenance import sync_board_catalog

    with app.app_context():
        for index, name in enumerate(REQUIRED_BOARD_NAMES[:4], start=1):
            db.session.add(Board(name=name, description="旧描述", sort_order=index))
        db.session.commit()

        sync_board_catalog()
        sync_board_catalog()

        boards = Board.query.order_by(Board.sort_order, Board.id).all()
        assert [board.name for board in boards] == REQUIRED_BOARD_NAMES
        assert Board.query.filter(Board.name.in_(REQUIRED_BOARD_NAMES)).count() == 6
        assert Board.query.filter_by(name="摄影交流").one().description != "旧描述"
        assert Board.query.filter_by(name="东方二次同人").one().description != "旧描述"


def test_navigation_lists_new_boards_in_catalog_order_and_has_mobile_toggle(app):
    from app.maintenance import sync_board_catalog

    with app.app_context():
        sync_board_catalog()

    html = app.test_client().get("/").get_data(as_text=True)
    positions = [html.index(name) for name in REQUIRED_BOARD_NAMES]
    assert positions == sorted(positions)
    assert "摄影交流" in html
    assert "东方二次同人" in html
    assert 'class="nav-toggle"' in html
    assert "展开导航" in html


def test_only_site_owner_can_grant_and_revoke_administrator(app):
    owner_id, admin_id, member_id, _ = _add_users(app)

    admin_client = app.test_client()
    _login(admin_client, admin_id)
    denied = admin_client.post(
        f"/admin/users/{member_id}/role",
        data={"role": "admin"},
    )
    assert denied.status_code == 403

    owner_client = app.test_client()
    _login(owner_client, owner_id)
    granted = owner_client.post(
        f"/admin/users/{member_id}/role",
        data={"role": "admin"},
    )
    assert granted.status_code == 302
    with app.app_context():
        assert db.session.get(User, member_id).is_admin is True

    revoked = owner_client.post(
        f"/admin/users/{member_id}/role",
        data={"role": "member"},
    )
    assert revoked.status_code == 302
    with app.app_context():
        target = db.session.get(User, member_id)
        assert target.is_admin is False
        assert target.is_site_owner is False


def test_role_endpoint_rejects_owner_target_and_invalid_role(app):
    owner_id, _, member_id, _ = _add_users(app)
    client = app.test_client()
    _login(client, owner_id)

    assert client.post(
        f"/admin/users/{owner_id}/role",
        data={"role": "member"},
    ).status_code == 403
    assert client.post(
        f"/admin/users/{member_id}/role",
        data={"role": "site_owner"},
    ).status_code == 400

    with app.app_context():
        owner = db.session.get(User, owner_id)
        member = db.session.get(User, member_id)
        assert owner.is_site_owner is True
        assert member.is_site_owner is False
        assert member.is_admin is False


def test_site_owner_cannot_promote_unverified_account(app):
    owner_id, _, member_id, _ = _add_users(app)
    with app.app_context():
        member = db.session.get(User, member_id)
        member.verified = False
        db.session.commit()

    client = app.test_client()
    _login(client, owner_id)
    response = client.post(
        f"/admin/users/{member_id}/role",
        data={"role": "admin"},
    )
    assert response.status_code == 409
    with app.app_context():
        assert db.session.get(User, member_id).is_admin is False


def test_role_endpoint_requires_post_and_csrf(tmp_path):
    class CsrfConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = True
        SECRET_KEY="csrf..."
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'csrf.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        SESSION_COOKIE_SECURE = False

    application = create_app(CsrfConfig)
    with application.app_context():
        db.create_all()
        owner = User(
            username="csrf-owner",
            email="csrf-owner@example.test",
            verified=True,
            is_site_owner=True,
        )
        member = User(
            username="csrf-member",
            email="csrf-member@example.test",
            verified=True,
        )
        db.session.add_all([owner, member])
        db.session.commit()
        owner_id, member_id = owner.id, member.id

    client = application.test_client()
    _login(client, owner_id)
    endpoint = f"/admin/users/{member_id}/role"
    assert client.get(endpoint).status_code == 405
    assert client.post(endpoint, data={"role": "admin"}).status_code == 400
    assert client.post(
        f"/admin/users/toggle_admin/{member_id}", data={"role": "admin"}
    ).status_code == 404
    with application.app_context():
        assert db.session.get(User, member_id).is_admin is False
        db.session.remove()
        db.drop_all()


def test_admin_revocation_applies_to_existing_session_on_next_request(app):
    owner_id, admin_id, _, _ = _add_users(app)
    admin_client = app.test_client()
    owner_client = app.test_client()
    _login(admin_client, admin_id)
    _login(owner_client, owner_id)

    assert admin_client.get("/admin/").status_code == 200
    assert owner_client.post(
        f"/admin/users/{admin_id}/role",
        data={"role": "member"},
    ).status_code == 302
    assert admin_client.get("/admin/").status_code == 403


def test_muted_user_must_be_unmuted_before_staff_promotion(app):
    owner_id, _, member_id, _ = _add_users(app)
    with app.app_context():
        member = db.session.get(User, member_id)
        member.is_muted = True
        db.session.commit()

    client = app.test_client()
    _login(client, owner_id)
    assert client.post(
        f"/admin/users/{member_id}/role", data={"role": "admin"}
    ).status_code == 409
    with app.app_context():
        member = db.session.get(User, member_id)
        assert member.is_admin is False
        assert member.is_muted is True

    assert client.post(f"/admin/users/unmute/{member_id}").status_code == 302
    assert client.post(
        f"/admin/users/{member_id}/role", data={"role": "admin"}
    ).status_code == 302
    with app.app_context():
        member = db.session.get(User, member_id)
        assert member.is_admin is True
        assert member.is_muted is False


def test_staff_cannot_mute_or_delete_owner_or_peer_staff(app):
    owner_id, admin_id, member_id, peer_admin_id = _add_users(app)
    admin_client = app.test_client()
    owner_client = app.test_client()
    _login(admin_client, admin_id)
    _login(owner_client, owner_id)

    assert admin_client.post(
        f"/admin/users/mute/{owner_id}", data={"duration": "1"}
    ).status_code == 403
    assert admin_client.post(
        f"/admin/users/delete/{peer_admin_id}"
    ).status_code == 403
    assert owner_client.post(
        f"/admin/users/delete/{admin_id}"
    ).status_code == 403

    assert admin_client.post(
        f"/admin/users/mute/{member_id}", data={"duration": "1"}
    ).status_code == 302
    with app.app_context():
        assert db.session.get(User, member_id).is_muted is True
        assert db.session.get(User, owner_id).is_muted is False
        assert db.session.get(User, peer_admin_id) is not None


def test_database_restricts_deleting_an_author_with_content(app):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        foreign_keys = db.session.execute(text("PRAGMA foreign_keys")).scalar_one()
        assert foreign_keys == 1
        board = Board(name="综合讨论", sort_order=10)
        author = User(
            username="database-protected-author",
            email="database-protected-author@example.test",
            verified=True,
        )
        db.session.add_all([board, author])
        db.session.flush()
        post = Post(
            title="database restriction",
            content="body",
            user_id=author.id,
            board_id=board.id,
        )
        db.session.add(post)
        db.session.commit()
        author_id, post_id = author.id, post.id

        db.session.delete(author)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(User, author_id) is not None
        assert db.session.get(Post, post_id).user_id == author_id


def test_user_deletion_refuses_orphaning_content_and_cleans_empty_avatar(app, monkeypatch):
    deleted_urls: list[str | None] = []
    monkeypatch.setattr(
        "app.admin.delete_by_url", lambda url: deleted_urls.append(url)
    )
    owner_id, _, _, _ = _add_users(app)
    with app.app_context():
        board = Board(name="综合讨论", sort_order=10)
        author = User(
            username="author-with-content",
            email="author-with-content@example.test",
            verified=True,
        )
        empty = User(
            username="empty-user",
            email="empty-user@example.test",
            verified=True,
            avatar_url="https://images.example.test/avatar.png",
        )
        db.session.add_all([board, author, empty])
        db.session.flush()
        db.session.add(
            Post(
                title="keep author",
                content="body",
                user_id=author.id,
                board_id=board.id,
            )
        )
        db.session.commit()
        author_id, empty_id = author.id, empty.id

    client = app.test_client()
    _login(client, owner_id)
    assert client.post(f"/admin/users/delete/{author_id}").status_code == 302
    with app.app_context():
        assert db.session.get(User, author_id) is not None
        assert Post.query.filter_by(user_id=author_id).count() == 1

    assert client.post(f"/admin/users/delete/{empty_id}").status_code == 302
    with app.app_context():
        assert db.session.get(User, empty_id) is None
    assert deleted_urls == ["https://images.example.test/avatar.png"]


def test_admin_and_owner_can_moderate_content_but_member_cannot(app, monkeypatch):
    monkeypatch.setattr("app.forum.delete_by_url", lambda _url: None)
    owner_id, admin_id, member_id, _ = _add_users(app)
    with app.app_context():
        board = Board(name="综合讨论", sort_order=10)
        author = User(username="author", email="author@example.test", verified=True)
        db.session.add_all([board, author])
        db.session.flush()
        owner_target = Post(
            title="owner target",
            content="body",
            user_id=author.id,
            board_id=board.id,
        )
        admin_target = Post(
            title="admin target",
            content="body",
            user_id=author.id,
            board_id=board.id,
        )
        member_target = Post(
            title="member target",
            content="body",
            user_id=author.id,
            board_id=board.id,
        )
        db.session.add_all([owner_target, admin_target, member_target])
        db.session.flush()
        comment = Comment(content="moderate me", user_id=author.id, post_id=admin_target.id)
        db.session.add(comment)
        db.session.commit()
        owner_target_id = owner_target.id
        admin_target_id = admin_target.id
        member_target_id = member_target.id
        comment_id = comment.id

    member_client = app.test_client()
    _login(member_client, member_id)
    assert member_client.post(f"/forum/delete_post/{member_target_id}").status_code == 403

    owner_client = app.test_client()
    _login(owner_client, owner_id)
    assert owner_client.post(f"/forum/delete_post/{owner_target_id}").status_code == 302

    admin_client = app.test_client()
    _login(admin_client, admin_id)
    assert admin_client.post(f"/forum/delete_comment/{comment_id}").status_code == 302

    with app.app_context():
        assert db.session.get(Post, owner_target_id) is None
        assert db.session.get(Post, member_target_id) is not None
        assert db.session.get(Post, admin_target_id) is not None
        assert db.session.get(Comment, comment_id) is None


def test_management_page_shows_role_controls_only_to_site_owner(app):
    owner_id, admin_id, member_id, _ = _add_users(app)

    owner_client = app.test_client()
    _login(owner_client, owner_id)
    owner_html = owner_client.get("/admin/users").get_data(as_text=True)
    assert "站长" in owner_html
    assert f'action="/admin/users/{member_id}/role"' in owner_html
    assert "任命管理员" in owner_html

    admin_client = app.test_client()
    _login(admin_client, admin_id)
    admin_html = admin_client.get("/admin/users").get_data(as_text=True)
    assert "管理员" in admin_html
    assert f'action="/admin/users/{member_id}/role"' not in admin_html
    assert "仅站长可以任命或撤销管理员" in admin_html


def test_untrusted_names_and_titles_never_enter_inline_javascript(app):
    payload = "x');fetch('/pwn');//"
    with app.app_context():
        owner = User(
            username="owner-xss-test",
            email="owner-xss@example.test",
            verified=True,
            is_site_owner=True,
        )
        attacker = User(
            username=payload,
            email="attacker@example.test",
            verified=True,
        )
        board = Board(name="综合讨论", sort_order=10)
        db.session.add_all([owner, attacker, board])
        db.session.flush()
        post = Post(
            title=payload,
            content="body",
            user_id=attacker.id,
            board_id=board.id,
        )
        db.session.add(post)
        db.session.commit()
        owner_id = owner.id

    client = app.test_client()
    _login(client, owner_id)
    handlers = _inline_handlers(
        client.get("/admin/users").get_data(as_text=True)
        + client.get("/admin/posts").get_data(as_text=True)
    )
    assert handlers
    assert all(payload not in handler for handler in handlers)
    assert all("/pwn" not in handler for handler in handlers)


def test_initial_site_owner_appointment_is_explicit_and_non_replaceable(app):
    import app.maintenance as maintenance

    assert hasattr(maintenance, "appoint_initial_site_owner")
    with app.app_context():
        first = User(
            username="first-owner",
            email="first-owner@example.test",
            verified=True,
            is_admin=True,
            is_muted=True,
        )
        second = User(
            username="second-owner",
            email="second-owner@example.test",
            verified=True,
        )
        db.session.add_all([first, second])
        db.session.commit()
        first_id, second_id = first.id, second.id
        assert User.query.filter_by(is_site_owner=True).count() == 0

        with pytest.raises(ValueError, match="username does not match"):
            maintenance.appoint_initial_site_owner(first_id, "wrong-owner")
        assert User.query.filter_by(is_site_owner=True).count() == 0

        with pytest.raises(ValueError, match="must not be muted"):
            maintenance.appoint_initial_site_owner(first_id, "first-owner")
        first.is_muted = False
        db.session.commit()

        appointed = maintenance.appoint_initial_site_owner(first_id, "first-owner")
        assert appointed.id == first_id
        assert appointed.is_site_owner is True
        assert appointed.is_admin is False

        appointed.is_admin = True
        db.session.commit()
        same = maintenance.appoint_initial_site_owner(first_id, "first-owner")
        assert same.id == first_id
        assert same.is_admin is False
        with pytest.raises(RuntimeError, match="already has a different station owner"):
            maintenance.appoint_initial_site_owner(second_id, "second-owner")


def test_postgresql_boolean_defaults_use_false_not_integer_zero():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    import app.maintenance as maintenance

    ddl = str(CreateTable(User.__table__).compile(dialect=postgresql.dialect()))
    assert "is_site_owner BOOLEAN DEFAULT false NOT NULL" in ddl
    assert hasattr(maintenance, "site_owner_column_definition")
    assert maintenance.site_owner_column_definition("postgresql") == (
        "BOOLEAN NOT NULL DEFAULT false"
    )
    assert maintenance.site_owner_column_definition("sqlite") == (
        "BOOLEAN NOT NULL DEFAULT 0"
    )


def test_old_sqlite_schema_migrates_roles_boards_and_unique_owner(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE "user" (
                id INTEGER PRIMARY KEY,
                username VARCHAR(64) NOT NULL UNIQUE,
                email VARCHAR(120) NOT NULL,
                password_hash VARCHAR(128),
                verified BOOLEAN DEFAULT 0,
                is_admin BOOLEAN DEFAULT 0,
                is_muted BOOLEAN DEFAULT 0,
                mute_expires DATETIME,
                created_at DATETIME
            );
            CREATE TABLE board (
                id INTEGER PRIMARY KEY,
                name VARCHAR(64) NOT NULL UNIQUE,
                description VARCHAR(200)
            );
            CREATE TABLE post (
                id INTEGER PRIMARY KEY,
                title VARCHAR(128) NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME,
                user_id INTEGER REFERENCES "user"(id),
                board_id INTEGER REFERENCES board(id),
                bird_name VARCHAR(64),
                location VARCHAR(128),
                photo_url VARCHAR(256),
                is_pinned BOOLEAN DEFAULT 0
            );
            CREATE TABLE comment (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                created_at DATETIME,
                user_id INTEGER REFERENCES "user"(id),
                post_id INTEGER REFERENCES post(id)
            );
            CREATE TABLE message (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                created_at DATETIME,
                is_read BOOLEAN DEFAULT 0,
                sender_id INTEGER REFERENCES "user"(id),
                receiver_id INTEGER REFERENCES "user"(id)
            );
            INSERT INTO "user" (id, username, email, is_admin)
            VALUES
                (1, 'legacy-admin', 'legacy@example.test', 1),
                (2, 'legacy-member', 'member@example.test', 0);
            INSERT INTO board (id, name, description)
            VALUES (1, '综合讨论', '旧描述');
            INSERT INTO post (id, title, content, user_id, board_id)
            VALUES (1, '旧帖子', '正文', 1, 1);
            INSERT INTO comment (id, content, user_id, post_id)
            VALUES (1, '旧回复', 2, 1);
            INSERT INTO message (id, content, sender_id, receiver_id)
            VALUES (1, '旧私信', 1, 2);
            """
        )

    class LegacyConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        SECRET_KEY = "legacy-migration-test"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}

    application = create_app(LegacyConfig)
    from app.maintenance import migrate_schema, sync_board_catalog

    with application.app_context():
        migrate_schema()
        migrate_schema()
        sync_board_catalog()
        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns("user")
        }
        board_columns = {
            column["name"]
            for column in inspect(db.engine).get_columns("board")
        }
        assert "is_site_owner" in columns
        assert "sort_order" in board_columns

        inspector = inspect(db.engine)
        for table, required_columns in {
            "post": {"user_id", "board_id"},
            "comment": {"user_id", "post_id"},
            "message": {"sender_id", "receiver_id"},
        }.items():
            by_name = {
                column["name"]: column
                for column in inspector.get_columns(table)
            }
            assert all(by_name[name]["nullable"] is False for name in required_columns)

        foreign_keys = {
            table: {
                tuple(foreign_key["constrained_columns"]): foreign_key["options"].get(
                    "ondelete"
                )
                for foreign_key in inspector.get_foreign_keys(table)
            }
            for table in ("post", "comment", "message")
        }
        assert foreign_keys["post"] == {
            ("user_id",): "RESTRICT",
            ("board_id",): "RESTRICT",
        }
        assert foreign_keys["comment"] == {
            ("user_id",): "RESTRICT",
            ("post_id",): "CASCADE",
        }
        assert foreign_keys["message"] == {
            ("sender_id",): "RESTRICT",
            ("receiver_id",): "RESTRICT",
        }

    with sqlite3.connect(database_path) as connection:
        owner_default = connection.execute(
            'SELECT is_site_owner FROM "user" WHERE id = 1'
        ).fetchone()[0]
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM board ORDER BY sort_order, id"
            ).fetchall()
        ]
        assert owner_default == 0
        assert names == REQUIRED_BOARD_NAMES
        assert connection.execute("SELECT user_id, board_id FROM post").fetchall() == [
            (1, 1)
        ]
        assert connection.execute("SELECT user_id, post_id FROM comment").fetchall() == [
            (2, 1)
        ]
        assert connection.execute(
            "SELECT sender_id, receiver_id FROM message"
        ).fetchall() == [(1, 2)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        connection.execute('UPDATE "user" SET is_site_owner = 1 WHERE id = 1')
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                'INSERT INTO "user" '
                '(id, username, email, is_admin, is_site_owner) '
                "VALUES (3, 'second-owner', 'second@example.test', 0, 1)"
            )


def test_legacy_content_integrity_failure_aborts_before_schema_changes(tmp_path):
    database_path = tmp_path / "orphaned.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE "user" (
                id INTEGER PRIMARY KEY,
                username VARCHAR(64) NOT NULL UNIQUE,
                email VARCHAR(120) NOT NULL
            );
            CREATE TABLE board (
                id INTEGER PRIMARY KEY,
                name VARCHAR(64) NOT NULL UNIQUE,
                description VARCHAR(200)
            );
            CREATE TABLE post (
                id INTEGER PRIMARY KEY,
                title VARCHAR(128) NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME,
                user_id INTEGER REFERENCES "user"(id),
                board_id INTEGER REFERENCES board(id),
                bird_name VARCHAR(64),
                location VARCHAR(128),
                photo_url VARCHAR(256),
                is_pinned BOOLEAN
            );
            CREATE TABLE comment (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                created_at DATETIME,
                user_id INTEGER REFERENCES "user"(id),
                post_id INTEGER REFERENCES post(id)
            );
            CREATE TABLE message (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                created_at DATETIME,
                is_read BOOLEAN,
                sender_id INTEGER REFERENCES "user"(id),
                receiver_id INTEGER REFERENCES "user"(id)
            );
            INSERT INTO "user" (id, username, email)
            VALUES (1, 'legacy', 'legacy@example.test');
            INSERT INTO board (id, name) VALUES (1, '综合讨论');
            INSERT INTO post (id, title, content, user_id, board_id)
            VALUES (1, '孤儿', '正文', NULL, 1);
            """
        )

    class OrphanConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        SECRET_KEY="orph..."
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}

    application = create_app(OrphanConfig)
    from app.maintenance import migrate_schema

    with application.app_context():
        with pytest.raises(RuntimeError, match="NULL or orphaned content references"):
            migrate_schema()

    with sqlite3.connect(database_path) as connection:
        user_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("user")')
        }
        assert "is_site_owner" not in user_columns
        assert connection.execute("SELECT user_id FROM post").fetchall() == [(None,)]
