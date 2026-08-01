# 部署手册：幻想博物志（阿里云 ECS + OSS）

> 域名：`gensoumono.cn`　状态：**生产已上线**（HTTPS / ICP / 邮件 / OSS 全链路可用）
> 最后更新：2026-08-01

---

## 0. 现状快照

| 项 | 现状 |
|---|---|
| 应用 | Flask 2.3.3；生产源码 `/srv/touhou`；Gunicorn 仅监听 `127.0.0.1:8001` |
| 数据库 | SQLite `/var/lib/touhou/forum.db`（独立于源码部署）；`settings.py` 支持 `DATABASE_URL` 切换 |
| 图片 | 浏览器直传阿里云 OSS bucket `gensoumono-img`；后端只签名、复核、删除 |
| 邮件 | 阿里云 DirectMail `smtpdm.aliyun.com:465`；发件人 `verify@auth.groovin.cn` |
| HTTPS | Nginx 80 → 443；Let's Encrypt 覆盖 apex + `www`；`certbot.timer` 自动续期 |
| 安全 | CSRF；Secure/HttpOnly/SameSite=Lax Cookie；UFW 只放 22/80/443；安全组 TCP 只放 22/80/443（保留 ICMP）；Fail2ban |
| 服务器 | 阿里云上海 ECS `i-uf60z751agi7ddrtt7cx`，公网 `106.14.66.88`，Ubuntu 24.04 |
| 当前版本 | `e683d9e8ca576260a2c1fb9d3b1d2843b333a986` |

### 上线状态与剩余待办

- [x] `SECRET_KEY` 使用生产随机值
- [x] OSS bucket 与 RAM 子账号就绪
- [x] OSS CORS 已包含本地、`https://gensoumono.cn`、`https://www.gensoumono.cn`；
      PUT/GET/HEAD 公网实测通过
- [x] 旧 126 SMTP 已停用；生产已切到 DirectMail，收件端实测 SPF/DKIM pass
- [x] ICP 已通过并展示 `京ICP备2026030889号-2`
- [x] apex / `www` TLS、HTTP 跳转、自动续期 dry-run 均通过
- [ ] **EXIF 旋转实测** —— 第 7 节两条手机横拍路径仍需真照片验收
- [ ] **公安联网备案** —— ICP 通过后 30 日内在 `www.beian.gov.cn` 完成
- [ ] 可选：为 OSS 绑定 `img.gensoumono.cn`，改善“点击原图”时默认 endpoint 触发下载的体验

### 当前公网入口

- `http://gensoumono.cn/*` → `301 https://gensoumono.cn/*`
- `https://www.gensoumono.cn/*` → `301 https://gensoumono.cn/*`
- `https://gensoumono.cn/` → Nginx → `127.0.0.1:8001`
- 未知 Host/SNI 返回 444；Gunicorn 的 8001 不进入阿里云安全组，也不在 UFW 放行
- 管理员密码不写进仓库或手册，统一存于 `~/.api_keys.json` 的 `gensoumono` 节（文件权限 600）
- favicon：`app/static/icons/`；SVG + 16/32 PNG + Apple Touch + Web Manifest，版本参数为 `20260801-bird-seal`

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
浏览器 ──⓪ 选图：嗅真实格式，必要时 canvas 转码 / 压缩（见下）
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

### 第 ⓪ 步：浏览器端归一化（别把它删掉）

`oss-upload.js` 在**选图那一刻**就读文件的前 12 字节嗅出真实格式，然后：

| 情况 | 做法 |
|---|---|
| JPG / PNG / GIF / WebP / BMP / TIFF | **原样上传，不重编码** —— 重编码会掉画质，还会丢 PNG 的透明和 GIF 的动画 |
| AVIF / HEIC | `createImageBitmap` + canvas **转成 JPEG** 再传 |
| 超过 `_MAX_BYTES` | **弹窗问用户**要不要压缩后上传；答应了就降质量 / 降分辨率压到限额内 |
| 浏览器都解不开 | 当场报错，**不浪费一次完整上传** |

上传用的文件名扩展名一律按**嗅出来的格式**取，不采信用户的文件名 —— 后端是按扩展名推
Content-Type 的，这样「扩展名 / Content-Type / 真实字节」三者才始终一致。

**为什么非有这一步不可**：B 站 / Twitter / 微博的 CDN 现在发 **AVIF**。用户右键「图片
另存为」拿到的文件叫 `xxx.jpg`，Windows 还按扩展名把 `file.type` 报成 `image/jpeg` ——
**可字节是 AVIF**。没有这一步的话：签名按扩展名放行 → 整张图传完 → 后端复核魔术字节不
认识 → **删掉对象并报「文件内容不是有效的图片」**。用户白传一场，且完全看不懂
（实测踩过，见第 6 节）。

