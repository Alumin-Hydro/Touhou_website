from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ops import assign_initial_roles_atomic as roles


def _create_role_database(
    path: Path,
    *,
    include_index: bool = True,
    index_column: str = "is_site_owner",
    index_predicate: str = "is_site_owner = 1",
    owner_id: int | None = None,
    admin_verified: int = 1,
    owner_muted: int = 0,
) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        f'''
        CREATE TABLE "user" (
          id INTEGER PRIMARY KEY,
          username TEXT NOT NULL UNIQUE,
          verified INTEGER NOT NULL DEFAULT 0,
          is_muted INTEGER NOT NULL DEFAULT 0,
          is_admin INTEGER NOT NULL DEFAULT 0,
          is_site_owner INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO "user" VALUES (1, 'marisa', 1, 0, 1, 0);
        INSERT INTO "user" VALUES (
          2, '书书鸟Nipponia', 1, {owner_muted}, 0,
          {1 if owner_id == 2 else 0}
        );
        INSERT INTO "user" VALUES (
          3, 'Alumin_Hyrdo', {admin_verified}, 0, 0,
          {1 if owner_id == 3 else 0}
        );
        INSERT INTO "user" VALUES (4, 'ordinary', 1, 0, 0, 0);
        '''
    )
    if include_index:
        connection.execute(
            f'CREATE UNIQUE INDEX uq_user_single_site_owner '
            f'ON "user" ("{index_column}") WHERE {index_predicate}'
        )
    connection.commit()
    connection.close()


def _snapshot(path: Path) -> list[tuple[int, str, int, int]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            'SELECT id, username, is_admin, is_site_owner FROM "user" ORDER BY id'
        ).fetchall()
    finally:
        connection.close()


def _valid_assign(path: Path, marker: Path, guard=lambda: None):
    return roles.assign_roles(
        path,
        offline_marker=marker,
        offline_guard=guard,
        owner_id=2,
        owner_username="书书鸟Nipponia",
        admin_id=3,
        admin_username="Alumin_Hyrdo",
        preserved_admins=((1, "marisa"),),
    )


def test_atomic_initial_roles_fail_closed_then_succeed_and_are_idempotent(tmp_path):
    database = tmp_path / "forum.db"
    marker = tmp_path / "offline.json"
    _create_role_database(database)
    before = _snapshot(database)

    with pytest.raises(RuntimeError, match="offline marker"):
        _valid_assign(database, marker)
    assert _snapshot(database) == before

    roles.write_offline_marker(database, marker)
    with pytest.raises(RuntimeError, match="id/username mismatch"):
        roles.assign_roles(
            database,
            offline_marker=marker,
            offline_guard=lambda: None,
            owner_id=2,
            owner_username="书书鸟Nipponia",
            admin_id=3,
            admin_username="Alumin_Hydro",
            preserved_admins=((1, "marisa"),),
        )
    assert _snapshot(database) == before

    result = _valid_assign(database, marker)
    expected = [
        (1, "marisa", 1, 0),
        (2, "书书鸟Nipponia", 0, 1),
        (3, "Alumin_Hyrdo", 1, 0),
        (4, "ordinary", 0, 0),
    ]
    assert _snapshot(database) == expected
    assert result["owner_count"] == 1

    roles.write_offline_marker(database, marker)
    assert _valid_assign(database, marker)["owner_count"] == 1
    assert _snapshot(database) == expected


def test_offline_marker_is_bound_to_database_inode_and_freshness(tmp_path, monkeypatch):
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    marker = tmp_path / "offline.json"
    _create_role_database(first)
    _create_role_database(second)
    roles.write_offline_marker(first, marker)

    with pytest.raises(RuntimeError, match="does not match"):
        _valid_assign(second, marker)

    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["created_at_ns"] = time.time_ns() - (
        roles.OFFLINE_MARKER_MAX_AGE_SECONDS + 1
    ) * 1_000_000_000
    marker.write_text(json.dumps(payload), encoding="utf-8")
    marker.chmod(0o600)
    with pytest.raises(RuntimeError, match="stale"):
        _valid_assign(first, marker)


def test_role_transaction_rejects_invalid_invariants_without_writes(tmp_path):
    scenarios = [
        ({"include_index": False}, "partial index"),
        ({"index_column": "username"}, "wrong columns"),
        ({"index_predicate": "is_site_owner = 2"}, "wrong predicate"),
        ({"owner_id": 3}, "different station owner"),
        ({"admin_verified": 0}, "not verified"),
        ({"owner_muted": 1}, "is muted"),
    ]
    for index, (options, message) in enumerate(scenarios):
        database = tmp_path / f"scenario-{index}.db"
        marker = tmp_path / f"scenario-{index}.json"
        _create_role_database(database, **options)
        before = _snapshot(database)
        roles.write_offline_marker(database, marker)
        with pytest.raises(RuntimeError, match=message):
            _valid_assign(database, marker)
        assert _snapshot(database) == before

    database = tmp_path / "same-target.db"
    marker = tmp_path / "same-target.json"
    _create_role_database(database)
    before = _snapshot(database)
    roles.write_offline_marker(database, marker)
    with pytest.raises(RuntimeError, match="must be different"):
        roles.assign_roles(
            database,
            offline_marker=marker,
            offline_guard=lambda: None,
            owner_id=2,
            owner_username="书书鸟Nipponia",
            admin_id=2,
            admin_username="书书鸟Nipponia",
        )
    assert _snapshot(database) == before


