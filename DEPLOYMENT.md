# 部署手册：幻想博物志（阿里云 ECS + OSS）

> 域名：`gensoumono.cn`　状态：**ICP 备案已提交，等管局审核**（1–20 工作日）
> 最后更新：2026-07-12

---

## 0. 现状快照

| 项 | 现状 |
|---|---|
| 应用 | Flask 2.3.3，蓝图：auth / forum / birding / message / profile / admin / **oss** |
| 数据库 | SQLite（`instance/forum.db`，不入库）；已开 WAL，`settings.py` 支持 `DATABASE_URL` 切换 |
| 图片 | **浏览器直传阿里云 OSS**，后端只签名与复核，图片不经过本服务 |
| 邮件 | 126 邮箱 SMTP，SSL 465 端口 |
| 安全 | CSRF 全覆盖；8 个状态变更路由为 POST-only；密码重置流程已上线 |
| 服务器 | 阿里云 ECS e 实例，2 核 2G / 3M 固定带宽 / 40G ESSD Entry，大陆地域、包年包月 |

### 上线前还剩什么

- [x] ~~轮换 `SECRET_KEY`~~ —— 已于 2026-07-12 换成新的 32 字节随机值
- [ ] **轮换 126 邮箱 SMTP 授权码** —— 曾进入已推送的 git 历史，须视为已泄露。
      去 126 邮箱后台重置，然后更新 `.env` 的 `MAIL_PASSWORD`。
- [ ] **在阿里云控制台开 OSS**（见第 1 节）—— 代码已就绪，只差配置
- [ ] 备案通过后：绑自定义域名、签证书、页脚放备案号、30 天内做公安备案

### 备案卡什么、不卡什么

**不卡**：本地开发与测试、OSS 直传本身（用默认 endpoint + CORS 就能跑通）、
`<img>` 展示图片、在 ECS 上用 `http://<公网IP>:8000` 自测（避开 80/443）。

**只卡一件事**：用 `gensoumono.cn` 在大陆 ECS 上对公网提供服务（80/443）。
阿里云在网络层按 Host/SNI 拦截未备案域名，跟代码无关。附带地，大陆 bucket
绑自定义域名（`img.gensoumono.cn`）同样要备案。

---

## 1. 阿里云 OSS 配置（代码已就绪，只差这一步）

### 1.1 创建 Bucket

| 项 | 选什么 | 为什么 |
|---|---|---|
| 地域 | **与 ECS 同地域** | 同地域才能走内网 endpoint，后端调 OSS 不花流量费、不占 3M 出向带宽 |
| 读写权限 | **公共读**（公共读、私有写） | 图片要能被 `<img>` 直接引用；写入必须签名 |
| 存储类型 | 标准存储 | |
| 版本控制 / 服务端加密 | 关 | 用不上，省钱 |

### 1.2 RAM 子账号（**绝不要用主账号 AccessKey**）

主账号 AccessKey 泄露 = 整个阿里云账号沦陷（对方能删你的 ECS、开机器挖矿）。

1. RAM 控制台 → 创建用户 → 只勾「OpenAPI 调用访问」→ 保存 AccessKey（**只显示一次**）
2. 新建**自定义权限策略**，只授权这一个 bucket（不要用 `AliyunOSSFullAccess`）：

```json
{
  "Version": "1",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["oss:PutObject", "oss:GetObject", "oss:DeleteObject", "oss:GetObjectMeta"],
    "Resource": ["acs:oss:*:*:你的bucket名/*"]
  }]
}
```

### 1.3 CORS（浏览器直传的必要条件，不配就 100% 失败）

Bucket → 数据安全 → 跨域设置：

- 来源：`http://localhost:5000`、`http://127.0.0.1:5000`（本地开发）、
  `https://gensoumono.cn`、`https://www.gensoumono.cn`（上线后）
- Methods：`PUT`、`GET`、`HEAD`
- 允许 Headers：`*`　暴露 Headers：`ETag`　缓存时间：600

### 1.4 填 `.env`

