#!/usr/bin/env python3
"""Real-browser acceptance for the media workflow release:

1. Post image opens an in-page lightbox (no direct OSS navigation/download);
2. File pickers ask original-vs-crop before any upload, and the crop editor
   renders preview / zoom / ratio controls;
3. "Continue last reading" prompt appears on the next visit and links back
   to the stored post.

Run:  ~/.hermes/hermes-agent/venv/bin/python tests/media_experience_smoke.py
The candidate Flask app uses the repo .venv and a temporary SQLite DB.
"""
from __future__ import annotations

import json
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
APP_PYTHON = REPO / ".venv/bin/python"
assert APP_PYTHON.exists(), "create .venv and install requirements first"


def make_png(width: int = 64, height: int = 48) -> bytes:
    """生成一张真实可解码的 RGB PNG（无外部依赖）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(
        b"\x00" + b"".join(bytes(((x * 4) % 256, (y * 5) % 256, 128)) for x in range(width))
        for y in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url: str, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"flask exited early: {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("flask did not become ready")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="touhou-media-smoke-"))
    db_path = tmp / "test.db"
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    seed = tmp / "seed.py"
    seed.write_text(
        "from app import create_app, db\n"
        "from app.maintenance import initialize_database\n"
        "from app.models import Board, Post, User\n"
        "from settings import Config\n"
        "class C(Config):\n"
        "    TESTING = True\n"
        "    WTF_CSRF_ENABLED = False\n"
        f"    SQLALCHEMY_DATABASE_URI = 'sqlite:///{db_path}'\n"
        "    OSS_ACCESS_KEY_ID = 'test'\n"
        "    OSS_ACCESS_KEY_SECRET = 'test'\n"
        "    OSS_BUCKET = 'test'\n"
        "    OSS_ENDPOINT = 'oss-cn-shanghai.aliyuncs.com'\n"
        "    OSS_ENDPOINT_INTERNAL = 'oss-cn-shanghai.aliyuncs.com'\n"
        "    OSS_PUBLIC_BASE = 'https://test.oss-cn-shanghai.aliyuncs.com'\n"
        "app = create_app(C)\n"
        "with app.app_context():\n"
        "    initialize_database()\n"
        "    u = User(username='smoke-user', email='smoke@example.test', verified=True)\n"
        "    u.set_password('smoke-pass-123')\n"
        "    db.session.add(u)\n"
        "    db.session.flush()\n"
        "    b = Board.query.filter_by(name='观鸟记录').one()\n"
        "    p = Post(title='烟雾测试鸟帖', content='正文', user_id=u.id, board_id=b.id,\n"
        "             bird_name='朱鹭', location='测试公园',\n"
        "             photo_url='https://test.oss-cn-shanghai.aliyuncs.com/post/1/test.png')\n"
        "    db.session.add(p)\n"
        "    db.session.commit()\n"
        "    print('POST_ID', p.id)\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [str(APP_PYTHON), str(seed)], capture_output=True, text=True, cwd=REPO,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)},
    )
    if out.returncode != 0:
        print(out.stderr)
        return 1
    post_id = int(out.stdout.strip().split()[-1])

    env = dict(
        FLASK_APP="app",
        TOUHOU_DB=str(db_path),
    )
    run = tmp / "run.py"
    run.write_text(
        "from app import create_app\n"
        "from app.maintenance import initialize_database\n"
        "from settings import Config\n"
        "class C(Config):\n"
        "    TESTING = True\n"
        "    WTF_CSRF_ENABLED = False\n"
        f"    SQLALCHEMY_DATABASE_URI = 'sqlite:///{db_path}'\n"
        "    OSS_ACCESS_KEY_ID = 'test'\n"
        "    OSS_ACCESS_KEY_SECRET = 'test'\n"
        "    OSS_BUCKET = 'test'\n"
        "    OSS_ENDPOINT = 'oss-cn-shanghai.aliyuncs.com'\n"
        "    OSS_ENDPOINT_INTERNAL = 'oss-cn-shanghai.aliyuncs.com'\n"
        "    OSS_PUBLIC_BASE = 'https://test.oss-cn-shanghai.aliyuncs.com'\n"
        "app = create_app(C)\n"
        f"app.run(host='127.0.0.1', port={port})\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [str(APP_PYTHON), str(run)], cwd=REPO,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    failures = []
    console_errors = []
    try:
        wait_ready(base + "/", proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(str(e)))

            # ── 1. 灯箱 ─────────────────────────────────────────
            page.goto(f"{base}/forum/post/{post_id}", wait_until="networkidle")
            trigger = page.locator(".js-image-lightbox")
            assert trigger.count() == 1, "lightbox trigger missing"
            trigger.click()
            dialog = page.locator("#image-lightbox")
            assert dialog.is_visible(), "lightbox dialog not visible after click"
            src = page.locator("#image-lightbox-image").get_attribute("src") or ""
            assert src.startswith("https://test.oss-cn-shanghai.aliyuncs.com/"), f"unexpected lightbox src {src}"
            page.locator("[data-lightbox-close]").click()
            assert not dialog.is_visible(), "lightbox did not close"
            # 直接导航不会发生（灯箱是 button，不是 <a>）
            assert page.url.endswith(f"/forum/post/{post_id}")

            # ── 2. 恢复提示 ─────────────────────────────────────
            # 离开帖子页：localStorage 应已写入
            stored = page.evaluate(
                "localStorage.getItem('gensoumono:last-post:v1')"
            )
            assert stored and json.loads(stored)["url"] == f"/forum/post/{post_id}", f"last-post not stored: {stored}"
            page.goto(base + "/", wait_until="networkidle")
            prompt = page.locator("#resume-post-prompt")
            assert prompt.is_visible(), "resume prompt not visible on next page"
            link = page.locator("[data-resume-link]")
            assert "烟雾测试鸟帖" in (link.text_content() or "")
            link.click()
            page.wait_for_url(f"**/forum/post/{post_id}")

            # ── 3. 裁剪选择对话框 ────────────────────────────────
            page.goto(f"{base}/auth/login", wait_until="networkidle")
            page.fill("input[name=username]", "smoke-user")
            page.fill("input[name=password]", "smoke-pass-123")
            page.locator("form:has(input[name=password]) button[type=submit]").click()
            page.wait_for_url("**/")

            page.goto(f"{base}/forum/new_post/1", wait_until="networkidle")
            img = tmp / "pick.png"
            img.write_bytes(make_png())
            page.set_input_files("#photo-input", str(img))
            crop_dialog = page.locator("#image-crop-dialog")
            crop_dialog.wait_for(state="visible", timeout=5000)
            assert page.locator("[data-crop-action=original]").is_visible()
            # 进裁剪编辑器
            page.locator("[data-crop-action=crop]").click()
            editor = page.locator("[data-crop-editor]")
            assert editor.is_visible(), "crop editor not shown"
            assert page.locator("[data-crop-image]").is_visible(), "crop preview image missing"
            assert page.locator("[data-crop-zoom]").is_visible()
            assert page.locator("[data-crop-ratio='1']").is_visible()
            # 取消 → 回到选择层，再取消整个上传
            page.locator("[data-crop-action=back]").click()
            assert not editor.is_visible()
            page.locator("[data-crop-action=cancel]").click()
            assert not crop_dialog.is_visible(), "crop dialog did not close on cancel"

            # 再选一次，走「裁剪 → 确认」路径：到达 /oss/sign 的必须是 canvas
            # 重编码后的 JPEG（尺寸与原 PNG 不同、且随 prepare 统一命名为 upload.jpg）。
            sign_requests = []
            page.on("request", lambda req: sign_requests.append(req.post_data or "")
                    if req.url.endswith("/oss/sign") else None)
            original_size = img.stat().st_size
            page.set_input_files("#photo-input", str(img))
            crop_dialog.wait_for(state="visible", timeout=5000)
            page.locator("[data-crop-action=crop]").click()
            page.locator("[data-crop-ratio='1']").click()
            page.locator("[data-crop-action=confirm]").click()
            page.wait_for_timeout(2500)
            assert sign_requests, "crop confirm never reached /oss/sign"
            signed = json.loads(sign_requests[-1])
            assert signed["filename"] == "upload.jpg", f"cropped file should be JPEG: {signed}"
            assert signed["size"] != original_size, \
                f"signed size {signed['size']} equals original PNG size — encodeCrop never ran"

            # 无横向溢出
            overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
            assert not overflow, "horizontal overflow at 390px"

            browser.close()
    except AssertionError as exc:
        failures.append(str(exc))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if console_errors:
        # 假 OSS 域名必然 CORS/网络失败 —— 只过滤这一类，其余错误照样 fail
        real = [e for e in console_errors
                if "test.oss-cn-shanghai.aliyuncs.com" not in e
                and "net::ERR_FAILED" not in e]
        if real:
            failures.append(f"console errors: {real[:5]}")
    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS: lightbox / resume-prompt / crop-dialog all verified at 390px, no console errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
