#!/usr/bin/env python3
"""Real-browser regression for long post titles/usernames at a 390px viewport.

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

SERVER_CODE = r'''
from app import create_app, db
from app.models import Board, Post, User
from settings import Config

class SmokeConfig(Config):
    TESTING = False
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False

app = create_app(SmokeConfig)
with app.app_context():
    db.drop_all()
    db.create_all()
    board = Board(name='绘画与创作', description='mobile smoke')
    user = User(username='u' * 64, email='mobile-smoke@example.com', verified=True)
    user.set_password('not-a-production-credential')
    db.session.add_all([board, user])
    db.session.flush()
    db.session.add(Post(
        title='[SMOKE]' + 'x' * 150,
        content='mobile layout regression',
        user_id=user.id,
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
        url = f"http://127.0.0.1:{port}/"
        deadline = time.monotonic() + 20
        while True:
            if proc.poll() is not None:
                raise RuntimeError(proc.stdout.read() if proc.stdout else "candidate exited")
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                if time.monotonic() >= deadline:
                    raise TimeoutError("candidate did not become ready")
                time.sleep(0.2)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            response = page.goto(url, wait_until="domcontentloaded")
            assert response and response.status == 200
            page.wait_for_timeout(200)
            result = page.evaluate(
                """() => ({
                    clientWidth: document.documentElement.clientWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    longTitleVisible: document.body.innerText.includes('[SMOKE]' + 'x'.repeat(150)),
                    longUsernameVisible: document.body.innerText.includes('u'.repeat(64)),
                    stylesheet: document.querySelector('link[href*="style.css"]').getAttribute('href')
                })"""
            )
            page.screenshot(path="/tmp/gensoumono-mobile-layout-smoke.png", full_page=True)
            browser.close()

        assert result["longTitleVisible"] is True
        assert result["longUsernameVisible"] is True
        assert result["scrollWidth"] <= result["clientWidth"], result
        assert "v=20260801-mobile-wrap" in result["stylesheet"]
        print(json.dumps({**result, "passed": True}, ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