```bash
OSS_ACCESS_KEY_ID=<RAM 子账号的 AccessKey ID>
OSS_ACCESS_KEY_SECRET=<RAM 子账号的 AccessKey Secret>
OSS_BUCKET=<bucket 名>
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com                      # 公网，按实际地域改
OSS_ENDPOINT_INTERNAL=                                         # 本地留空！见下
OSS_PUBLIC_BASE=https://<bucket>.oss-cn-hangzhou.aliyuncs.com  # 备案后换成 https://img.gensoumono.cn
```

> **`OSS_ENDPOINT_INTERNAL` 在本地必须留空**，只有部署到 ECS 上才填成
> `oss-cn-hangzhou-internal.aliyuncs.com`（按实际地域改）。内网 endpoint 只在阿里云 VPC
> 内部解析得了；本地填了它，`confirm_upload()`（走 `_bucket(internal=True)`）会一路连到
> 超时才失败，报出来的样子像权限问题，其实跟权限毫无关系。留空时 `settings.py` 会自动
> 回落到公网 endpoint。`check_oss.py` 开头会先探一次，踩了会直接告诉你。

填完先跑 **`python check_oss.py`** 一键验真实链路（CORS 预检 → 签名 → 直传 → 复核 →
缩略图 → 删除），全绿之后再 `python run.py` 用浏览器走一遍第 7 节的清单。
两步都不需要备案。

> **备案前的临时现象**：OSS 默认域名会给图片强制加 `Content-Disposition: attachment`
> （2019-09 后新建的 bucket 都如此）。浏览器只在**导航请求**时看这个头，`<img>` 子资源
> 加载不看 —— 所以帖子里的图正常显示，只有「点击查看原图」会变成下载。
> 绑上 `img.gensoumono.cn` 后即恢复正常。这不是 bug。

---

## 2. 图片架构（已实现）

```
浏览器 ──① POST /oss/sign ─────> Flask（校验后签发预签名 PUT URL，5 分钟有效）
浏览器 ──② PUT 原图 ───────────> OSS         ← 不经 ECS，不占带宽不占磁盘
浏览器 ──③ 提交表单（只带 key）─> Flask（复核后入库）
访客   ──④ <img> 读图 ─────────> OSS
```

为什么必须这样：3 Mbps 出向 ≈ 375 KB/s，一张 20MB 原图独占全部带宽发给单个访客
也要约 53 秒；40G 盘按 20MB/张只存得下约 1700 张。论坛图片是「写一次、读很多次」，
瓶颈恰好在读侧。

### 代码分布

| 文件 | 职责 |
|---|---|
| `app/oss.py` | `sign_upload` / `confirm_upload` / `resolve_upload` / `delete_by_url` / `thumb`，以及 `POST /oss/sign` |
| `app/static/js/oss-upload.js` | 前端直传（XHR + 进度条），4 个模板共用 |
| `app/forum.py` · `app/admin.py` · `app/profile.py` | 收 `photo_key` / `avatar_key` 而非文件 |

> `app/admin.py` 的管理员编辑/删帖同样在写图片 —— 本手册早期版本漏列了这处，已补。

### 三条安全约束（改这块代码时不要破坏）

1. **key 一律由后端生成**（`{kind}/{user_id}/{uuid}.{ext}`），绝不接受前端指定，
   否则前端可以覆盖任意对象。
2. **key 里编入 user_id，入库时比对当前用户**。否则 A 可以把 B 刚上传的 key 填进
   自己的表单，之后 A 删帖会连带把 B 的图删掉。
3. **入库前必须回查对象本身**（`head_object` 查大小与类型 + 读前 12 字节验魔术字节）。
   预签名 URL 一旦签出，客户端实际 PUT 了什么字节后端是看不见的 —— 签名只约束了
   key 和 Content-Type，约束不了内容。

### 缩略图：用 OSS 图片处理，不要跑 Pillow

2G 内存下 Pillow 必 OOM（6000×4000 的 JPEG 解码成 RGB 位图就是约 72MB，
处理过程还有多份副本）。模板里用 `{{ post.photo_url | thumb(1200) }}`，
OSS 实时生成并缓存；20MB 原图只在用户点击「查看原图」时才传。