后端的魔术字节白名单**故意不收 AVIF**，别去放开它：那是道安全阀（挡住把 HTML/SVG 伪装
成图片存进去），而且真放开了，存下来的对象会是 `Content-Type: image/jpeg` 配 AVIF 字节
—— OSS 图片处理不认，缩略图当场就裂。`check_oss.py` 对这两条都有回归防护。

### 缩略图：用 OSS 图片处理，不要跑 Pillow

2G 内存下 Pillow 必 OOM（6000×4000 的 JPEG 解码成 RGB 位图就是约 72MB，
处理过程还有多份副本）。模板里用 `{{ post.photo_url | thumb(1200) }}`，
OSS 实时生成并缓存；20MB 原图只在用户点击「查看原图」时才传。

`thumb()` 拼的参数串是 `image/auto-orient,1/resize,...`，**`auto-orient` 必须排在
`resize` 前面**（放后面则缩放按未旋转的宽高算，宽高对调）。它防的是 EXIF 旋转的**第二条
路**：原生上传的图（≤20MB 的 JPEG 由 `oss-upload.js` 原样直传、不重编码）EXIF 是完好的
—— 浏览器看原图会自己扶正，可**缩略图是 OSS 现生成的**，不显式要求扶正就可能吐一张躺倒
的图，而输出里 EXIF 已被剥掉，前端再也救不回来。于是**原图正着、缩略图躺着**。
（第一条路是转码 / 压缩，由 `oss-upload.js` 的 `imageOrientation:'from-image'` 负责。）

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
`settings.py` 只需换 `DATABASE_URL`、加 `psycopg[binary]`；`app/maintenance.py`
会按 SQLite / PostgreSQL 方言生成正确的布尔默认值和唯一站长索引。

### 初始化与每次升级

Gunicorn 只导入 `run:app`，不会执行 `run.py` 的 `__main__`。新装和已有数据库
都必须在启动新 worker **之前**显式执行维护入口：

```bash
cd /srv/touhou
sudo -u www-data .venv/bin/python -c "
from run import app
from app.maintenance import initialize_database
with app.app_context():
    initialize_database()
"
```

`initialize_database()` 是幂等的，固定完成三件事：

1. 创建缺失表；
2. 添加 `user.is_site_owner`、`board.sort_order` 等缺失列，并创建“至多一名站长”的部分唯一索引；
3. 按固定顺序补齐并更新六个板块：`综合讨论`、`观鸟记录`、`东方鸟类考据`、
   `绘画与创作`、`摄影交流`、`东方二次同人`。

生产升级顺序必须是：**验证数据库备份 → 停止旧 worker → 解包新代码 →
执行 `initialize_database()` → 核验列/索引/六板块/站长数 → 启动新 worker → 健康检查**。
不能先启动新代码再补列，否则首页会在查询 `board.sort_order` 时 500。

维护入口不会自动选择站长。待所有者明确指定经过邮箱验证的用户 ID 后，首次任命使用：

```bash
cd /srv/touhou
sudo -u www-data .venv/bin/python appoint_site_owner.py <USER_ID>
```

该命令只接受不可变数字 ID；对同一用户重复执行是幂等的，若已有不同站长则拒绝，
不会自动转移最高权限。`set_admin.py` / `make_me_admin.py` 已停用；管理员任免只允许
站长在 `/admin/users` 页面执行。

---

## 4. ECS 初始化与部署

生产部署不公开 Gunicorn 端口：应用只绑定 `127.0.0.1:8001`，公网统一经过 Nginx 80/443。

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
          -b 127.0.0.1:8001 --timeout 60 run:app
Restart=always

