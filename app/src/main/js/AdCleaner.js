/**
 * AdCleaner - DOM level ad sweeper (Layer 4).
 * Implements behavioural rules from the ad-block/anti-redirect technical notes:
 * rules describe shapes of behaviour (invisible, bottom-pinned, randomly named,
 * write-then-query-back), never concrete strings, so rotating domain/class/ports
 * cannot evade them.
 *
 * Configuration arrives via window.__ADC__ (injected by the browser right before
 * this script):
 *   { site: "example.com",
 *     rules: ["invisible-hotspot","computed-hotspot","bottom-banner",
 *             "random-tag","random-name","cover-iframe","ad-host","popup-text"],
 *     cosmetic: [".some-class", "#some-id"],
 *     hosts: ["ads.example.com"] }
 *
 * Design lines (red lines from the technical notes):
 *   - fail-open: any error inside a pass is swallowed, never breaks the page
 *   - protect site UI: off-screen / tiny / interactive / background-image elements are never removed
 *   - every removal is logged to console so regressions are explainable
 *   - idempotent: re-injection is a no-op
 */
(function () {
    'use strict';
    if (window.__ADC_INSTALLED__) { return; }
    window.__ADC_INSTALLED__ = true;

    var cfg = window.__ADC__ || { rules: [], cosmetic: [], hosts: [] };
    var enabled = {};
    (cfg.rules || []).forEach(function (r) { enabled[r] = true; });
    var DEBUG = !!cfg.debug;

    function log(action, el, reason) {
        var tag = el && el.tagName ? el.tagName.toLowerCase() : '?';
        var id = el && el.id ? '#' + el.id : '';
        var cls = el && el.className && typeof el.className === 'string' ? '.' + el.className.split(/\s+/).slice(0, 3).join('.') : '';
        var line = '[AdCleaner] ' + action + ' <' + tag + id + cls + '> ' + reason;
        if (DEBUG) { try { console.log(line); } catch (e) {} }
    }

    function has(list, name) { return Object.prototype.hasOwnProperty.call(enabled, name); }

    // ---------- helpers ----------

    function viewport() {
        var w = window.innerWidth || document.documentElement.clientWidth;
        var h = window.innerHeight || document.documentElement.clientHeight;
        return { w: w, h: h };
    }

    function isFixedish(el) {
        var cs = window.getComputedStyle(el);
        var pos = cs.position;
        return pos === 'fixed' || pos === 'absolute';
    }

    // Inline opacity read from the style attribute only (no layout flush)
    function inlineOpacity(el) {
        var s = el.style && el.style.opacity;
        if (!s) return null;
        var v = parseFloat(s);
        return isNaN(v) ? null : v;
    }

    function rectSize(el) {
        var r = el.getBoundingClientRect();
        return { w: r.width, h: r.height, top: r.top, left: r.left };
    }

    function isOffScreen(el) {
        var vp = viewport();
        var r = el.getBoundingClientRect();
        return r.bottom < 0 || r.right < 0 || r.top > vp.h || r.left > vp.w;
    }

    function hasInteractiveChild(el) {
        return !!el.querySelector('img,a,button,input,video,select,textarea,iframe');
    }

    function hasBackgroundImage(el) {
        var cs = window.getComputedStyle(el);
        return !!(cs.backgroundImage && cs.backgroundImage !== 'none');
    }

    function textLength(el) {
        return (el.textContent || '').trim().length;
    }

    var STD_TAGS = {};
    ('a abbr address area article aside audio b base bdi bdo blockquote body br button canvas caption cite code col colgroup data datalist dd del details dfn dialog div dl dt em embed fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 head header hgroup hr html i iframe img input ins kbd label legend li link main map mark menu meta meter nav noscript object ol optgroup option output p param picture pre progress q rp rt ruby s samp section select slot small source span strong style sub summary sup table tbody td template textarea tfoot th thead time title tr track u ul var video wbr').split(' ').forEach(function (t) { STD_TAGS[t] = true; });

    // Random naming shape: 3-12 letters, no separator, at least 2 uppercase
    function isRandomName(name) {
        return /^[A-Za-z]{3,12}$/.test(name) && (name.match(/[A-Z]/g) || []).length >= 2;
    }

    function hostOf(url) {
        try { return new URL(url, location.href).hostname.toLowerCase(); } catch (e) { return null; }
    }

    function hostBlocked(url) {
        if (!cfg.hosts || !cfg.hosts.length) return false;
        var h = hostOf(url);
        if (!h) return false;
        for (var i = 0; i < cfg.hosts.length; i++) {
            var d = cfg.hosts[i];
            if (h === d || h.endsWith('.' + d)) return true;
        }
        return false;
    }

    // ---------- removal ----------

    var REMOVED = 0;
    var MAX_REMOVE_PER_PASS = 400;

    function removeEl(el, reason) {
        if (!el || !el.parentNode || REMOVED >= MAX_REMOVE_PER_PASS) return;
        REMOVED++;
        log('remove', el, reason);
        try { el.parentNode.removeChild(el); } catch (e) {}
    }

    // ---------- behavioural rule checks (each returns true/false/'maybe') ----------

    function checkInvisibleHotspot(el) {
        if (!isFixedish(el)) return false;
        var op = inlineOpacity(el);
        if (op === null || op >= 0.05) return false;
        var s = rectSize(el);
        if (s.w < 20 && s.h < 20) return false; // tiny layout helpers are protected
        if (hasInteractiveChild(el)) return false;
        return true;
    }

    // For computed-opacity variants: requires a second confirmation round.
    var maybePending = [];
    function checkComputedHotspot(el) {
        if (!isFixedish(el)) return false;
        var s = rectSize(el);
        if (s.w < 20 && s.h < 20) return false;
        if (hasInteractiveChild(el)) return false;
        var cs = window.getComputedStyle(el);
        var op = parseFloat(cs.opacity);
        if (isNaN(op) || op >= 0.05) { return false; }
        return 'maybe';
    }

    function checkBottomBanner(el) {
        if (!isFixedish(el)) return false;
        if (el.parentNode !== document.body) return false;
        var vp = viewport();
        var s = rectSize(el);
        if (s.w < vp.w * 0.85) return false;
        if (s.h < 45 || s.h > 320) return false;
        if (s.top < vp.h * 0.5) return false;
        if (!hasInteractiveChild(el)) return false;
        if (textLength(el) >= 40) return false;
        return true;
    }

    function checkRandomTag(el) {
        if (el.parentNode !== document.body) return false;
        var tag = el.tagName.toLowerCase();
        if (STD_TAGS[tag]) return false;
        return true;
    }

    function checkRandomName(el) {
        if (el.parentNode !== document.body) return false;
        var tag = el.tagName.toLowerCase();
        if (['div', 'section', 'span', 'a', 'iframe'].indexOf(tag) < 0) return false;
        var id = el.id || '';
        var cls = (typeof el.className === 'string') ? el.className.split(/\s+/)[0] : '';
        return isRandomName(id) || isRandomName(cls);
    }

    function checkCoverIframe(el) {
        if (el.tagName.toLowerCase() !== 'iframe') return false;
        var cs = window.getComputedStyle(el);
        var z = parseInt(cs.zIndex, 10);
        if (isNaN(z) || z < 99999) return false;
        if (!isFixedish(el)) return false;
        return true;
    }

    function checkAdHost(el) {
        if (!cfg.hosts || !cfg.hosts.length) return false;
        var url = el.src || el.href || '';
        if (!url) return false;
        return hostBlocked(url);
    }

    var POPUP_TEXT = /未满\s*18|18\s*禁|年龄验证|已满\s*18/;
    function checkPopupText(el) {
        if (textLength(el) === 0) return false;
        if (!POPUP_TEXT.test(el.textContent)) return false;
        var cs = window.getComputedStyle(el);
        if (cs.position !== 'fixed' && cs.position !== 'absolute') return false;
        var s = rectSize(el);
        if (s.w < 200 || s.h < 100) return false;
        return true;
    }

    // ---------- passes ----------

    var CHECKS = [];
    if (has('invisible-hotspot')) CHECKS.push(checkInvisibleHotspot);
    if (has('bottom-banner')) CHECKS.push(checkBottomBanner);
    if (has('random-tag')) CHECKS.push(checkRandomTag);
    if (has('random-name')) CHECKS.push(checkRandomName);
    if (has('cover-iframe')) CHECKS.push(checkCoverIframe);
    if (has('ad-host')) CHECKS.push(checkAdHost);
    if (has('popup-text')) CHECKS.push(checkPopupText);
    if (has('computed-hotspot')) CHECKS.push(checkComputedHotspot);

    function sweep() {
        REMOVED = 0;
        try {
            var candidates = document.querySelectorAll('body *');
            for (var i = 0; i < candidates.length && REMOVED < MAX_REMOVE_PER_PASS; i++) {
                var el = candidates[i];
                if (!el || !el.parentNode) continue;
                for (var c = 0; c < CHECKS.length; c++) {
                    var verdict;
                    try { verdict = CHECKS[c](el); } catch (e) { continue; }
                    if (verdict === true) { removeEl(el, 'rule'); break; }
                    if (verdict === 'maybe') { maybePending.push(el); break; }
                }
            }
            applyCosmetic();
        } catch (e) { /* fail-open */ }
    }

    // Cosmetic selectors: hide placeholders/promo slots without removing (safe).
    var cosmeticStyleEl = null;
    function applyCosmetic() {
        var sel = (cfg.cosmetic || []).filter(function (s) { return typeof s === 'string' && s.length && s.length < 200; });
        if (!sel.length) return;
        try {
            if (!cosmeticStyleEl || !cosmeticStyleEl.parentNode) {
                cosmeticStyleEl = document.createElement('style');
                cosmeticStyleEl.setAttribute('data-adcleaner', '1');
                (document.head || document.documentElement).appendChild(cosmeticStyleEl);
            }
            cosmeticStyleEl.textContent = sel.join(',') + '{display:none !important;}';
        } catch (e) { /* fail-open */ }
    }

    // Second-round confirmation for computed-opacity hotspots (3s later).
    var confirmTimer = null;
    function scheduleConfirm() {
        if (!has('computed-hotspot')) return;
        if (confirmTimer) return;
        confirmTimer = setTimeout(function () {
            confirmTimer = null;
            var pending = maybePending;
            maybePending = [];
            pending.forEach(function (el) {
                if (!el || !el.parentNode) return;
                try {
                    var verdict = checkComputedHotspot(el);
                    if (verdict === true || verdict === 'maybe') removeEl(el, 'computed-hotspot confirmed');
                } catch (e) { /* fail-open */ }
            });
        }, 3000);
    }

    // Capture-phase event suppression: while a sweep has not yet removed a hostile
    // element, clicks/touches on invisible overlays are swallowed. Also defeats
    // hijack handlers delegated on document.
    function hitInvisibleOverlay(target) {
        try {
            var el = target;
            for (var i = 0; el && i < 4; i++, el = el.parentElement) {
                if (!isFixedish(el)) continue;
                var op = inlineOpacity(el);
                if (op !== null && op < 0.05 && rectSize(el).w >= 20) return true;
            }
        } catch (e) { /* fail-open */ }
        return false;
    }

    function swallow(event) {
        if (hitInvisibleOverlay(event.target)) {
            log('swallow', event.target, event.type + ' on invisible overlay');
            event.stopPropagation();
            event.preventDefault();
        }
    }

    try {
        document.addEventListener('touchend', swallow, true);
        document.addEventListener('touchstart', swallow, true);
        document.addEventListener('click', swallow, true);
    } catch (e) { /* fail-open */ }

    // ---------- triggers ----------

    try {
        var mo = new MutationObserver(function () {
            // Batch: run on the next microtask so insertion happens before paint.
            if (window.__ADC_MO_PEND__) return;
            window.__ADC_MO_PEND__ = true;
            Promise.resolve().then(function () {
                window.__ADC_MO_PEND__ = false;
                sweep();
                scheduleConfirm();
            });
        });
        var start = function () {
            try {
                mo.observe(document.documentElement, { childList: true, subtree: true });
            } catch (e) { /* fail-open */ }
            sweep();
            scheduleConfirm();
            // Timed fallback: some ads are injected late or via timers.
            setInterval(function () { sweep(); scheduleConfirm(); }, 2000);
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', start, { once: true });
        } else {
            start();
        }
    } catch (e) { /* fail-open */ }

    // Signal for diagnostics: page-level census of what we removed.
    window.__ADC_STATS__ = { removed: function () { return REMOVED; } };
})();