---

## 3. 数据库

继续用 SQLite。论坛读多写少，2 核 2G 的规模下完全够用，备份就是拷一个文件。
PostgreSQL 会额外吃掉 150–250MB 内存。

WAL 与 busy_timeout 已在 `app/__init__.py` 的 `_enable_sqlite_wal()` 里配好
（gunicorn 多进程下必须，否则读写互相阻塞、并发写直接抛 `database is locked`）。

部署时**把数据库文件挪出代码目录**，否则每次部署都可能被覆盖：

```bash
sudo mkdir -p /var/lib/touhou && sudo chown www-data:www-data /var/lib/touhou
# .env： DATABASE_URL=sqlite:////var/lib/touhou/forum.db   （四个斜杠 = 绝对路径）
```

**何时迁 PostgreSQL**：发帖/回复开始偶发 `database is locked` 时再说。届时
`settings.py` 只需换 `DATABASE_URL`、加 `psycopg[binary]`；`run.py` 的 `migrate_db()`
用的是标准 `ALTER TABLE ... ADD COLUMN`，Postgres 同样支持。

### 首次初始化

`run.py` 的建表逻辑写在 `if __name__ == '__main__':` 里，gunicorn 不会执行。
首次部署需手动跑一次：

```bash
.venv/bin/python -c "
from run import app, migrate_db
from app import db
from app.models import Board
with app.app_context():
    db.create_all(); migrate_db()
    if Board.query.count() == 0:
        for n in ['综合讨论','观鸟记录','东方鸟类考据','绘画与创作']:
            db.session.add(Board(name=n, description=f'{n}板块'))
        db.session.commit()
"
```

---

## 4. ECS 初始化与部署

备案期间就可以先搭好，用 `http://<公网IP>:8000` 自测（安全组临时放行 8000，别碰 80/443）。

系统选 **Ubuntu 24.04 LTS**：自带 Python 3.12，与本地 `.venv` 一致，消除版本漂移。

```bash
# 2G 内存务必加 swap，防止装依赖 / 突发流量时 OOM
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo sysctl -w vm.swappiness=10

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip nginx git ufw fail2ban
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

SSH 加固：禁用密码登录（`PasswordAuthentication no`），只用密钥。

`run.py` 在模块层就有 `app = create_app()`，gunicorn 可直接用 `run:app`
（`app.run(debug=True)` 在 `__main__` 分支里，不会被执行）。

`/etc/systemd/system/touhou.service`：

```ini
[Unit]
Description=Touhou Birding Forum
After=network.target

[Service]
User=www-data
WorkingDirectory=/srv/touhou
EnvironmentFile=/srv/touhou/.env
ExecStart=/srv/touhou/.venv/bin/gunicorn -w 3 -k gthread --threads 2 \
          -b 127.0.0.1:8000 --timeout 60 run:app
Restart=always

[Install]
WantedBy=multi-user.target
```

3 个 worker 是 2G 内存下的稳妥值（每个 Flask+SQLAlchemy worker 约 80–120MB）。
别套用 `2*核数+1` 的公式。

Nginx 反代到 `127.0.0.1:8000`；`app/static/`（css、js、backgrounds）由 Nginx 直接 serve。

`.env` 权限收紧：`chmod 600 .env && chown www-data:www-data .env`

---

## 5. 备案通过后

1. 域名解析：`gensoumono.cn` / `www` → ECS 公网 IP；`img.gensoumono.cn` → OSS
2. OSS 绑定自定义域名 `img.gensoumono.cn`（子域名继承主域名备案，无需单独备案），
   然后把 `.env` 的 `OSS_PUBLIC_BASE` 换成 `https://img.gensoumono.cn`
3. certbot 签 Let's Encrypt 证书，Nginx 上 TLS
4. **页脚放备案号**并链到 `https://beian.miit.gov.cn` —— 硬性合规要求，不放会被巡查
5. **公安联网备案**：ICP 备案通过后 **30 日内**在 www.beian.gov.cn 完成

