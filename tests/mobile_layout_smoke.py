#!/usr/bin/env python3
"""Real-browser regression for boards, mobile nav, and the staff console.

Run from the repository root with a Python that has Playwright installed:
    ~/.hermes/hermes-agent/venv/bin/python tests/mobile_layout_smoke.py

The candidate Flask process uses the repository .venv and a temporary SQLite DB.
It never points write operations at production.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
APP_PYTHON = REPO / ".venv/bin/python"
assert APP_PYTHON.exists(), "create .venv and install requirements first"

BOARD_NAMES = [
    "综合讨论",
    "观鸟记录",
    "东方鸟类考据",
    "绘画与创作",
    "摄影交流",
    "东方二次同人",
]

SERVER_CODE = r'''
from app import create_app, db
from app.maintenance import initialize_database
from app.models import Board, Post, User
from settings import Config

class SmokeConfig(Config):
    TESTING = False
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False

app = create_app(SmokeConfig)
with app.app_context():
    db.drop_all()
    initialize_database()
    board = Board.query.filter_by(name='绘画与创作').one()
    owner = User(
        username='station-owner',
        email='owner@local-smoke.invalid',
        verified=True,
        is_site_owner=True,
    )
    owner.set_password('local-smoke-password')
    member = User(
        username='u' * 64,
        email='member@local-smoke.invalid',
        verified=True,
    )
    member.set_password('local-smoke-password')
    db.session.add_all([owner, member])
    db.session.flush()
    db.session.add(Post(
        title='[SMOKE]' + 'x' * 150,
        content='mobile layout regression',
        user_id=member.id,
        board_id=board.id,
    ))
    db.session.commit()
app.run(host='127.0.0.1', port=int(__import__('os').environ['SMOKE_PORT']), use_reloader=False)
'''

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

with tempfile.TemporaryDirectory(prefix="gensoumono-mobile-") as tmp:
    env = os.environ.copy()
    env.update(
        DATABASE_URL=f"sqlite:///{Path(tmp) / 'smoke.db'}",
        SECRET_KEY="mobile-layout-smoke-only",
        SESSION_COOKIE_SECURE="false",
        SMOKE_PORT=str(port),
    )
    proc = subprocess.Popen(
        [str(APP_PYTHON), "-c", SERVER_CODE],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while True:
            if proc.poll() is not None:
                raise RuntimeError(proc.stdout.read() if proc.stdout else "candidate exited")
            try:
                with urllib.request.urlopen(f"{base_url}/", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                if time.monotonic() >= deadline:
                    raise TimeoutError("candidate did not become ready")
                time.sleep(0.2)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            console_errors: list[str] = []
            page_errors: list[str] = []
            http_errors: list[str] = []
            request_errors: list[str] = []

            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "response",
                lambda response: http_errors.append(
                    f"{response.status} {response.url}"
                )
                if response.status >= 400
                else None,
            )

            def record_request_failure(request):
                failure = request.failure or "unknown"
                known_background_abort = (
                    request.resource_type == "image"
                    and "/static/backgrounds/" in request.url
                    and "ERR_ABORTED" in failure
                )
                if not known_background_abort:
                    request_errors.append(f"{failure} {request.url}")

            page.on("requestfailed", record_request_failure)

            response = page.goto(f"{base_url}/", wait_until="domcontentloaded")
            assert response and response.status == 200
            page.wait_for_timeout(250)
            assert page.locator(".nav-toggle").is_visible()
            assert not page.locator(".nav-links").is_visible()

            page.locator(".nav-toggle").click()
            assert page.locator(".nav-links").is_visible()
            rendered_boards = page.locator(".nav-main a").all_inner_texts()
            for board_name in BOARD_NAMES:
                assert board_name in rendered_boards
            min_touch_height = page.eval_on_selector_all(
                ".nav-main a",
                "elements => Math.min(...elements.map(el => el.getBoundingClientRect().height))",
            )
            mobile_home = page.evaluate(
                """() => ({
                    clientWidth: document.documentElement.clientWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    longTitleVisible: document.body.innerText.includes('[SMOKE]' + 'x'.repeat(150)),
                    longUsernameVisible: document.body.innerText.includes('u'.repeat(64)),
                    stylesheet: document.querySelector('link[href*="style.css"]').getAttribute('href')
                })"""
            )
            page.screenshot(path="/tmp/gensoumono-mobile-nav-open.png", full_page=True)

            rules_response = page.goto(
                f"{base_url}/rules", wait_until="domcontentloaded"
            )
            assert rules_response and rules_response.status == 200
            assert page.get_by_role("heading", name="论坛规则", exact=True).is_visible()
            assert page.get_by_role("heading", name="板块专属规则").is_visible()
            real_record_copy = page.get_by_text("记录须基于实际观察", exact=False)
            assert real_record_copy.count() >= 1
            assert real_record_copy.first.is_visible()
            assert page.get_by_text("真实或虚构", exact=False).count() == 0
            rules_contrast = page.evaluate(
                """() => {
                    const channel = value => {
                        value /= 255;
                        return value <= 0.04045
                            ? value / 12.92
                            : Math.pow((value + 0.055) / 1.055, 2.4);
                    };
                    const luminance = color => {
                        const values = color.match(/\d+(?:\.\d+)?/g)
                            .slice(0, 3).map(Number).map(channel);
                        return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
                    };
                    const measure = selector => {
                        const style = getComputedStyle(document.querySelector(selector));
                        const foreground = luminance(style.color);
                        const background = luminance(style.backgroundColor);
                        return {
                            color: style.color,
                            backgroundColor: style.backgroundColor,
                            ratio: (Math.max(foreground, background) + 0.05) /
                                (Math.min(foreground, background) + 0.05)
                        };
                    };
                    return {
                        kicker: measure('.rules-kicker'),
                        effective: measure('.rules-effective-note strong')
                    };
                }"""
            )
            mobile_rules = page.evaluate(
                """() => {
                    const table = document.querySelector('.rules-table-wrap');
                    return {
                        clientWidth: document.documentElement.clientWidth,
                        scrollWidth: document.documentElement.scrollWidth,
                        tableClientWidth: table.clientWidth,
                        tableScrollWidth: table.scrollWidth
                    };
                }"""
            )
            page.screenshot(path="/tmp/gensoumono-rules-mobile.png", full_page=True)

            profile_response = page.goto(
                f"{base_url}/user/station-owner", wait_until="domcontentloaded"
            )
            assert profile_response and profile_response.status == 200
            assert page.get_by_text("站务身份：站长", exact=True).is_visible()
            assert page.get_by_text("贡献等级：初来乍到", exact=True).is_visible()
            assert page.get_by_text("管理员特权", exact=False).count() == 0
            mobile_profile = page.evaluate(
                "() => ({clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth})"
            )
            page.screenshot(path="/tmp/gensoumono-profile-role-mobile.png", full_page=True)

            page.goto(f"{base_url}/auth/login", wait_until="domcontentloaded")
            page.locator('input[name="username"]').fill("station-owner")
            page.locator('input[name="password"]').fill("local-smoke-password")
            page.get_by_role("button", name="🌸 进入幻想乡").click()
            page.wait_for_url(f"{base_url}/")

            admin_response = page.goto(
                f"{base_url}/admin/", wait_until="domcontentloaded"
            )
            assert admin_response and admin_response.status == 200
            assert page.get_by_role("heading", name="幻想博物志管理台").is_visible()
            assert page.get_by_text("站长", exact=True).is_visible()
            mobile_admin = page.evaluate(
                "() => ({clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth})"
            )
            page.screenshot(path="/tmp/gensoumono-admin-mobile.png", full_page=True)

            users_response = page.goto(
                f"{base_url}/admin/users", wait_until="domcontentloaded"
            )
            assert users_response and users_response.status == 200
            assert page.get_by_role("button", name="任命管理员").is_visible()
            mobile_users = page.evaluate(
                "() => ({clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth})"
            )
            page.screenshot(path="/tmp/gensoumono-users-mobile.png", full_page=True)

            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(f"{base_url}/admin/", wait_until="domcontentloaded")
            assert not page.locator(".nav-toggle").is_visible()
            assert page.locator(".nav-links").is_visible()
            desktop_admin = page.evaluate(
                "() => ({clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth})"
            )
            page.screenshot(path="/tmp/gensoumono-admin-desktop.png", full_page=True)
            page.goto(f"{base_url}/admin/users", wait_until="domcontentloaded")
            desktop_users = page.evaluate(
                "() => ({clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth})"
            )
            page.screenshot(path="/tmp/gensoumono-users-desktop.png", full_page=True)
            page.goto(f"{base_url}/rules", wait_until="domcontentloaded")
            desktop_rules = page.evaluate(
                "() => ({clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth})"
            )
            page.screenshot(path="/tmp/gensoumono-rules-desktop.png", full_page=True)
            browser.close()

        assert mobile_home["longTitleVisible"] is True
        assert mobile_home["longUsernameVisible"] is True
        assert mobile_home["scrollWidth"] <= mobile_home["clientWidth"], mobile_home
        assert mobile_rules["scrollWidth"] <= mobile_rules["clientWidth"], mobile_rules
        assert mobile_rules["tableScrollWidth"] <= mobile_rules["tableClientWidth"], mobile_rules
        assert rules_contrast["kicker"]["ratio"] >= 4.5, rules_contrast
        assert rules_contrast["effective"]["ratio"] >= 4.5, rules_contrast
        assert rules_contrast["kicker"]["backgroundColor"] != "rgba(0, 0, 0, 0)"
        assert rules_contrast["effective"]["backgroundColor"] != "rgba(0, 0, 0, 0)"
        assert mobile_profile["scrollWidth"] <= mobile_profile["clientWidth"], mobile_profile
        assert mobile_admin["scrollWidth"] <= mobile_admin["clientWidth"], mobile_admin
        assert mobile_users["scrollWidth"] <= mobile_users["clientWidth"], mobile_users
        assert desktop_admin["scrollWidth"] <= desktop_admin["clientWidth"], desktop_admin
        assert desktop_users["scrollWidth"] <= desktop_users["clientWidth"], desktop_users
        assert desktop_rules["scrollWidth"] <= desktop_rules["clientWidth"], desktop_rules
        assert min_touch_height >= 44, min_touch_height
        assert "v=20260801-rules-role-fix" in mobile_home["stylesheet"]
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        assert http_errors == [], http_errors
        assert request_errors == [], request_errors
        print(
            json.dumps(
                {
                    "mobileHome": mobile_home,
                    "mobileRules": mobile_rules,
                    "rulesContrast": rules_contrast,
                    "mobileProfile": mobile_profile,
                    "mobileAdmin": mobile_admin,
                    "mobileUsers": mobile_users,
                    "desktopAdmin": desktop_admin,
                    "desktopUsers": desktop_users,
                    "desktopRules": desktop_rules,
                    "boards": rendered_boards,
                    "minTouchHeight": min_touch_height,
                    "consoleErrors": len(console_errors),
                    "pageErrors": len(page_errors),
                    "httpErrors": len(http_errors),
                    "requestErrors": len(request_errors),
                    "passed": True,
                },
                ensure_ascii=False,
            )
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