[Install]
WantedBy=multi-user.target
```

3 个 worker 是 2G 内存下的稳妥值（每个 Flask+SQLAlchemy worker 约 80–120MB）。
别套用 `2*核数+1` 的公式。

Nginx 反代到 `127.0.0.1:8001`；`app/static/`（css、js、backgrounds）由 Nginx 直接 serve。

`.env` 权限收紧：`chmod 600 .env && chown www-data:www-data .env`

---

## 5. 备案通过后的现状

1. [x] `gensoumono.cn` / `www` 指向 ECS；`www` 归一到 apex
2. [ ] OSS 自定义域名 `img.gensoumono.cn` 尚未绑定；当前继续使用 bucket 默认域名
3. [x] Let's Encrypt 已签发并启用；`certbot renew --dry-run` 通过
4. [x] 页脚展示 `京ICP备2026030889号-2` 并链接工信部
5. [ ] **公安联网备案**：ICP 通过后 **30 日内**在 `www.beian.gov.cn` 完成

> **已入库的老图片 URL 带的是 OSS 默认域名，换 `OSS_PUBLIC_BASE` 后它们不会跟着变。**
> `app/oss.py` 的 `_known_bases()` 因此**同时认两个前缀**（当前的 `OSS_PUBLIC_BASE`
> 和 bucket 默认域名）—— 老图照样有缩略图、照样删得掉，换域名不是断层。
> **别把这个判断改回单前缀 `startswith`**：那样一改，换域名当天此前上传的每一张图会
> 同时失去缩略图（帖子列表直接拉 20MB 原图、导航栏拿 5MB 头像去填 22×22 的框）和删除
> 能力（删帖不再删 OSS 对象），而且**两条都不报错**。`check_oss.py` 第 7 节把这一天
> 预演了一遍，是唯一钉得住它的地方。
>
> 迁移那两条 `UPDATE` 于是降级成**可选的收尾**（不跑也不会坏）。跑它的收益是让老图也
> 走自定义域名，从而「点开看原图」不再变成下载：
>
> ```sql
> UPDATE post SET photo_url  = REPLACE(photo_url,  '旧前缀', '新前缀');
> UPDATE "user" SET avatar_url = REPLACE(avatar_url, '旧前缀', '新前缀');  -- 头像同理，别漏
> ```

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
- **`settings.py` 的 `WTF_CSRF_TIME_LIMIT = None` 不要改回默认。** Flask-WTF 默认让
  CSRF token **一小时**后过期 —— 而观鸟记录常常是写一两个小时的长帖，页面就那么一直
  开着，到点一提交就是 400，**正文全丢**（2026-07-13 实测撞到过：登录页放了 1 小时
  54 分，两次 400）。设成 `None` **不放宽任何安全边界**：本站没开 remember-me，session
  cookie 就是浏览器会话 cookie，于是 token 与登录同寿、活不过登录本身，且仍由
  `SECRET_KEY` 签名、与 `session['csrf_token']` 绑定 —— 去掉的只是那个多余的时限。
  CSRF 真失败时（关掉浏览器再回来、清了 cookie、轮换了 `SECRET_KEY`）走
  `errors/400.html`，明确告诉用户正文还在；`/oss/*` 则回 **JSON** —— 否则
  `oss-upload.js` 见到 HTML 会一律当成登录失效，谎报一句「登录状态已失效」。
- **endpoint 必须是 `https://`**，否则上线后传图会静默失效。oss2 对不带协议头的 endpoint
  一律补成 `http://`，签出来的预签名 PUT URL 也就是 http 的 —— 本地开发页面自己是 http，
  http→http 不算混合内容，**本地怎么测都是绿的**；等站点上了 TLS，浏览器会把 https 页面里
  发往 http 的这个 PUT 当**混合内容**直接拦掉。已在 `app/oss.py` 的 `_endpoint()` 里统一补
  `https://` 钉死，`check_oss.py` 有回归防护。`OSS_PUBLIC_BASE` 同理必须是 `https://`
  —— 它会带着前缀原样入库，写错了每张历史图片都得改。
- **文件扩展名会骗人：`.jpg` 里装的可能是 AVIF。** B 站 / Twitter / 微博的 CDN 发的是
  AVIF，右键「另存为」拿到的文件叫 `xxx.jpg`，Windows 连 `file.type` 都按扩展名报成
  `image/jpeg`。**只有魔术字节不会骗人**（AVIF 是 `\x00\x00\x00\x1cftypavif`）。
  2026-07-13 实测踩过：头像怎么传都存不上，`/oss/sign` 200、`PUT` 200、表单 302，
  可 `avatar_url` 就是空的 —— 原来是 `confirm_upload` 复核字节时不认识，**删掉对象**后
  只 flash 了一句「文件内容不是有效的图片」。**整张图已经传完了才被拒。**
  现在由 `oss-upload.js` 在选图时就嗅格式并转码（见第 2 节第 ⓪ 步）。
- **生产环境不要临时公开 8000/8001**：Gunicorn 只绑 `127.0.0.1:8001`；安全组与 UFW 都只放 22/80/443。
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
- [ ] **AVIF**：拿一张 B 站存的图（名字是 `.jpg`、字节是 AVIF）传头像 → 应提示「正在转换
      为 JPEG」并**成功存上**；OSS 上落的是 `.jpg`，`head_object` 的 Content-Type 是
      `image/jpeg`，帖子页缩略图正常
- [ ] **超限压缩**：传一张超过限额的图（头像 >5MB / 帖子 >20MB）→ 应**弹窗**问要不要压缩
      → 选「取消」则不上传、`photo_key` 保持空；选「确定」则压到限额内并成功上传
- [ ] **EXIF 旋转**（两条路径，一张手机横拍的照片全能覆盖）。
      ⚠️ **别用微信 / QQ 把照片传到电脑**——它们会洗掉 EXIF，那就测了个寂寞；
      用数据线，或发送时选「原图」。
  - [ ] **原生路径**：≤20MB 的 JPEG 原样直传（EXIF 完好），扶正靠 OSS 的 `auto-orient`
        → **帖子列表和正文里的缩略图不能躺倒**（点开的原图正着、缩略图躺着，就是这条挂了）
  - [ ] **转码 / 压缩路径**：HEIC/AVIF，或超限后同意压缩（拿同一张照片设头像，5MB 上限
        大概率会弹压缩确认框），扶正靠 `oss-upload.js` 的 `imageOrientation:'from-image'`
        → **存下来的图不能是躺倒的**（导航栏 22×22、资料页 160px 的头像都看一眼）

### 上线后（2026-08-01 公网验收）

- [x] `GET /` 返回 200，首页原始数据与板块正常
- [x] 注册 → DirectMail 验证邮件 → 验证 → 登录 → 刷新恢复 session
- [x] 头像 OSS 直传；发图帖；回复；登出重登后帖子与回复持久化；测试对象已从 DB/OSS 清理
- [x] 管理员新密码登录并进入 `/admin/`；凭证只保存在 `~/.api_keys.json`
- [ ] 忘记密码 → 收到重置邮件 → 重置 → 用新密码登录
- [ ] 收件箱、**发件箱**删除私信均正常（发件箱曾因路由改 POST 而漏改模板，返回 405）
- [ ] 后台破坏性动作：置顶 / 删帖 / 删用户 / 切换管理员 / 解除禁言
- [x] 不存在 URL 返回自定义 404（“这只鸟飞走了”）
- [x] `app.debug is False`
- [x] CSRF 否定测试 400；路径穿越 400/404；公网 8000/8001 不可达；未知 Host 拒绝
- [x] 桌面与 390px 手机真实 Chromium 视觉冒烟；控制台/HTTP 错误为 0；无横向溢出
- [x] 页脚备案号已放
- [ ] 公安联网备案已做

---

## 8. 生产路径、备份与回滚

### 关键路径

- 源码与 venv：`/srv/touhou`、`/srv/touhou/.venv`
- 生产环境：`/srv/touhou/.env`（`www-data:www-data`，0600）
- 持久化数据库：`/var/lib/touhou/forum.db`
- 当前版本标记：`/var/lib/touhou/deployed-commit`
- systemd：`/etc/systemd/system/touhou.service`
- Nginx：`/etc/nginx/sites-available/touhou`、`/etc/nginx/conf.d/touhou-rate-limit.conf`、
  `/etc/nginx/snippets/touhou-proxy.conf`
- 证书：`/etc/letsencrypt/live/gensoumono.cn/`
- 本机部署证据与验收截图：`~/Project/gensoumono-ops/`

### 2026-08-01 回滚点

- `/var/backups/touhou/20260801-020722`：正式应用部署前的源码、`.env`、SQLite、systemd、Nginx
- `/var/backups/touhou/20260801-021232`：切换最终 TLS Nginx 配置前
- `/var/backups/touhou/20260801-024943`：移动端长标题换行热修前的 CSS 与基础模板
- `/var/backups/touhou/20260801-122536`：favicon 模板与静态资源部署前
- `/var/backups/touhou/20260801-122931`：Web Manifest MIME 的 Nginx 修复前
- `/var/backups/touhou/20260801-020455`：第一次就绪探测过快时自动回滚使用的快照（保留作证据）

回滚前先再做一份当前 DB 与配置备份。只回退移动端修复时，恢复 `024943` 中的
`style.css` / `base.html` 并重启 `touhou`；回退 TLS 时恢复 `021232` 的 Nginx 配置，
执行 `nginx -t` 后 reload；完整回退才使用 `020722` 的源码、环境和数据库。任何回退后都要复核：
`systemctl is-active touhou nginx`、本机 8001、公网 HTTPS、注册登录与 OSS。