def test_database_path_swap_after_marker_check_is_rejected(tmp_path, monkeypatch):
    database = tmp_path / "forum.db"
    original_hardlink = tmp_path / "original.db"
    replacement = tmp_path / "replacement.db"
    marker = tmp_path / "offline.json"
    _create_role_database(database)
    _create_role_database(replacement)
    os.link(database, original_hardlink)
    roles.write_offline_marker(database, marker)
    before = _snapshot(original_hardlink)

    real_connect = roles.sqlite3.connect
    swapped = False

    def swapping_connect(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path) == database:
            os.replace(replacement, database)
            swapped = True
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(roles.sqlite3, "connect", swapping_connect)
    with pytest.raises(RuntimeError, match="marked database inode"):
        _valid_assign(database, marker)
    assert swapped is True
    assert _snapshot(original_hardlink) == before


def test_cli_service_guard_requires_runtime_masked_inactive_unit(monkeypatch):
    class Result:
        returncode = 0

        def __init__(self, load_state: str, active_state: str):
            self.stdout = (
                f"LoadState={load_state}\nActiveState={active_state}\n"
            )

    state = {"load": "loaded", "active": "active"}
    calls = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(command)
        assert roles.MANAGED_SYSTEMD_UNIT in command
        return Result(state["load"], state["active"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="not runtime-masked"):
        roles._assert_managed_service_runtime_masked()

    state["load"] = "masked"
    with pytest.raises(RuntimeError, match="still active"):
        roles._assert_managed_service_runtime_masked()

    state["active"] = "inactive"
    roles._assert_managed_service_runtime_masked()
    assert len(calls) == 3


def test_cli_main_wires_the_service_guard_into_every_assignment(monkeypatch):
    guard_calls = []
    captured = {}

    def fake_guard():
        guard_calls.append("checked")

    def fake_assign(database, **kwargs):
        captured["database"] = database
        captured.update(kwargs)
        kwargs["offline_guard"]()
        return {"owner_count": 1}

    monkeypatch.setattr(roles, "_assert_managed_service_runtime_masked", fake_guard)
    monkeypatch.setattr(roles, "assign_roles", fake_assign)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "assign_initial_roles_atomic.py",
            "--database", "/tmp/forum.db",
            "--offline-marker", "/tmp/offline.json",
            "--owner-id", "2",
            "--owner-username", "书书鸟Nipponia",
            "--admin-id", "3",
            "--admin-username", "Alumin_Hyrdo",
            "--preserve-admin", "1:marisa",
        ],
    )
    roles.main()

    assert guard_calls == ["checked"]
    assert captured["offline_guard"] is fake_guard
    assert captured["preserved_admins"] == ((1, "marisa"),)


def test_documented_role_bootstrap_cannot_reach_unmask_after_failure():
    deployment = (Path(__file__).parents[1] / "DEPLOYMENT.md").read_text()
    section = deployment.split("### 首次同时初始化站长与管理员", 1)[1]
    shell_block = re.search(r"```bash\n(.*?)\n```", section, re.S)
    assert shell_block is not None
    script = shell_block.group(1)

    assert script.startswith("set -euo pipefail\n")
    role_gate = script.index("if ROLE_RESULT=$(sudo .venv/bin/python")
    failure_exit = script.index("exit 1", role_gate)
    success_assertion = script.index('test "$ROLE_OK" = 1', failure_exit)
    unmask = script.index("systemctl unmask --runtime touhou.service")
    start = script.index("systemctl start touhou.service")
    assert role_gate < failure_exit < success_assertion < unmask < start


def _rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5))


def _relative_luminance(hex_color: str) -> float:
    channels = []
    for value in _rgb(hex_color):
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first: str, second: str) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def test_rules_small_text_color_meets_wcag_aa_on_declared_solid_backgrounds():
    css = (Path(__file__).parents[1] / "app/static/css/style.css").read_text()
    for selector in ("rules-kicker", "rules-effective-note strong"):
        selector_pattern = selector.replace(" ", r"\s+")
        bodies = re.findall(r"\." + selector_pattern + r"\s*\{([^}]+)\}", css)
        assert len(bodies) == 1
        foreground = re.search(r"(?:^|\s)color:\s*(#[0-9a-fA-F]{6})", bodies[0])
        background = re.search(
            r"background-color:\s*(#[0-9a-fA-F]{6})", bodies[0]
        )
        assert foreground is not None and background is not None
        assert _contrast_ratio(foreground.group(1), background.group(1)) >= 4.5
