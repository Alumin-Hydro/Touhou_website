"""Explicit, idempotent database maintenance for deploys and local setup.

Gunicorn imports ``run:app`` without executing ``run.py``'s ``__main__`` block,
so schema/catalog updates must be invoked once by the deployment transaction
before new workers start. No initialization or migration automatically promotes a
user; the separate explicit appointment function requires an immutable user ID.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Board, User


BOARD_CATALOG = (
    (10, "综合讨论", "关于东方 Project、观鸟与幻想乡的自由讨论"),
    (20, "观鸟记录", "分享真实或幻想乡鸟类的观察记录"),
    (30, "东方鸟类考据", "考据东方 Project 中的鸟类、妖怪与民俗原型"),
    (40, "绘画与创作", "分享插画、文字、手作等原创作品"),
    (50, "摄影交流", "分享鸟类、自然与东方主题摄影作品和拍摄心得"),
    (60, "东方二次同人", "交流东方 Project 二次创作、同人作品与活动"),
)

_CONTENT_TABLES = {"post", "comment", "message"}
_EXPECTED_CONTENT_FOREIGN_KEYS = {
    "post": {
        ("user_id",): "RESTRICT",
        ("board_id",): "RESTRICT",
    },
    "comment": {
        ("user_id",): "RESTRICT",
        ("post_id",): "CASCADE",
    },
    "message": {
        ("sender_id",): "RESTRICT",
        ("receiver_id",): "RESTRICT",
    },
}
_REQUIRED_CONTENT_COLUMNS = {
    "post": {"user_id", "board_id"},
    "comment": {"user_id", "post_id"},
    "message": {"sender_id", "receiver_id"},
}
_REFERENCE_AUDIT_QUERIES = {
    "post.user_id": (
        'SELECT COUNT(*) FROM post AS child LEFT JOIN "user" AS parent '
        "ON parent.id = child.user_id "
        "WHERE child.user_id IS NULL OR parent.id IS NULL"
    ),
    "post.board_id": (
        "SELECT COUNT(*) FROM post AS child LEFT JOIN board AS parent "
        "ON parent.id = child.board_id "
        "WHERE child.board_id IS NULL OR parent.id IS NULL"
    ),
    "comment.user_id": (
        'SELECT COUNT(*) FROM comment AS child LEFT JOIN "user" AS parent '
        "ON parent.id = child.user_id "
        "WHERE child.user_id IS NULL OR parent.id IS NULL"
    ),
    "comment.post_id": (
        "SELECT COUNT(*) FROM comment AS child LEFT JOIN post AS parent "
        "ON parent.id = child.post_id "
        "WHERE child.post_id IS NULL OR parent.id IS NULL"
    ),
    "message.sender_id": (
        'SELECT COUNT(*) FROM message AS child LEFT JOIN "user" AS parent '
        "ON parent.id = child.sender_id "
        "WHERE child.sender_id IS NULL OR parent.id IS NULL"
    ),
    "message.receiver_id": (
        'SELECT COUNT(*) FROM message AS child LEFT JOIN "user" AS parent '
        "ON parent.id = child.receiver_id "
        "WHERE child.receiver_id IS NULL OR parent.id IS NULL"
    ),
}


def site_owner_column_definition(dialect: str) -> str:
    """Return a boolean column definition valid for the selected database."""

    if dialect == "sqlite":
        return "BOOLEAN NOT NULL DEFAULT 0"
    if dialect == "postgresql":
        return "BOOLEAN NOT NULL DEFAULT false"
    raise RuntimeError(f"unsupported migration dialect: {dialect}")


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _content_tables_present(tables: set[str]) -> bool:
    present = tables & _CONTENT_TABLES
    if present and present != _CONTENT_TABLES:
        missing = ", ".join(sorted(_CONTENT_TABLES - present))
        raise RuntimeError(f"content tables are incomplete; missing: {missing}")
    return present == _CONTENT_TABLES


def _audit_content_references(connection) -> None:
    issues = {
        name: int(connection.execute(text(query)).scalar_one())
        for name, query in _REFERENCE_AUDIT_QUERIES.items()
    }
    issues = {name: count for name, count in issues.items() if count}
    if issues:
        detail = ", ".join(f"{name}={count}" for name, count in issues.items())
        raise RuntimeError(f"NULL or orphaned content references: {detail}")


def _content_constraints_current(inspector) -> bool:
    for table, required_columns in _REQUIRED_CONTENT_COLUMNS.items():
        columns = {
            column["name"]: column for column in inspector.get_columns(table)
        }
        if any(columns[name]["nullable"] for name in required_columns):
            return False

        actual_foreign_keys = {
            tuple(foreign_key["constrained_columns"]): (
                foreign_key.get("options", {}).get("ondelete") or "NO ACTION"
            ).upper()
            for foreign_key in inspector.get_foreign_keys(table)
        }
        if actual_foreign_keys != _EXPECTED_CONTENT_FOREIGN_KEYS[table]:
            return False
    return True


def _rebuild_sqlite_content_tables() -> None:
    """Atomically rebuild legacy SQLite content tables with strict ownership FKs."""

    db.session.remove()
    raw_connection = db.engine.raw_connection()
    cursor = raw_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=OFF")
        if cursor.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise RuntimeError("could not disable SQLite foreign keys for table rebuild")
        cursor.execute("BEGIN IMMEDIATE")

        for table in ("comment__strict", "message__strict", "post__strict"):
            cursor.execute(f"DROP TABLE IF EXISTS {table}")

        cursor.execute(
            """
            CREATE TABLE post__strict (
                id INTEGER NOT NULL PRIMARY KEY,
                title VARCHAR(128) NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME,
                user_id INTEGER NOT NULL,
                board_id INTEGER NOT NULL,
                bird_name VARCHAR(64),
                location VARCHAR(128),
                photo_url VARCHAR(256),
                is_pinned BOOLEAN,
                FOREIGN KEY(user_id) REFERENCES "user"(id) ON DELETE RESTRICT,
                FOREIGN KEY(board_id) REFERENCES board(id) ON DELETE RESTRICT
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO post__strict (
                id, title, content, created_at, updated_at, user_id, board_id,
                bird_name, location, photo_url, is_pinned
            )
            SELECT
                id, title, content, created_at, updated_at, user_id, board_id,
                bird_name, location, photo_url, is_pinned
            FROM post
            """
        )
        cursor.execute(
            """
            CREATE TABLE comment__strict (
                id INTEGER NOT NULL PRIMARY KEY,
                content TEXT NOT NULL,
                created_at DATETIME,
                user_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES "user"(id) ON DELETE RESTRICT,
                FOREIGN KEY(post_id) REFERENCES post(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO comment__strict (id, content, created_at, user_id, post_id)
            SELECT id, content, created_at, user_id, post_id FROM comment
            """
        )
        cursor.execute(
            """
            CREATE TABLE message__strict (
                id INTEGER NOT NULL PRIMARY KEY,
                content TEXT NOT NULL,
                created_at DATETIME,
                is_read BOOLEAN,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                FOREIGN KEY(sender_id) REFERENCES "user"(id) ON DELETE RESTRICT,
                FOREIGN KEY(receiver_id) REFERENCES "user"(id) ON DELETE RESTRICT
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO message__strict (
                id, content, created_at, is_read, sender_id, receiver_id
            )
            SELECT id, content, created_at, is_read, sender_id, receiver_id
            FROM message
            """
        )

        cursor.execute("DROP TABLE comment")
        cursor.execute("DROP TABLE message")
        cursor.execute("DROP TABLE post")
        cursor.execute("ALTER TABLE post__strict RENAME TO post")
        cursor.execute("ALTER TABLE comment__strict RENAME TO comment")
        cursor.execute("ALTER TABLE message__strict RENAME TO message")

        violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key check failed after rebuild: {violations}")
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
            raw_connection.close()


def _migrate_postgresql_content_constraints(connection, inspector) -> None:
    """Normalize legacy PostgreSQL content ownership constraints in one transaction."""

    if _content_constraints_current(inspector):
        return

    references = {
        ("post", "user_id"): ("user", "RESTRICT"),
        ("post", "board_id"): ("board", "RESTRICT"),
        ("comment", "user_id"): ("user", "RESTRICT"),
        ("comment", "post_id"): ("post", "CASCADE"),
        ("message", "sender_id"): ("user", "RESTRICT"),
        ("message", "receiver_id"): ("user", "RESTRICT"),
    }
    existing = {
        (table, tuple(foreign_key["constrained_columns"])): foreign_key.get("name")
        for table in _CONTENT_TABLES
        for foreign_key in inspector.get_foreign_keys(table)
    }

    for (table, column), (parent, on_delete) in references.items():
        quoted_table = _quote_identifier(table)
        quoted_column = _quote_identifier(column)
        connection.execute(
            text(
                f"ALTER TABLE {quoted_table} ALTER COLUMN {quoted_column} SET NOT NULL"
            )
        )
        old_name = existing.get((table, (column,)))
        if old_name:
            connection.execute(
                text(
                    f"ALTER TABLE {quoted_table} DROP CONSTRAINT "
                    f"{_quote_identifier(old_name)}"
                )
            )
        constraint_name = _quote_identifier(f"fk_{table}_{column}_{on_delete.lower()}")
        connection.execute(
            text(
                f"ALTER TABLE {quoted_table} ADD CONSTRAINT {constraint_name} "
                f"FOREIGN KEY ({quoted_column}) REFERENCES {_quote_identifier(parent)} (id) "
                f"ON DELETE {on_delete}"
            )
        )


def migrate_schema() -> None:
    """Bring an existing SQLite/PostgreSQL database to the current schema.

    The caller must first make and verify a database backup, stop new workers,
    and keep them stopped until this transaction and its verification succeed.
    """

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    if "user" not in tables or "board" not in tables:
        raise RuntimeError("user/board tables are missing; run db.create_all() first")

    dialect = db.engine.dialect.name
    site_owner_column_definition(dialect)  # fail before changing an unknown dialect
    has_content_tables = _content_tables_present(tables)
    if has_content_tables:
        with db.engine.connect() as connection:
            _audit_content_references(connection)

    user_columns = {column["name"] for column in inspector.get_columns("user")}
    board_columns = {column["name"] for column in inspector.get_columns("board")}
    new_user_columns = {
        "bio": "TEXT",
        "avatar_url": "VARCHAR(256)",
        "custom_title": "VARCHAR(64)",
        "search_per_page": "INTEGER DEFAULT 20",
        "search_scope": "VARCHAR(20) DEFAULT 'all'",
        "search_type": "VARCHAR(20) DEFAULT 'all'",
        "is_site_owner": site_owner_column_definition(dialect),
    }

    with db.engine.begin() as connection:
        for column, column_type in new_user_columns.items():
            if column not in user_columns:
                connection.execute(
                    text(f'ALTER TABLE "user" ADD COLUMN "{column}" {column_type}')
                )
        if "sort_order" not in board_columns:
            connection.execute(
                text(
                    "ALTER TABLE board ADD COLUMN "
                    "sort_order INTEGER NOT NULL DEFAULT 1000"
                )
            )

        if dialect == "sqlite":
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_user_single_site_owner ON \"user\" (is_site_owner) "
                    "WHERE is_site_owner = 1"
                )
            )
        elif dialect == "postgresql":
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_user_single_site_owner ON \"user\" (is_site_owner) "
                    "WHERE is_site_owner = true"
                )
            )
            if has_content_tables:
                _migrate_postgresql_content_constraints(connection, inspector)

    if dialect == "sqlite" and has_content_tables:
        current_inspector = inspect(db.engine)
        if not _content_constraints_current(current_inspector):
            _rebuild_sqlite_content_tables()
        final_inspector = inspect(db.engine)
        if not _content_constraints_current(final_inspector):
            raise RuntimeError("SQLite content constraints do not match the model")
        with db.engine.connect() as connection:
            violations = connection.execute(text("PRAGMA foreign_key_check")).fetchall()
            if violations:
                raise RuntimeError(f"SQLite foreign key check failed: {violations}")


def appoint_initial_site_owner(user_id: int) -> User:
    """Explicitly appoint the first station owner by immutable user ID.

    The operation is idempotent for the same user and refuses replacement.
    Transferring ownership is intentionally a separate, manually reviewed task.
    """

    target = db.session.get(User, user_id)
    if target is None:
        raise ValueError(f"user id {user_id} does not exist")
    if not target.verified:
        raise ValueError("station owner must have a verified account")
    if target.is_muted:
        raise ValueError("station owner must not be muted")

    current_owner = User.query.filter_by(is_site_owner=True).first()
    if current_owner is not None:
        if current_owner.id == target.id:
            return current_owner
        raise RuntimeError("site already has a different station owner")

    target.is_site_owner = True
    target.is_admin = False
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise RuntimeError("site already has a different station owner") from error
    return target


def sync_board_catalog() -> None:
    """Add missing required boards and normalize their descriptions/order."""

    for sort_order, name, description in BOARD_CATALOG:
        board = Board.query.filter_by(name=name).first()
        if board is None:
            board = Board(name=name)
            db.session.add(board)
        board.description = description
        board.sort_order = sort_order
    db.session.commit()


def initialize_database() -> None:
    """Create a new schema or upgrade an existing one, then sync boards."""

    db.create_all()
    migrate_schema()
    sync_board_catalog()
