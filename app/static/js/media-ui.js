/* 帖子图片灯箱与“继续上次阅读”。
 * 所有用户内容都只通过 textContent / 属性赋值进入 DOM，不做 HTML 字符串拼接。
 */
(function () {
    'use strict';

    var LAST_POST_KEY = 'gensoumono:last-post:v1';
    var RESUME_SHOWN_KEY = 'gensoumono:resume-shown:v1';
    var POST_PATH = /^\/forum\/post\/\d+$/;

    function safeUrl(raw) {
        try {
            var url = new URL(raw, window.location.origin);
            if (url.origin !== window.location.origin || !POST_PATH.test(url.pathname)) return null;
            return url.pathname;
        } catch (_) {
            return null;
        }
    }

    function setupLightbox() {
        var dialog = document.getElementById('image-lightbox');
        var image = document.getElementById('image-lightbox-image');
        var closeButton = dialog && dialog.querySelector('[data-lightbox-close]');
        if (!dialog || !image || !closeButton || typeof dialog.showModal !== 'function') return;

        var trigger = null;

        document.querySelectorAll('.js-image-lightbox').forEach(function (button) {
            button.addEventListener('click', function () {
                var raw = button.dataset.lightboxSrc || '';
                var full;
                try {
                    full = new URL(raw, window.location.href);
                } catch (_) {
                    return;
                }
                if (full.protocol !== 'https:' && full.protocol !== 'http:') return;

                trigger = button;
                image.src = full.href;
                image.alt = button.dataset.lightboxAlt || '帖子图片原图';
                dialog.showModal();
                closeButton.focus();
            });
        });

        closeButton.addEventListener('click', function () {
            dialog.close();
        });

        dialog.addEventListener('click', function (event) {
            if (event.target === dialog) dialog.close();
        });

        dialog.addEventListener('close', function () {
            image.removeAttribute('src');
            if (trigger && trigger.isConnected) trigger.focus();
            trigger = null;
        });
    }

    function readLastPost() {
        try {
            var value = JSON.parse(window.localStorage.getItem(LAST_POST_KEY) || 'null');
            if (!value || typeof value.title !== 'string') return null;
            var url = safeUrl(value.url);
            if (!url) return null;
            return { url: url, title: value.title.slice(0, 160) };
        } catch (_) {
            return null;
        }
    }

    function writeLastPost(marker) {
        var url = safeUrl(marker.dataset.resumePostUrl || '');
        if (!url) return;
        var title = (marker.dataset.resumePostTitle || '未命名帖子').slice(0, 160);
        try {
            window.localStorage.setItem(LAST_POST_KEY, JSON.stringify({
                url: url,
                title: title,
                savedAt: Date.now()
            }));
        } catch (_) {
            // Safari 私密模式或存储配额异常时静默降级，不阻断阅读。
        }
    }

    function setupResumePrompt() {
        var marker = document.querySelector('[data-resume-post-url]');
        var prompt = document.getElementById('resume-post-prompt');
        var link = prompt && prompt.querySelector('[data-resume-link]');
        var dismiss = prompt && prompt.querySelector('[data-resume-dismiss]');
        var saved = readLastPost();
        var currentPath = window.location.pathname;
        var alreadyShown = false;

        try {
            alreadyShown = window.sessionStorage.getItem(RESUME_SHOWN_KEY) === '1';
        } catch (_) {
            alreadyShown = false;
        }

        if (prompt && link && dismiss && saved && saved.url !== currentPath && !alreadyShown) {
            link.href = saved.url;
            link.textContent = '继续上次阅读：《' + saved.title + '》';
            prompt.hidden = false;
            try {
                window.sessionStorage.setItem(RESUME_SHOWN_KEY, '1');
            } catch (_) {
                // 存储不可用时允许下个页面再次提示，比丢失恢复入口更安全。
            }
            dismiss.addEventListener('click', function () {
                prompt.hidden = true;
            });
        }

        if (!marker) return;
        var save = function () { writeLastPost(marker); };
        save();
        window.addEventListener('pagehide', save);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'hidden') save();
        });
    }

    function init() {
        setupLightbox();
        setupResumePrompt();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
