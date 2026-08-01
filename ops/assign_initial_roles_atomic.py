#!/usr/bin/env python3
"""Atomically assign the initial owner/admin pair in an offline SQLite DB."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

OFFLINE_MARKER_MAX_AGE_SECONDS = 300
MANAGED_SYSTEMD_UNIT = "touhou.service"
MANAGED_RUNTIME_MASK = Path("/run/systemd/system/touhou.service")
MANAGED_APP_ADDRESS = ("127.0.0.1", 8001)
DatabaseIdentity = tuple[int, int]


def _path_identity(path: Path) -> DatabaseIdentity:
    file_stat = path.resolve(strict=True).stat()
    return file_stat.st_dev, file_stat.st_ino


def write_offline_marker(database: Path, marker: Path) -> None:
    """Bind a short-lived 0600 marker to the exact database inode."""

    database = database.resolve(strict=True)
    device, inode = _path_identity(database)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "database": str(database),
                "device": device,
                "inode": inode,
                "created_at_ns": time.time_ns(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    marker.chmod(0o600)


def _assert_offline_marker(database: Path, marker: Path) -> DatabaseIdentity:
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeError("fresh offline marker is required")
    marker_stat = marker.stat()
    if stat.S_IMODE(marker_stat.st_mode) != 0o600:
        raise RuntimeError("offline marker must have mode 0600")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("offline marker is invalid") from error

    resolved_database = database.resolve(strict=True)
    device, inode = _path_identity(resolved_database)
    expected_payload = {
        "database": str(resolved_database),
        "device": device,
        "inode": inode,
    }
    if any(payload.get(key) != value for key, value in expected_payload.items()):
        raise RuntimeError("offline marker does not match the database inode")
    created_at_ns = payload.get("created_at_ns")
    if not isinstance(created_at_ns, int):
        raise RuntimeError("offline marker timestamp is invalid")
    age_ns = time.time_ns() - created_at_ns
    max_age_ns = OFFLINE_MARKER_MAX_AGE_SECONDS * 1_000_000_000
    if age_ns < 0 or age_ns > max_age_ns:
        raise RuntimeError("offline marker is stale")
    return device, inode


def _app_port_is_closed() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(MANAGED_APP_ADDRESS) != 0


def _assert_managed_service_runtime_masked() -> None:
    if (
        not MANAGED_RUNTIME_MASK.is_symlink()
        or os.readlink(MANAGED_RUNTIME_MASK) != "/dev/null"
    ):
        raise RuntimeError(
            f"systemd unit {MANAGED_SYSTEMD_UNIT} has no runtime mask"
        )
    result = subprocess.run(
        [
            "systemctl",
            "show",
            MANAGED_SYSTEMD_UNIT,
            "--property=LoadState",
            "--property=ActiveState",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot verify systemd unit {MANAGED_SYSTEMD_UNIT}")
    properties = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            properties[name] = value
    if properties.get("LoadState") not in {"loaded", "masked"}:
        raise RuntimeError(
            f"systemd unit {MANAGED_SYSTEMD_UNIT} has unexpected load state"
        )
    if properties.get("ActiveState") != "inactive":
        raise RuntimeError(
            f"systemd unit {MANAGED_SYSTEMD_UNIT} is still "
            f"{properties.get('ActiveState', 'unknown')}"
        )
    if not _app_port_is_closed():
        raise RuntimeError(
            f"application port {MANAGED_APP_ADDRESS[1]} is still accepting connections"
        )


def _open_inode_guard(database: Path, expected: DatabaseIdentity) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(database, flags)
    descriptor_stat = os.fstat(descriptor)
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) != expected:
        os.close(descriptor)
        raise RuntimeError("database inode changed before lock acquisition")
    return descriptor


def _assert_path_and_guard_identity(
    database: Path,
    descriptor: int,
    expected: DatabaseIdentity,
) -> None:
    if _path_identity(database) != expected:
        raise RuntimeError("database path no longer points to the marked inode")
    descriptor_stat = os.fstat(descriptor)
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) != expected:
        raise RuntimeError("database inode guard no longer matches the marker")


def _assert_connection_identity(
    connection: sqlite3.Connection,
    expected: DatabaseIdentity,
) -> None:
    main_rows = [
        row for row in connection.execute("PRAGMA database_list") if row[1] == "main"
    ]
    if len(main_rows) != 1 or not main_rows[0][2]:
        raise RuntimeError("cannot identify SQLite main database")
    if _path_identity(Path(main_rows[0][2])) != expected:
        raise RuntimeError("SQLite connection is not using the marked database inode")


def _assert_unique_owner_index(connection: sqlite3.Connection) -> None:
    index_rows = {
        row[1]: row for row in connection.execute('PRAGMA index_list("user")')
    }
    index = index_rows.get("uq_user_single_site_owner")
    if index is None or int(index[2]) != 1 or int(index[4]) != 1:
        raise RuntimeError("unique station-owner partial index is missing")
    indexed_columns = [
        row[2]
        for row in connection.execute(
            'PRAGMA index_info("uq_user_single_site_owner")'
        )
    ]
    if indexed_columns != ["is_site_owner"]:
        raise RuntimeError("station-owner index covers the wrong columns")
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND tbl_name = 'user' AND name = 'uq_user_single_site_owner'"
    ).fetchone()
    sql = sql_row[0] if sql_row else ""
    if not re.search(r'\bWHERE\s+"?is_site_owner"?\s*=\s*1\s*$', sql, re.I):
        raise RuntimeError("station-owner index has the wrong predicate")


def _role_snapshot(connection: sqlite3.Connection) -> dict[int, tuple[str, int, int, int, int]]:
    rows = connection.execute(
        'SELECT id, username, verified, is_muted, is_admin, is_site_owner '
        'FROM "user" ORDER BY id'
    ).fetchall()
    return {
        int(row[0]): (
            str(row[1]),
            int(row[2]),
            int(row[3]),
            int(row[4]),
            int(row[5]),
        )
        for row in rows
    }


def _assert_target(
    snapshot: dict[int, tuple[str, int, int, int, int]],
    user_id: int,
    expected_username: str,
    role_name: str,
) -> tuple[str, int, int, int, int]:
    row = snapshot.get(user_id)
    if row is None:
        raise RuntimeError(f"{role_name} target id does not exist")
    if row[0] != expected_username:
        raise RuntimeError(f"{role_name} target id/username mismatch")
    if row[1] != 1:
        raise RuntimeError(f"{role_name} target is not verified")
    if row[2] != 0:
        raise RuntimeError(f"{role_name} target is muted")
    return row


def assign_roles(
    database: Path,
    *,
    offline_marker: Path,
    offline_guard: Callable[[], None],
    owner_id: int,
    owner_username: str,
    admin_id: int,
    admin_username: str,
    preserved_admins: tuple[tuple[int, str], ...] = (),
) -> dict[str, object]:
    if owner_id == admin_id:
        raise RuntimeError("owner and administrator targets must be different users")

    offline_guard()
    expected_identity = _assert_offline_marker(database, offline_marker)
    inode_descriptor = _open_inode_guard(database, expected_identity)
    connection: sqlite3.Connection | None = None
    try:
        _assert_path_and_guard_identity(
            database, inode_descriptor, expected_identity
        )
        connection = sqlite3.connect(database, isolation_level=None, timeout=15)
        _assert_connection_identity(connection, expected_identity)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed before role update")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("database foreign-key check failed before role update")
        _assert_unique_owner_index(connection)

        offline_guard()
        _assert_path_and_guard_identity(
            database, inode_descriptor, expected_identity
        )
        _assert_connection_identity(connection, expected_identity)
        connection.execute("BEGIN IMMEDIATE")
        before = _role_snapshot(connection)
        _assert_target(before, owner_id, owner_username, "owner")
        _assert_target(before, admin_id, admin_username, "administrator")
        for preserved_id, preserved_username in preserved_admins:
            row = _assert_target(
                before, preserved_id, preserved_username, "preserved administrator"
            )
            if row[3] != 1 or row[4] != 0:
                raise RuntimeError("preserved administrator role is not intact")

        existing_owner_ids = [
            user_id for user_id, row in before.items() if row[4] == 1
        ]
        if existing_owner_ids not in ([], [owner_id]):
            raise RuntimeError("site already has a different station owner")

        connection.execute(
            'UPDATE "user" SET is_site_owner = 1, is_admin = 0 WHERE id = ?',
            (owner_id,),
        )
        connection.execute(
            'UPDATE "user" SET is_site_owner = 0, is_admin = 1 WHERE id = ?',
            (admin_id,),
        )
        after = _role_snapshot(connection)

        if len(after) != len(before) or set(after) != set(before):
            raise RuntimeError("user set changed during role transaction")
        for user_id, before_row in before.items():
            if user_id not in {owner_id, admin_id} and after[user_id] != before_row:
                raise RuntimeError("an unrelated user changed during role transaction")
        if after[owner_id] != (owner_username, 1, 0, 0, 1):
            raise RuntimeError("owner postcondition failed")
        if after[admin_id] != (admin_username, 1, 0, 1, 0):
            raise RuntimeError("administrator postcondition failed")
        for preserved_id, preserved_username in preserved_admins:
            if after[preserved_id] != (
                preserved_username,
                1,
                0,
                1,
                0,
            ):
                raise RuntimeError("preserved administrator postcondition failed")
        if sum(row[4] for row in after.values()) != 1:
            raise RuntimeError("station-owner uniqueness postcondition failed")

        offline_guard()
        _assert_path_and_guard_identity(
            database, inode_descriptor, expected_identity
        )
        _assert_connection_identity(connection, expected_identity)
        connection.commit()
        connection.close()
        connection = None

        offline_guard()
        _assert_path_and_guard_identity(
            database, inode_descriptor, expected_identity
        )
        verify = sqlite3.connect(database, timeout=15)
        try:
            _assert_connection_identity(verify, expected_identity)
            persisted = _role_snapshot(verify)
            if persisted[owner_id] != (owner_username, 1, 0, 0, 1):
                raise RuntimeError("persisted owner verification failed")
            if persisted[admin_id] != (admin_username, 1, 0, 1, 0):
                raise RuntimeError("persisted administrator verification failed")
            for preserved_id, preserved_username in preserved_admins:
                if persisted[preserved_id] != (
                    preserved_username,
                    1,
                    0,
                    1,
                    0,
                ):
                    raise RuntimeError(
                        "persisted preserved administrator verification failed"
                    )
            if verify.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("database integrity check failed after role update")
            if verify.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("database foreign-key check failed after role update")
        finally:
            verify.close()
    except Exception:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
        raise
    finally:
        os.close(inode_descriptor)

    return {
        "owner": {"id": owner_id, "username": owner_username},
        "administrator": {"id": admin_id, "username": admin_username},
        "preserved_administrators": [
            {"id": user_id, "username": username}
            for user_id, username in preserved_admins
        ],
        "owner_count": 1,
        "integrity": "ok",
        "foreign_key_check": "ok",
    }


def _parse_preserved_admin(value: str) -> tuple[int, str]:
    user_id, separator, username = value.partition(":")
    if not separator or not user_id.isdigit() or not username:
        raise argparse.ArgumentTypeError("expected ID:USERNAME")
    return int(user_id), username


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Assign the initial owner/admin pair while touhou.service is "
            "runtime-masked and inactive. Refuses missing/stale markers."
        )
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--offline-marker", required=True, type=Path)
    parser.add_argument("--owner-id", required=True, type=int)
    parser.add_argument("--owner-username", required=True)
    parser.add_argument("--admin-id", required=True, type=int)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument(
        "--preserve-admin",
        action="append",
        default=[],
        type=_parse_preserved_admin,
        metavar="ID:USERNAME",
    )
    args = parser.parse_args()
    result = assign_roles(
        args.database,
        offline_marker=args.offline_marker,
        offline_guard=_assert_managed_service_runtime_masked,
        owner_id=args.owner_id,
        owner_username=args.owner_username,
        admin_id=args.admin_id,
        admin_username=args.admin_username,
        preserved_admins=tuple(args.preserve_admin),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
