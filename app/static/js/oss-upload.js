/* 图片直传阿里云 OSS —— 图片不经过后端。
 *
 * 流程：选图 → 向 /oss/sign 要一个预签名 PUT URL → 浏览器直接 PUT 到 OSS
 *      → 把返回的 key 填进隐藏字段 → 用户提交表单时只带这个 key。
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

    function signUpload(file, kind) {
        return fetch('/oss/sign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify({ kind: kind, filename: file.name, size: file.size })
        }).then(function (res) {
            // 登录过期时 Flask-Login 会 302 到登录页，拿回来的是 HTML 而不是 JSON
            var ct = res.headers.get('content-type') || '';
            if (ct.indexOf('application/json') === -1) {
                throw new Error('登录状态已失效，请重新登录后再试');
            }
            return res.json().then(function (data) {
                if (!res.ok) throw new Error(data.error || '签名失败');
                return data;
            });
        });
    }

    // 用 XMLHttpRequest 而不是 fetch：fetch 拿不到上传进度，
    // 而一张 20MB 原图要传几秒到几十秒，没有进度条用户会以为页面卡死了。
    function putToOss(xhr, sig, file, onProgress) {
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
            xhr.send(file);
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

        function lock(on) {
            uploading = on;
            Array.prototype.forEach.call(submits, function (b) { b.disabled = on; });
        }

        function say(msg, isError) {
            text.textContent = msg;
            status.style.color = isError ? '#c0392b' : '#7a6a5a';
        }

        function showPreview(file) {
            var img = document.getElementById(input.dataset.ossPreview || '');
            if (img) {
                // createObjectURL 是 O(1)。不要用 FileReader.readAsDataURL——
                // 它会把 20MB 的图编成约 27MB 的 base64 字符串，又慢又吃内存。
                img.src = URL.createObjectURL(file);
                img.style.display = 'block';
            }
            var wrap = document.getElementById(input.dataset.ossPreviewWrap || '');
            if (wrap) wrap.style.display = 'block';
            var hide = document.getElementById(input.dataset.ossHide || '');
            if (hide) hide.style.display = 'none';
        }

        input.addEventListener('change', function () {
            var file = input.files[0];
            if (!file) return;

            var ticket = ++seq;
            if (activeXhr) activeXhr.abort();

            keyField.value = '';
            showPreview(file);
            lock(true);
            bar.style.display = 'block';
            bar.value = 0;
            say('正在准备上传…');

            signUpload(file, input.dataset.ossKind).then(function (sig) {
                if (ticket !== seq) return null;
                var xhr = new XMLHttpRequest();
                activeXhr = xhr;
                return putToOss(xhr, sig, file, function (p) {
                    if (ticket === seq) {
                        bar.value = Math.round(p * 100);
                        say('上传中 ' + Math.round(p * 100) + '%');
                    }
                }).then(function () {
                    if (ticket !== seq) return;
                    keyField.value = sig.key;
                    bar.value = 100;
                    say('图片已上传，可以提交了');
                });
            }).catch(function (err) {
                if (ticket !== seq) return;  // 被新的选图中止的，不当作错误
                keyField.value = '';
                bar.style.display = 'none';
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