> 已入库的老图片 URL 是 OSS 默认域名。换 `OSS_PUBLIC_BASE` 后新图走新域名，
> 老图仍指向旧 URL（依然可访问）。数量少，不值得写迁移脚本；真要统一，
> 直接 `UPDATE post SET photo_url = REPLACE(photo_url, 旧前缀, 新前缀)`。

> CDN 先不上。初期流量小，OSS 外网流出 ~0.5 元/GB 与 CDN ~0.2 元/GB 差不了几毛钱，
> 少一层复杂度。以后要加，在 `img.gensoumono.cn` 前面套一层即可，代码零改动。

---

## 6. 已知坑

- **备案要求包年包月实例**，且剩余时长满 3 个月；按量付费实例申请不了备案服务号。
- **OSS 自定义域名和 CDN 域名也要备案**，不是只备 ECS 的主域名。
- **阿里云 ECS 默认封禁 TCP 25 出方向**（需申请解封）。`settings.py` 用的是
  **465 + SSL**，不受影响 —— **不要改回 25**。
- **`app.run(debug=True)`** 只在本地 `python run.py` 时生效。务必确认生产走 gunicorn，
  绝不能让 debug 模式暴露到公网（Werkzeug 调试器可执行任意代码）。
- **endpoint 必须是 `https://`**，否则上线后传图会静默失效。oss2 对不带协议头的 endpoint
  一律补成 `http://`，签出来的预签名 PUT URL 也就是 http 的 —— 本地开发页面自己是 http，
  http→http 不算混合内容，**本地怎么测都是绿的**；等站点上了 TLS，浏览器会把 https 页面里
  发往 http 的这个 PUT 当**混合内容**直接拦掉。已在 `app/oss.py` 的 `_endpoint()` 里统一补
  `https://` 钉死，`check_oss.py` 有回归防护。`OSS_PUBLIC_BASE` 同理必须是 `https://`
  —— 它会带着前缀原样入库，写错了每张历史图片都得改。
- **备案期间域名不可用**，用 `IP:8000` 调试，别用 80/443。
- **`requirements.txt` 现已全部钉死版本**。注意 Flask 2.3.3 只声明 `Werkzeug>=2.3.7`
  而无上限 —— 本地实测跑的是 Werkzeug 3.1.8，已钉住；别让线上解析到别的大版本。

---

## 7. 验证清单

### 本地（配好 OSS 就能全部跑通，不需要备案）

- [ ] 发帖上传一张 20MB 原图 → Network 面板里应看到 `POST /oss/sign`，随后一个 `PUT`
      **直接打到 OSS 域名，不经过 Flask**；上传时有进度条
- [ ] OSS 控制台确认对象落在 `post/<你的 user_id>/<uuid>.jpg`
- [ ] 帖子页显示的是缩略图（URL 带 `x-oss-process`），点击才是原图
- [ ] 删帖 → OSS 上对应对象消失
- [ ] 头像上传、**管理员编辑帖子**（`/admin/posts/edit/<id>`）各验一遍
- [ ] 安全：表单里把 `photo_key` 改成别人 user_id 的 key → 必须被拒（帖子照发，但不带图）
- [ ] 安全：拿签出的 URL 用 curl PUT 一个非图片文件 → 提交时应被复核拒绝并删除该对象

### 上线后

- [ ] `GET /` 返回 200，首页「发布新记录」指向「观鸟记录」板块
- [ ] 注册 → 收到验证邮件 → 验证 → 登录
- [ ] 忘记密码 → 收到重置邮件 → 重置 → 用新密码登录
- [ ] 收件箱、**发件箱**删除私信均正常（发件箱曾因路由改 POST 而漏改模板，返回 405）
- [ ] 后台：置顶 / 删帖 / 删用户 / 切换管理员 / 解除禁言
- [ ] 访问不存在的 URL → 自定义 404 页
- [ ] 生产环境确认 `app.debug is False`
- [ ] 页脚备案号已放、公安备案已做
