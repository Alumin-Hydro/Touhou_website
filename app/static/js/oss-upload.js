/* 图片直传阿里云 OSS —— 图片不经过后端。
 *
 * 流程：选图 → 嗅探真实格式（必要时在浏览器里转码 / 压缩）→ 向 /oss/sign 要一个
 *      预签名 PUT URL → 浏览器直接 PUT 到 OSS → 把返回的 key 填进隐藏字段
 *      → 用户提交表单时只带这个 key。
 *
 * 页面用法：
 *   <input type="file" data-oss-kind="post|avatar"
 *          data-oss-preview="预览 <img> 的 id"
 *          data-oss-preview-wrap="预览外层容器的 id（可选）"
 *          data-oss-hide="选图后要隐藏的元素 id（可选，如头像占位块）">
 *   同一个 <form> 内需有 <input type="hidden" name="photo_key">（头像是 avatar_key）
 */
(function () {
    var meta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = meta ? meta.content : '';

    // 后端 _looks_like_image 认的格式（见 app/oss.py）。别的一律先在浏览器里转码。
    var NATIVE_EXT = {
        jpeg: 'jpg', png: 'png', gif: 'gif', webp: 'webp', bmp: 'bmp', tiff: 'tiff'
    };

    function mb(bytes) {
        return (bytes / 1024 / 1024).toFixed(1);
    }

    /* 按魔术字节判断真实格式 —— 文件名是会骗人的。
     *
     * B 站 / Twitter / 微博的 CDN 现在发 AVIF；用户右键「图片另存为」存下来的文件叫
     * xxx.jpg，Windows 还按扩展名把 file.type 报成 image/jpeg，可字节是 AVIF。
     * 只信扩展名的话：签名放行 → 整张图传完 → 后端复核魔术字节不认识 → 删对象 + 报错
     * 「文件内容不是有效的图片」。用户白传一场，且完全看不懂。所以选图时就先嗅一遍。
     */
    function sniff(buf) {
        var b = new Uint8Array(buf);
        function at(i, s) {
            for (var j = 0; j < s.length; j++) {
                if (b[i + j] !== s.charCodeAt(j)) return false;
            }
            return true;
        }
        if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'jpeg';
        if (b[0] === 0x89 && at(1, 'PNG')) return 'png';
        if (at(0, 'GIF87a') || at(0, 'GIF89a')) return 'gif';
        if (at(0, 'RIFF') && at(8, 'WEBP')) return 'webp';
        if (at(0, 'BM')) return 'bmp';
        if (at(0, 'II*') && b[3] === 0x00) return 'tiff';
        if (at(0, 'MM') && b[2] === 0x00 && b[3] === 0x2a) return 'tiff';
        if (at(4, 'ftyp')) return 'heif';  // AVIF / HEIC / HEIF 共用这个 ISO-BMFF 盒子
        return 'unknown';
    }

    function decode(blob) {
        if (!window.createImageBitmap) {
            return Promise.reject(new Error('浏览器版本过旧，无法处理这张图片'));
        }
        // imageOrientation：手机拍的照片把方向记在 EXIF 里，而 canvas 重编码会把 EXIF
        // 整个丢掉 —— 不带这个参数，转出来 / 压出来的图会是躺倒的。
        return createImageBitmap(blob, { imageOrientation: 'from-image' });
    }

    function encodeJpeg(bitmap, scale, quality) {
        var w = Math.max(1, Math.round(bitmap.width * scale));
        var h = Math.max(1, Math.round(bitmap.height * scale));
        var canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        var ctx = canvas.getContext('2d');
        // JPEG 没有透明通道。不先铺一层白，canvas 里的透明像素会被编成黑色。
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(bitmap, 0, 0, w, h);
        return new Promise(function (resolve, reject) {
            canvas.toBlob(function (blob) {
                if (blob) resolve(blob);
                else reject(new Error('图片编码失败'));
            }, 'image/jpeg', quality);
        });
    }

    // 先压质量，再降分辨率。挨个试到能塞进 maxBytes 为止。
    var LADDER = [
        [1, 0.92], [1, 0.85], [1, 0.78],
        [0.8, 0.82], [0.8, 0.72],
        [0.6, 0.80], [0.6, 0.70],
        [0.45, 0.70], [0.30, 0.70]
    ];

    // ImageBitmap 是显式释放的：一张 6000×4000 的图解出来上百 MB，成功失败都得 close，
    // 别指望 GC。
    function withBitmap(bitmap, fn) {
        return Promise.resolve().then(fn).then(function (v) {
            bitmap.close();
            return v;
        }, function (e) {
            bitmap.close();
            throw e;
        });
    }

    function compress(bitmap, maxBytes) {
        var i = 0;
        function attempt() {
            if (i >= LADDER.length) {
                throw new Error('这张图压不到限制以内，请换一张');
            }
            var step = LADDER[i++];
            return encodeJpeg(bitmap, step[0], step[1]).then(function (blob) {
                return blob.size <= maxBytes ? blob : attempt();
            });
        }
        return Promise.resolve().then(attempt);
    }

    /* 归一化：把用户选的文件变成后端认得的东西。返回 {blob, filename, fmt}。
     *
     * filename 的扩展名一律按**嗅出来的真实格式**取，不采信用户文件名 —— 后端是按扩展名
     * 推 Content-Type 的，这样扩展名 / Content-Type / 真实字节三者才永远一致。
     * 原生支持的格式原样上传，不重编码：重编码会掉画质，PNG 的透明和 GIF 的动画也会丢。
     */
    function prepare(file, say) {
        return file.slice(0, 12).arrayBuffer().then(function (buf) {
            var fmt = sniff(buf);
            if (NATIVE_EXT[fmt]) {
                return { blob: file, filename: 'upload.' + NATIVE_EXT[fmt], fmt: fmt };
            }
            say(fmt === 'heif' ? '这张是 AVIF/HEIC 格式，正在转换为 JPEG…' : '正在转换图片格式…');
            return decode(file).then(function (bitmap) {
                return withBitmap(bitmap, function () {
                    return encodeJpeg(bitmap, 1, 0.92);
                }).then(function (blob) {
                    return { blob: blob, filename: 'upload.jpg', fmt: 'jpeg' };
                });
            }, function () {
                // 连浏览器自己都解不开 —— 那是真的不认识，别再往 OSS 上传了
                throw new Error('无法识别这个图片格式，请换一张（或先转成 JPG / PNG）');
            });
        });
    }

    function signUpload(cand, kind) {
        return fetch('/oss/sign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify({ kind: kind, filename: cand.filename, size: cand.blob.size })
        }).then(function (res) {
            // 这条路径上后端总是回 JSON —— 连 CSRF 失败也回（见 app/__init__.py 的
            // csrf_error）。所以拿到 HTML 只意味着「撞上了预期之外的东西」，别替它猜
            // 具体原因：一句猜错的报错比一句含糊的更误导人。
            var ct = res.headers.get('content-type') || '';
            if (ct.indexOf('application/json') === -1) {
                throw new Error('服务器响应异常（HTTP ' + res.status + '），请刷新页面后重试');
            }
            return res.json().then(function (data) {
                if (!res.ok) {
                    var err = new Error(data.error || '签名失败');
                    err.maxBytes = data.max_bytes;  // 只有「超限」这一种错才带，见 app/oss.py
                    throw err;
                }
                return data;
            });
        });
    }

    // 签名。后端要是说超限了，就问用户愿不愿意压缩后再传 —— 直接报错劝退太粗暴：
    // 手机随手一张原图就是 8~12MB，而头像上限只有 5MB。
    function signOrCompress(cand, kind, say) {
        return signUpload(cand, kind).then(function (sig) {
            return { sig: sig, cand: cand };
        }, function (err) {
            if (!err.maxBytes) throw err;  // 不是超限（格式不支持、被禁言……），原样抛

            var warn = '压缩会转成 JPEG，透明背景会变成白色';
            if (cand.fmt === 'gif') warn += '；GIF 动图会变成静态图';
            var ok = window.confirm(
                '这张图 ' + mb(cand.blob.size) + 'MB，超过了 ' + mb(err.maxBytes) + 'MB 的上限。\n\n'
                + '要压缩后再上传吗？\n（' + warn + '）'
            );
            if (!ok) throw new Error('已取消上传');

            say('正在压缩…');
            return decode(cand.blob).then(function (bitmap) {
                return withBitmap(bitmap, function () {
                    return compress(bitmap, err.maxBytes);
                });
            }).then(function (blob) {
                say('已压缩到 ' + mb(blob.size) + 'MB，正在上传…');
                var small = { blob: blob, filename: 'upload.jpg', fmt: 'jpeg' };
                return signUpload(small, kind).then(function (sig) {
                    return { sig: sig, cand: small };
                });
            });
        });
    }

    // 用 XMLHttpRequest 而不是 fetch：fetch 拿不到上传进度，
    // 而一张 20MB 原图要传几秒到几十秒，没有进度条用户会以为页面卡死了。
    function putToOss(xhr, sig, blob, onProgress) {
        return new Promise(function (resolve, reject) {
            xhr.open('PUT', sig.put_url, true);
            // 必须与后端签名时用的 Content-Type 完全一致，否则 OSS 判签名不符（403）
            xhr.setRequestHeader('Content-Type', sig.content_type);
            xhr.upload.onprogress = function (e) {
                if (e.lengthComputable) onProgress(e.loaded / e.total);
            };
            xhr.onload = function () {
                if (xhr.status >= 200 && xhr.status < 300) resolve();
                else reject(new Error('上传失败（HTTP ' + xhr.status + '）'));
            };
            xhr.onerror = function () { reject(new Error('上传失败，请检查网络连接')); };
            xhr.onabort = function () { reject(new Error('已取消')); };
            xhr.send(blob);
        });
    }

    /* ── 上传前「原图 / 裁剪」选择 ───────────────────────────────
     * 选图后先弹 #image-crop-dialog 问用户要原图还是裁剪：
     *   - 原图：resolve(null)，主流程拿到 null 就按原样走既有 prepare/sign/PUT；
     *   - 裁剪：进入编辑器（拖动取景、滑杆缩放、四种比例），确认后 resolve(canvas
     *     编出来的 JPEG File)，主流程把它当普通文件继续走 —— 魔术字节、签名、
     *     后端复核整条安全链一字未动。
     *   - 取消：reject，主流程按「已取消」还原预览。
     */
    function cropDialogParts() {
        var dialog = document.getElementById('image-crop-dialog');
        if (!dialog || typeof dialog.showModal !== 'function') return null;
        return {
            dialog: dialog,
            editor: dialog.querySelector('[data-crop-editor]'),
            stage: dialog.querySelector('[data-crop-stage]'),
            image: dialog.querySelector('[data-crop-image]'),
            frame: dialog.querySelector('[data-crop-frame]'),
            zoom: dialog.querySelector('[data-crop-zoom]'),
            ratios: dialog.querySelectorAll('[data-crop-ratio]'),
            byAction: function (name) {
                return dialog.querySelector('[data-crop-action="' + name + '"]');
            }
        };
    }

    /* 编辑器状态：scale 是「显示尺寸 / 原始尺寸」，offset 是图片左上角相对
     * 舞台的位移（CSS px）。取景框在舞台坐标系里，最终裁剪区域 =
     * (frame - offset) / scale 映射回原图像素。 */
    function renderCropPreview(parts, state) {
        var dw = state.bitmap.width * state.scale;
        var dh = state.bitmap.height * state.scale;
        // 舞台包住缩放后的图，但最大不超过视口宽度
        parts.stage.style.width = Math.min(dw, state.maxStage) + 'px';
        parts.stage.style.height = (dh * (Math.min(dw, state.maxStage) / dw)) + 'px';
        var shownScale = Math.min(1, state.maxStage / dw);
        state.shown = shownScale;  // 舞台可能比图小（窄屏），frame 计算要用实际显示比例
        parts.image.style.width = dw + 'px';
        parts.image.style.height = dh + 'px';
        parts.image.style.transform =
            'translate(' + state.offset.x + 'px,' + state.offset.y + 'px)' +
            ' scale(' + shownScale + ')';
        var fw = state.frame.w * shownScale;
        var fh = state.frame.h * shownScale;
        parts.frame.style.left = state.frame.x * shownScale + 'px';
        parts.frame.style.top = state.frame.y * shownScale + 'px';
        parts.frame.style.width = fw + 'px';
        parts.frame.style.height = fh + 'px';
    }

    function encodeCrop(bitmap, region) {
        var w = Math.max(1, Math.round(region.w));
        var h = Math.max(1, Math.round(region.h));
        var canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        var ctx = canvas.getContext('2d');
        // JPEG 无透明通道：不铺底的话 PNG 透明部分会被编成黑色
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(bitmap, region.x, region.y, region.w, region.h, 0, 0, w, h);
        return new Promise(function (resolve, reject) {
            canvas.toBlob(function (blob) {
                if (blob) resolve(blob);
                else reject(new Error('裁剪编码失败'));
            }, 'image/jpeg', 0.92);
        });
    }

    function chooseOriginalOrCrop(file, say) {
        var parts = cropDialogParts();
        if (!parts || !window.createImageBitmap) return Promise.resolve(null);  // 无对话框/老浏览器：维持原样上传

        return decode(file).then(function (bitmap) {
            return new Promise(function (resolve, reject) {
                var objectUrl = URL.createObjectURL(file);
                parts.image.src = objectUrl;
                var state = {
                    bitmap: bitmap,
                    scale: 1, shown: 1,
                    offset: { x: 0, y: 0 },
                    frame: { x: 0, y: 0, w: 0, h: 0 },
                    ratio: null,  // null = 自由
                    maxStage: Math.min(parts.dialog.clientWidth - 48 || 640, window.innerWidth * 0.9)
                };

                function cleanup() {
                    URL.revokeObjectURL(objectUrl);
                    bitmap.close();
                    parts.editor.hidden = true;
                    parts.dialog.close();
                }
                function done(result) { cleanup(); resolve(result); }
                function cancel() {
                    cleanup();
                    reject(new Error('已取消上传'));
                }

                // 初始：图片按舞台宽度铺开，取景框取居中的 80%
                function resetView() {
                    var fit = state.maxStage / bitmap.width;
                    state.scale = Math.min(fit, 1);
                    var dw = bitmap.width * state.scale;
                    var dh = bitmap.height * state.scale;
                    state.offset = { x: 0, y: 0 };
                    var fw = dw * 0.8, fh = dh * 0.8;
                    if (state.ratio) {
                        if (fw / fh > state.ratio) fw = fh * state.ratio;
                        else fh = fw / state.ratio;
                    }
                    state.frame = {
                        x: (dw - fw) / 2, y: (dh - fh) / 2, w: fw, h: fh
                    };
                    parts.zoom.value = Math.round(state.scale * 100 * (1 / fit) * (fit < 1 ? 100 : 1)) ;
                    parts.zoom.value = Math.max(100, Math.round((state.scale / fit) * 100));
                    renderCropPreview(parts, state);
                }

                // 拖动取景：按下记录起点，移动时平移 offset（图随手动），框不动
                var dragStart = null;
                parts.stage.onpointerdown = function (e) {
                    parts.stage.setPointerCapture(e.pointerId);
                    dragStart = { x: e.clientX, y: e.clientY, ox: state.offset.x, oy: state.offset.y };
                };
                parts.stage.onpointermove = function (e) {
                    if (!dragStart) return;
                    state.offset.x = dragStart.ox + (e.clientX - dragStart.x);
                    state.offset.y = dragStart.oy + (e.clientY - dragStart.y);
                    renderCropPreview(parts, state);
                };
                parts.stage.onpointerup = parts.stage.onpointercancel = function () {
                    dragStart = null;
                };

                parts.zoom.oninput = function () {
                    var fit = state.maxStage / bitmap.width;
                    var base = Math.min(fit, 1);
                    state.scale = base * (parts.zoom.value / 100);
                    renderCropPreview(parts, state);
                };

                Array.prototype.forEach.call(parts.ratios, function (btn) {
                    btn.addEventListener('click', function () {
                        Array.prototype.forEach.call(parts.ratios, function (b) { b.classList.remove('active'); });
                        btn.classList.add('active');
                        var v = btn.getAttribute('data-crop-ratio');
                        state.ratio = v === 'free' ? null : parseFloat(v);
                        // 按比例重算框尺寸，保持中心
                        var cx = state.frame.x + state.frame.w / 2;
                        var cy = state.frame.y + state.frame.h / 2;
                        var dw = bitmap.width * state.scale;
                        var dh = bitmap.height * state.scale;
                        var fw = state.frame.w, fh = state.frame.h;
                        if (state.ratio) {
                            fw = Math.min(dw * 0.8, dh * 0.8 * state.ratio);
                            fh = fw / state.ratio;
                            if (fh > dh * 0.8) { fh = dh * 0.8; fw = fh * state.ratio; }
                        }
                        state.frame = { x: cx - fw / 2, y: cy - fh / 2, w: fw, h: fh };
                        renderCropPreview(parts, state);
                    });
                });

                parts.byAction('original').onclick = function () { done(null); };
                parts.byAction('cancel').onclick = cancel;
                parts.byAction('crop').onclick = function () {
                    parts.editor.hidden = false;
                    resetView();
                };
                parts.byAction('back').onclick = function () {
                    parts.editor.hidden = true;
                };
                parts.byAction('confirm').onclick = function () {
                    say('正在裁剪…');
                    // 舞台坐标 → 原图像素：先除以显示比例回到缩放图坐标，
                    // 再减 offset、除以 scale。
                    var s = state.scale * state.shown;
                    var region = {
                        x: (state.frame.x * state.shown - state.offset.x) / s,
                        y: (state.frame.y * state.shown - state.offset.y) / s,
                        w: state.frame.w / state.scale,
                        h: state.frame.h / state.scale
                    };
                    // 夹紧到图片边界，防浮点误差裁到图外
                    region.x = Math.max(0, Math.min(region.x, bitmap.width - 1));
                    region.y = Math.max(0, Math.min(region.y, bitmap.height - 1));
                    region.w = Math.max(1, Math.min(region.w, bitmap.width - region.x));
                    region.h = Math.max(1, Math.min(region.h, bitmap.height - region.y));
                    encodeCrop(bitmap, region).then(function (blob) {
                        var cropped = new File([blob], 'crop.jpg', { type: 'image/jpeg' });
                        done(cropped);
                    }, function (err) {
                        cleanup();
                        reject(err);
                    });
                };
                parts.dialog.addEventListener('cancel', function onEsc() {
                    parts.dialog.removeEventListener('cancel', onEsc);
                    cancel();
                });

                parts.editor.hidden = true;
                parts.dialog.showModal();
            });
        }, function () {
            // 浏览器连预览都解不开 —— 交给后面的 prepare 报「无法识别格式」，
            // 这里不重复弹错。
            return null;
        });
    }

    function setup(input) {
        var form = input.form;
        var keyField = form.querySelector('input[name$="_key"]');
        var submits = form.querySelectorAll('button[type=submit], input[type=submit]');
        if (!keyField) return;

        var bar = document.createElement('progress');
        bar.max = 100;
        bar.value = 0;
        bar.style.cssText = 'width:100%; height:6px; display:none;';
        var text = document.createElement('span');
        var status = document.createElement('div');
        status.style.cssText = 'margin-top:0.4rem; font-size:0.85rem; color:#7a6a5a;';
        status.appendChild(bar);
        status.appendChild(text);
        input.insertAdjacentElement('afterend', status);

        var uploading = false;
        var activeXhr = null;
        var seq = 0;  // 用户连着换图时，用它作废掉上一次上传的结果

        var preview = document.getElementById(input.dataset.ossPreview || '');
        var previewWrap = document.getElementById(input.dataset.ossPreviewWrap || '');
        var hideEl = document.getElementById(input.dataset.ossHide || '');

        // 选图会立刻把预览换成新图（乐观预览），所以必须记住原样，好在失败 / 取消时还原。
        // 在头像页，这个 <img> 就是显示**当前头像**的那个元素 —— 不还原的话，用户点了
        // 取消却看见头像已经变了，会以为真被换掉了（其实库里纹丝没动）。
        var orig = {
            src: preview ? preview.getAttribute('src') : null,
            display: preview ? preview.style.display : '',
            wrap: previewWrap ? previewWrap.style.display : '',
            hide: hideEl ? hideEl.style.display : ''
        };
        var objectUrl = null;

        function lock(on) {
            uploading = on;
            Array.prototype.forEach.call(submits, function (b) { b.disabled = on; });
        }

        function say(msg, isError) {
            text.textContent = msg;
            status.style.color = isError ? '#c0392b' : '#7a6a5a';
        }

        function dropObjectUrl() {
            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);  // 不 revoke 的话，20MB 的 blob 会一直留在内存里
                objectUrl = null;
            }
        }

        function showPreview(file) {
            if (preview) {
                // createObjectURL 是 O(1)。不要用 FileReader.readAsDataURL——
                // 它会把 20MB 的图编成约 27MB 的 base64 字符串，又慢又吃内存。
                dropObjectUrl();
                objectUrl = URL.createObjectURL(file);
                preview.src = objectUrl;
                preview.style.display = 'block';
            }
            if (previewWrap) previewWrap.style.display = 'block';
            if (hideEl) hideEl.style.display = 'none';
        }

        function restorePreview() {
            dropObjectUrl();
            if (preview) {
                preview.src = orig.src === null ? '' : orig.src;
                preview.style.display = orig.display;
            }
            if (previewWrap) previewWrap.style.display = orig.wrap;
            if (hideEl) hideEl.style.display = orig.hide;
        }

        input.addEventListener('change', function () {
            var file = input.files[0];
            if (!file) return;

            var ticket = ++seq;
            if (activeXhr) activeXhr.abort();

            var kind = input.dataset.ossKind;
            keyField.value = '';
            showPreview(file);
            lock(true);
            bar.style.display = 'block';
            bar.value = 0;
            say('正在准备上传…');

            prepare(file, say).then(function (prepared) {
                if (ticket !== seq) return null;
                // 原生支持的格式（jpg/png/gif/webp/bmp/tiff）问用户要原图还是裁剪；
                // 已经被 prepare 转码过的（AVIF/HEIC）不再弹一次，直接走。
                if (prepared.blob !== file) return { file: prepared.blob, skipChoice: true };
                return chooseOriginalOrCrop(file, say).then(function (chosen) {
                    return { file: chosen || file, skipChoice: false };
                });
            }).then(function (sel) {
                if (!sel || ticket !== seq) return null;
                return prepare(sel.file, say);
            }).then(function (cand) {
                if (!cand || ticket !== seq) return null;
                return signOrCompress(cand, kind, say);
            }).then(function (r) {
                if (!r || ticket !== seq) return null;
                var xhr = new XMLHttpRequest();
                activeXhr = xhr;
                return putToOss(xhr, r.sig, r.cand.blob, function (p) {
                    if (ticket === seq) {
                        bar.value = Math.round(p * 100);
                        say('上传中 ' + Math.round(p * 100) + '%');
                    }
                }).then(function () {
                    if (ticket !== seq) return;
                    keyField.value = r.sig.key;
                    bar.value = 100;
                    say('图片已上传，可以提交了');
                });
            }).catch(function (err) {
                if (ticket !== seq) return;  // 被新的选图中止的，不当作错误
                keyField.value = '';
                bar.style.display = 'none';
                // 没传成功就把预览还原 —— 不然页面上挂着一张根本没存上的图，
                // 用户会以为头像 / 配图已经换掉了。
                restorePreview();
                input.value = '';  // 也把文件框清掉，否则再选同一个文件不会触发 change
                say(err.message, true);
            }).then(function () {
                if (ticket === seq) {
                    activeXhr = null;
                    lock(false);
                }
            });
        });

        // 保险：就算有人绕开了被禁用的提交按钮（比如在文本框里按回车），
        // 也不能在图还没传完时提交 —— 那样 photo_key 是空的，等于没上传。
        form.addEventListener('submit', function (e) {
            if (uploading) {
                e.preventDefault();
                say('图片还在上传，请稍候…', true);
            }
        });
    }

    var inputs = document.querySelectorAll('input[type=file][data-oss-kind]');
    Array.prototype.forEach.call(inputs, setup);
})();
