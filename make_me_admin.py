#!/usr/bin/env python3
"""Retired: do not bypass the station-owner role boundary."""

raise SystemExit(
    "make_me_admin.py 已停用：管理员任免只能由站长在 /admin/users 执行；"
    "首次任命站长请使用 appoint_site_owner.py <user_id> <expected_username>。"
)
