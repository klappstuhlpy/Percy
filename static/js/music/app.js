/* Music app client. Upgrades the server-rendered player in templates/music/app.html:
 * 5 s live poll (paused while the tab is hidden) with rAF progress interpolation, transport with
 * optimistic UI + rollback, seek/volume, search, play/queue, likes, history/liked/playlist views,
 * synced lyrics, queue drag-reorder/remove for managers, toasts, connection banner, shortcuts.
 * Percy decides what a viewer may do (the viewer JSON); this script only mirrors it. */
(function () {
    'use strict';

    const root = document.getElementById('mapp');
    if (!root) return;

    // ── Utilities ────────────────────────────────────────────────────────
    const GID = document.body.dataset.guildId || '';
    const BASE = `/dashboard/music/${GID}`;
    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const ico = (n) => `<svg aria-hidden="true"><use href="/static/icons/sprite.svg#i-${n}"/></svg>`;
    const fmt = (ms) => { const s = Math.max(0, Math.floor((ms || 0) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
    const q = (s) => `“${s}”`;
    /* Mirrors MusicAppTemplate::grad (sum of code points mod 8). */
    const grad = (s) => { let n = 0; for (const ch of String(s || '')) n += ch.codePointAt(0); return n % 8; };
    /* Cover candidates, best first, as [url, letterboxed]; mirrors templates::guild::cover_chain.
       YouTube hq/sd thumbnails are 4:3 with black bars (CSS zooms .is-lb past them); maxres/mq are 16:9. */
    function coverChain(url, cls) {
        const m = /(?:ytimg\.com|img\.youtube\.com)\/vi(?:_webp)?\/([^/]+)\//.exec(url || '');
        if (!m) return [[url, false]];
        const base = `https://i.ytimg.com/vi/${m[1]}/`;
        const size = /np/.test(cls) ? 'lg' : /fill/.test(cls) ? 'card' : 'sm';
        const names = { lg: [['maxresdefault', false], ['sddefault', true], ['hqdefault', true], ['mqdefault', false]], card: [['hqdefault', true], ['mqdefault', false]], sm: [['mqdefault', false], ['hqdefault', true]] }[size];
        const out = names.map(([n, lb]) => [`${base}${n}.jpg`, lb]);
        if (!out.some(([u]) => u === url)) out.push([url, /hqdefault|sddefault/.test(url)]);
        return out;
    }
    function art(url, seed, cls) {
        let img = '';
        if (url) {
            const [first, ...rest] = coverChain(url, cls);
            img = `<img src="${esc(first[0])}"${first[1] ? ' class="is-lb"' : ''} data-fallback="${esc(rest.map(([u, lb]) => (lb ? 'lb:' : '') + u).join(' '))}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
        }
        return `<span class="mapp-art ${cls} g${grad(seed)}" aria-hidden="true">${ico('music')}${img}</span>`;
    }
    /* Next cover candidate, or drop the image so the placeholder underneath shows. */
    function nextCover(img) {
        const rest = (img.dataset.fallback || '').split(' ').filter(Boolean);
        if (!rest.length) { img.remove(); return; }
        const nxt = rest.shift();
        img.dataset.fallback = rest.join(' ');
        img.classList.toggle('is-lb', nxt.startsWith('lb:'));
        img.src = nxt.replace(/^lb:/, '');
    }
    /* Artist and album links. Percy gives a name (the track's author) and, for Spotify-like sources,
       the artist/album page link; the pages themselves are static/js/music/detail.js. */
    const artistHref = (name, url) => `#artist/${encodeURIComponent(name)}${url ? '?u=' + encodeURIComponent(url) : ''}`;
    const albumHref = (a, artist) => (a && a.url ? `#album?u=${encodeURIComponent(a.url)}&n=${encodeURIComponent(a.name || '')}${artist ? '&a=' + encodeURIComponent(artist) : ''}` : '');
    /* "A, B" becomes two links; Percy's artist link belongs to the first name. */
    function authorHtml(author, artistUrl) {
        const names = String(author || '').split(',').map((n) => n.trim().replace(/ - Topic$/, '')).filter(Boolean);
        if (!names.length) return 'Unknown artist';
        return names.map((n, i) => `<a class="mapp-ln" href="${esc(artistHref(n, i === 0 ? artistUrl : ''))}">${esc(n)}</a>`).join(', ');
    }
    function subHtml(t, withAlbum) {
        const href = withAlbum && t.album ? albumHref(t.album, t.author) : '';
        return authorHtml(t.author, t.artist_url || t.artistUrl) + (href ? ` <span class="mapp-sep">·</span> <a class="mapp-ln" href="${esc(href)}">${esc(t.album.name)}</a>` : '');
    }
    const store = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } } };

    function ago(iso) {
        if (!iso) return '';
        const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'); // Percy stores naive UTC
        if (Number.isNaN(t)) return '';
        const m = Math.round((Date.now() - t) / 60000);
        if (m < 1) return 'Just now';
        if (m < 60) return `${m} min ago`;
        if (m < 1440) return `${Math.round(m / 60)} h ago`;
        if (m < 10080) return `${Math.round(m / 1440)} d ago`;
        return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
    }

    // ── Toasts ───────────────────────────────────────────────────────────
    // The shared stack (static/js/common/toast.js); the player lifts it above the bottom bar in CSS.
    const toast = (kind, msg) => window.PercyToast && window.PercyToast.show(kind, msg);

    // ── API ──────────────────────────────────────────────────────────────
    function errText(d, status) {
        if (d && (d.error || d.detail)) return String(d.error || d.detail);
        if (status === 401 || status === 403) return 'Your session expired. Reload the page to log in again.';
        if (status === 404) return 'That was not found. It may have just changed.';
        if (status >= 500) return 'Percy is not responding right now. Try again in a moment.';
        return 'Something went wrong. Try again.';
    }
    async function api(url, opt) {
        const o = opt || {};
        try {
            const r = await fetch(url, {
                method: o.method || 'GET',
                credentials: 'same-origin',
                headers: Object.assign({ Accept: 'application/json' }, o.body ? { 'Content-Type': 'application/json' } : {}),
                body: o.body ? JSON.stringify(o.body) : undefined,
            });
            const isJson = (r.headers.get('content-type') || '').includes('json');
            const d = isJson ? await r.json().catch(() => ({})) : {};
            if (!isJson && r.ok) return { ok: false, status: 401, error: errText(null, 401) }; // a login page, not JSON
            if (!r.ok || d.ok === false) return { ok: false, status: r.status, error: errText(d, r.status) };
            return { ok: true, data: d };
        } catch (_) {
            return { ok: false, status: 0, error: 'Could not reach the server. Check your connection.' };
        }
    }

    // ── State ────────────────────────────────────────────────────────────
    let boot = {};
    try { boot = JSON.parse(root.dataset.boot || '{}'); } catch (_) { boot = {}; }
    let viewer = boot.viewer || {};
    let status = boot.status || null;
    let serverPos = 0, serverAt = 0, paused = true, duration = 0, isStream = false;
    let pending = 0;            // control calls in flight: polls do not overwrite optimistic state
    let seeking = false, seekDraft = 0;
    let volTouched = 0, lastVol = 70;
    const liked = new Map($$('#mapp-liked .mapp-row').map((r) => [r.dataset.uri, r.dataset.title]));
    const likedMeta = new Map($$('#mapp-liked .mapp-row').map((r) => [r.dataset.uri, { artwork: r.dataset.artwork || '', author: r.dataset.author || '' }]));
    const playlists = []; // [{id, name, track_count, thumbnail, cover}] from /account/music
    const busy = new Set();
    const np = () => (status && status.now_playing) || null;
    const hasGuild = !!GID;
    const canControl = () => !!viewer.can_control;
    const canDestroy = () => !!viewer.can_destroy;
    const isManager = () => !!viewer.is_manager;
    const DJ_ONLY = ['shuffle', 'back', 'skip', 'loop'];
    /* Mirrors MusicAppTemplate::role_label. */
    const roleLabel = (r) => (r === 'dj' ? 'DJ' : r ? r[0].toUpperCase() + r.slice(1) : 'Visitor');

    // ── Role gating (Percy decides; this only words it) ──────────────────
    function lockHint() {
        if (!hasGuild) return 'Pick a server first';
        if (viewer.dj_mode === 1 && !viewer.is_manager) return 'Only DJs can control the player';
        const ch = viewer.bot_channel && viewer.bot_channel.name;
        return ch ? `Join #${ch} to control` : "Join Percy's voice channel to control";
    }
    function queueHint() {
        if (!hasGuild) return 'Pick a server first';
        if (viewer.can_control || viewer.can_start) return '';
        if (status && status.active) return lockHint();
        if (viewer.dj_mode === 1 && !viewer.is_manager) return 'Only DJs can start the player';
        return 'Join a voice channel in this server to start a session';
    }
    function ctlHint(act) {
        if (!canControl()) return lockHint();
        if (!np()) return 'Nothing is playing';
        if (DJ_ONLY.includes(act) && !canDestroy()) return 'Needs the DJ role';
        return '';
    }
    function applyRole() {
        $$('.mapp-ctl[data-act]').forEach((b) => {
            const why = ctlHint(b.dataset.act);
            b.disabled = !!why;
            b.title = why || b.getAttribute('aria-label');
        });
        const volWhy = ctlHint('volume');
        const vol = $('#mapp-volume'), mute = $('[data-act="mute"]');
        if (vol) { vol.disabled = !!volWhy; vol.title = volWhy || 'Volume'; }
        if (mute) { mute.disabled = !!volWhy; mute.title = volWhy || 'Mute (M)'; }
        const seek = $('#mapp-seek');
        if (seek) {
            const off = !!ctlHint('seek') || isStream;
            seek.classList.toggle('is-off', off);
            seek.classList.toggle('is-live', isStream);
            seek.title = isStream ? 'Live stream' : ctlHint('seek');
            seek.setAttribute('aria-disabled', String(off));
        }
        const qh = queueHint();
        $$('[data-act="add"], [data-act="playnow"]').forEach((b) => {
            b.disabled = !!qh;
            b.title = qh || (b.dataset.act === 'add' ? 'Add to queue' : 'Play now');
        });
        const start = $('[data-act="start"]');
        if (start) start.disabled = !!qh;
        const stop = $('[data-act="stop"]');
        if (stop) stop.hidden = !canDestroy() || !np();
        const chip = $('#mapp-role');
        if (chip && viewer.role) { chip.textContent = roleLabel(viewer.role); chip.dataset.role = viewer.role; }
    }
    /* What the Sound panel (music/sound.js) borrows; it never reaches into app state directly. */
    window.mapp = { api, toast, ico, esc, BASE, hasGuild, lockHint, viewer: () => viewer, status: () => status };

    // ── Rendering: banner, now playing, queue, bar ───────────────────────
    let keys = {};
    const changed = (k, v) => { const s = JSON.stringify(v); if (keys[k] === s) return false; keys[k] = s; return true; };

    function banner() {
        if (!hasGuild) return null;
        if (!status) return { kind: 'danger', icon: 'wifi-off', title: 'Percy is offline', text: 'The player for this server cannot be reached right now. We will keep retrying.', cta: '' };
        const v = viewer;
        if (!(status.active && status.now_playing)) {
            if (v.can_start) {
                const ch = v.voice_channel ? `#${v.voice_channel.name}` : 'a voice channel';
                return { kind: 'accent', icon: 'headphones', title: `You are in ${ch}`, text: 'Nothing is playing. Search for a song to start a session here.', cta: 'Start session' };
            }
            return { kind: 'info', icon: 'voice', title: 'Nothing is playing', text: 'Join a voice channel in this server, then start a session from here.', cta: '' };
        }
        if (!v.can_control) {
            let text;
            if (v.dj_mode === 1 && !v.is_manager) text = 'This server is in DJ-only mode, so only DJs can steer the player.';
            else if (v.bot_channel) text = `Join #${v.bot_channel.name} in Discord to control playback and add songs.`;
            else text = "Join Percy's voice channel to control playback.";
            return { kind: 'info', icon: 'headphones', title: 'You are listening in', text, cta: '' };
        }
        return null;
    }
    function renderBanner() {
        const box = $('#mapp-banner');
        const b = banner();
        if (!box || !changed('banner', b)) return;
        box.innerHTML = b ? `<div class="mapp-banner is-${b.kind}">
            <span class="mapp-banner-ico">${ico(b.icon)}</span>
            <div class="mapp-banner-text"><b>${esc(b.title)}</b><span>${esc(b.text)}</span></div>
            ${b.cta ? `<button type="button" class="mapp-btn mapp-btn--accent" data-act="start">${ico('play')}${esc(b.cta)}</button>` : ''}
        </div>` : '';
    }

    const likeBtn = (t, extra) => `<button type="button" class="mapp-ibtn mapp-like" data-act="like"${extra || ''} aria-pressed="false" aria-label="Like ${esc(t.title)}" title="Like">${ico('heart')}</button>`;

    function renderNp() {
        const box = $('#mapp-np');
        const t = np();
        const key = t ? [t.title, t.author, t.uri, t.artwork, t.artist_url, t.album && t.album.url, paused, status.channel_name, status.listeners, t.requester && t.requester.name] : [!!status, hasGuild];
        if (!box || !changed('np', key)) return;
        if (!t) {
            box.innerHTML = `<div class="mapp-np">
                <span class="mapp-art mapp-art--np mapp-art--idle" aria-hidden="true">${ico('disc')}</span>
                <div class="mapp-np-text">
                    <p class="mapp-np-label">Now playing</p>
                    <p class="mapp-np-title">${hasGuild ? (status ? 'Nothing playing' : 'Percy is offline') : 'No server picked'}</p>
                    <p class="mapp-np-sub">${hasGuild ? (status ? 'Start a song to see it here' : 'Retrying in the background') : 'Pick a server above'}</p>
                </div>
            </div>`;
            return;
        }
        const req = t.requester && t.requester.name;
        box.innerHTML = `<div class="mapp-np-cover">${art(t.artwork, t.title, 'mapp-art--np mapp-art--fill')}</div>
            <div class="mapp-np">
                <div class="mapp-np-text">
                    <p class="mapp-np-label">${paused ? 'Paused' : 'Now playing'}</p>
                    <p class="mapp-np-title" title="${esc(t.title)}">${esc(t.title)}</p>
                    <p class="mapp-np-sub">${subHtml(t, true)}</p>
                </div>
                ${likeBtn(t, ' data-np')}
            </div>
            <div class="mapp-np-facts">
                ${status.channel_name ? `<span class="mapp-fact">${ico('voice')}#${esc(status.channel_name)}</span>` : ''}
                <span class="mapp-fact">${ico('users')}${status.listeners || 0} listening</span>
                ${req ? `<span class="mapp-fact" title="Requested by ${esc(req)}">${ico('user')}${esc(req)}</span>` : ''}
            </div>`;
    }

    function renderQueue() {
        const box = $('#mapp-queue');
        const list = (status && status.queue) || [];
        const mgr = isManager();
        if (!box || !changed('queue', [list.map((t) => [t.title, t.uri, t.autoplay, t.artwork, t.artist_url]), mgr, !!status, hasGuild])) return;
        const count = $('#mapp-queue-count');
        if (count) count.textContent = list.length ? `${list.length} track${list.length === 1 ? '' : 's'}` : '';
        if (!status) {
            box.innerHTML = empty('queue', hasGuild ? 'Queue unavailable' : 'No queue yet', hasGuild ? 'Percy could not be reached for this server.' : 'Pick a server to see what is lined up.', '', 'sm');
            return;
        }
        if (!list.length) {
            box.innerHTML = empty('queue', 'Queue is empty', 'Add songs from search or your library and they line up here.',
                '<a class="mapp-btn" href="#search" data-focus-search>Search</a><a class="mapp-btn mapp-btn--ghost" href="#liked">Library</a>', 'sm');
            return;
        }
        let html = '', sawManual = false, sawAuto = false;
        list.slice(0, 50).forEach((t, i) => {
            if (!t.autoplay && !sawManual) { html += '<li class="mapp-qlabel">Next up</li>'; sawManual = true; }
            if (t.autoplay && !sawAuto) { html += '<li class="mapp-qlabel">Autoplay picks</li>'; sawAuto = true; }
            const edit = mgr && !t.autoplay;
            html += `<li class="mapp-qrow${t.autoplay ? ' is-auto' : ''}" data-index="${i}" data-uri="${esc(t.uri || '')}" data-title="${esc(t.title)}" data-author="${esc(t.author)}" data-artwork="${esc(t.artwork || '')}"${edit ? ' data-reorder' : ''} draggable="true">
                ${edit ? `<span class="mapp-grip" title="Drag to reorder" aria-hidden="true">${ico('grip')}</span>` : ''}
                ${art(t.artwork, t.title, 'mapp-art--xs')}
                <span class="mapp-row-text"><span class="mapp-row-title">${esc(t.title)}</span><span class="mapp-row-sub">${authorHtml(t.author, t.artist_url)}</span></span>
                ${t.autoplay ? '<span class="mapp-tag">Autoplay</span>' : ''}
                <span class="mapp-row-dur">${t.is_stream ? 'LIVE' : fmt(t.duration)}</span>
                ${edit ? `<button type="button" class="mapp-ibtn mapp-q-remove" data-act="qremove" aria-label="Remove ${esc(t.title)} from the queue" title="Remove from queue">${ico('x')}</button>` : ''}
            </li>`;
        });
        box.innerHTML = `<ol class="mapp-qlist">${html}</ol>`;
    }

    function renderBarNow() {
        const box = $('#mapp-bar-now');
        const t = np();
        if (!box || !changed('bar', t ? [t.title, t.author, t.artwork, t.uri, t.artist_url] : [hasGuild])) return;
        box.innerHTML = t
            ? `${art(t.artwork, t.title, 'mapp-art--md')}<div class="mapp-bar-text"><p class="mapp-bar-title" title="${esc(t.title)}">${esc(t.title)}</p><p class="mapp-bar-sub">${authorHtml(t.author, t.artist_url)}</p></div>${likeBtn(t, ' data-np')}`
            : `<span class="mapp-art mapp-art--md mapp-art--idle" aria-hidden="true">${ico('disc')}</span><div class="mapp-bar-text"><p class="mapp-bar-title">Nothing playing</p><p class="mapp-bar-sub">${hasGuild ? 'Pick a song to get started' : 'Pick a server first'}</p></div>`;
        box.classList.remove('is-swap'); void box.offsetWidth; box.classList.add('is-swap');
        document.title = t ? `${t.title} · ${t.author} | Percy Music` : 'Music | Percy';
    }

    const LOOP_LABEL = ['Repeat: off', 'Repeat: this track', 'Repeat: queue'];
    function renderTransport() {
        const t = np();
        const tog = $('[data-act="toggle"]');
        if (tog) {
            const showPause = !!t && !paused;
            tog.innerHTML = ico(showPause ? 'pause' : 'play');
            tog.setAttribute('aria-label', showPause ? 'Pause' : 'Play');
        }
        const sh = $('[data-act="shuffle"]');
        if (sh) sh.setAttribute('aria-pressed', String(!!(t && t.shuffle)));
        const lp = $('[data-act="loop"]');
        if (lp) {
            const m = (t && t.loop) || 0;
            lp.innerHTML = ico(m === 1 ? 'repeat-one' : 'repeat');
            lp.setAttribute('aria-pressed', String(m !== 0));
            lp.setAttribute('aria-label', LOOP_LABEL[m] || LOOP_LABEL[0]);
        }
        const total = $('#mapp-total');
        if (total) total.textContent = !t ? '0:00' : isStream ? 'LIVE' : fmt(duration);
        const vol = $('#mapp-volume');
        if (vol && t && performance.now() - volTouched > 2000) { vol.value = t.volume; paintVol(); }
        if (t && t.volume) lastVol = t.volume;
    }

    function syncHearts() {
        $$('.mapp-like').forEach((b) => {
            const t = trackOf(b);
            b.hidden = !t.uri;
            const on = !!t.uri && liked.has(t.uri);
            b.setAttribute('aria-pressed', String(on));
            b.innerHTML = ico(on ? 'heart-fill' : 'heart');
            b.setAttribute('aria-label', `${on ? 'Unlike' : 'Like'} ${t.title || 'this track'}`);
            b.title = on ? 'Remove from Liked Songs' : 'Save to Liked Songs';
        });
        const cur = np() && np().uri;
        $$('.mapp-row').forEach((r) => r.classList.toggle('is-current', !!cur && r.dataset.uri === cur));
    }

    function renderAll() {
        renderBanner(); renderNp(); renderQueue(); renderBarNow(); renderTransport();
        applyRole(); syncHearts();
        document.dispatchEvent(new CustomEvent('mapp:status'));
    }

    function applyStatus(d) {
        const prev = np() ? `${np().title}|${np().uri}` : '';
        status = d;
        const t = d.now_playing;
        serverPos = t ? t.position : 0;
        serverAt = performance.now();
        paused = !t || !!t.paused;
        duration = t ? t.duration : 0;
        isStream = !!(t && t.is_stream);
        renderAll();
        const now = t ? `${t.title}|${t.uri}` : '';
        if (now !== prev) {
            refreshHistory();
            if (currentView === 'lyrics') loadLyrics(true);
        }
    }

    // ── Poll + connection banner ─────────────────────────────────────────
    let failures = 0, pollTimer = 0, polling = false;
    const conn = $('#mapp-conn');
    async function poll() {
        clearTimeout(pollTimer);
        if (!hasGuild || polling) return;
        if (document.hidden) return; // resumed on visibilitychange
        polling = true;
        const [s, v] = await Promise.all([api(`${BASE}/status`), api(`${BASE}/viewer`)]);
        polling = false;
        const ok = s.ok && v.ok;
        if (ok) {
            if (failures) toast('success', 'Reconnected to Percy');
            failures = 0;
        } else failures++;
        if (conn) conn.hidden = ok;
        if (v.ok) viewer = v.data;
        if (s.ok && !pending) applyStatus(s.data); else renderAll();
        pollTimer = setTimeout(poll, ok ? 5000 : Math.min(30000, 5000 * 2 ** Math.min(failures - 1, 3)));
    }
    const soon = (ms) => { clearTimeout(pollTimer); pollTimer = setTimeout(poll, ms || 500); };
    document.addEventListener('visibilitychange', () => { if (!document.hidden) soon(50); });

    // ── Progress interpolation (rAF) ─────────────────────────────────────
    const pos = () => Math.min(duration || Infinity, paused ? serverPos : serverPos + (performance.now() - serverAt));
    const fill = $('#mapp-seek-fill'), seekEl = $('#mapp-seek'), elapsed = $('#mapp-elapsed');
    let lastSec = -1;
    function paintSeek(ms) {
        const p = duration && !isStream ? Math.max(0, Math.min(100, (ms / duration) * 100)) : 0;
        if (fill) fill.style.width = `${p}%`;
        if (seekEl) {
            seekEl.style.setProperty('--p', `${p}%`);
            const sec = Math.floor(ms / 1000);
            if (sec !== lastSec) {
                lastSec = sec;
                seekEl.setAttribute('aria-valuenow', String(Math.round(p)));
                seekEl.setAttribute('aria-valuetext', `${fmt(ms)} of ${fmt(duration)}`);
                if (elapsed) elapsed.textContent = np() ? (isStream ? '' : fmt(ms)) : '0:00';
            }
        }
    }
    function frame() {
        if (!seeking) paintSeek(np() ? pos() : 0);
        highlightLyric();
        requestAnimationFrame(frame);
    }

    // ── Controls ─────────────────────────────────────────────────────────
    async function control(action, extra, opt) {
        const o = opt || {};
        pending++;
        const r = await api(`${BASE}/control`, { method: 'POST', body: Object.assign({ action }, extra || {}) });
        pending--;
        if (!r.ok) {
            if (o.rollback) o.rollback();
            toast('error', r.error);
            renderAll();
            return false;
        }
        if (o.done) toast('success', o.done);
        soon(450);
        return true;
    }
    async function once(key, btn, fn) {
        if (busy.has(key)) return;
        busy.add(key);
        if (btn) btn.classList.add('is-busy');
        try { await fn(); } finally { busy.delete(key); if (btn) btn.classList.remove('is-busy'); }
    }
    function blocked(act) {
        const why = act === 'add' || act === 'playnow' ? queueHint() : ctlHint(act);
        if (why) toast('info', why);
        return !!why;
    }

    function togglePlay() {
        if (blocked('toggle')) return;
        const t = np();
        const wasPaused = paused, wasPos = pos();
        serverPos = wasPos; serverAt = performance.now();
        paused = !wasPaused; t.paused = paused;
        renderTransport();
        once('toggle', $('[data-act="toggle"]'), () => control(wasPaused ? 'resume' : 'pause', null, {
            rollback: () => { paused = wasPaused; t.paused = wasPaused; serverPos = wasPos; serverAt = performance.now(); },
        }));
    }
    function seekTo(ms) {
        if (blocked('seek') || isStream || !duration) return;
        const before = pos();
        serverPos = Math.max(0, Math.min(duration, Math.round(ms))); serverAt = performance.now();
        paintSeek(serverPos);
        control('seek', { position: serverPos }, { rollback: () => { serverPos = before; serverAt = performance.now(); } });
    }
    const seekBy = (d) => seekTo(pos() + d);

    // Volume: the slider repaints at once; one request per settle, rolled back on refusal.
    const volEl = $('#mapp-volume');
    function paintVol() {
        if (!volEl) return;
        volEl.style.setProperty('--v', `${volEl.value}%`);
        const m = $('[data-act="mute"]');
        const v = +volEl.value;
        if (m) { m.innerHTML = ico(v === 0 ? 'volume-x' : v < 45 ? 'volume-low' : 'volume'); m.setAttribute('aria-label', v === 0 ? 'Unmute' : 'Mute'); }
    }
    let volTimer = 0, volCommitted = volEl ? +volEl.value : 70;
    function setVolume(v, immediate) {
        if (!volEl || blocked('volume')) return;
        volEl.value = v; paintVol(); volTouched = performance.now();
        clearTimeout(volTimer);
        volTimer = setTimeout(async () => {
            const prev = volCommitted, value = +volEl.value;
            volCommitted = value;
            const ok = await control('volume', { value }, { rollback: () => { volEl.value = prev; volCommitted = prev; paintVol(); } });
            if (ok && np()) np().volume = value;
        }, immediate ? 0 : 250);
    }
    function toggleMute() {
        if (!volEl) return;
        const v = +volEl.value;
        if (v > 0) { lastVol = v; setVolume(0, true); toast('info', 'Muted'); } else setVolume(lastVol || 50, true);
    }
    if (volEl) {
        paintVol();
        volEl.addEventListener('input', () => setVolume(+volEl.value));
    }

    // Seek bar: click, drag, hover time.
    if (seekEl) {
        const tip = $('#mapp-seek-tip');
        const ratio = (e) => { const r = seekEl.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); };
        const off = () => seekEl.classList.contains('is-off') || !duration;
        const showTip = (e) => {
            if (!tip || off()) return;
            const r = ratio(e);
            tip.hidden = false; tip.style.left = `${r * 100}%`; tip.textContent = fmt(r * duration);
        };
        seekEl.addEventListener('pointerdown', (e) => {
            if (off()) { if (!isStream && np()) blocked('seek'); return; }
            seeking = true; seekEl.setPointerCapture(e.pointerId); seekEl.classList.add('is-drag');
            seekDraft = ratio(e) * duration; paintSeek(seekDraft); showTip(e);
        });
        seekEl.addEventListener('pointermove', (e) => {
            showTip(e);
            if (!seeking) return;
            seekDraft = ratio(e) * duration; paintSeek(seekDraft);
        });
        const end = () => {
            if (!seeking) return;
            seeking = false; seekEl.classList.remove('is-drag');
            if (tip) tip.hidden = true;
            seekTo(seekDraft);
        };
        seekEl.addEventListener('pointerup', end);
        seekEl.addEventListener('pointercancel', () => { seeking = false; seekEl.classList.remove('is-drag'); });
        seekEl.addEventListener('pointerleave', () => { if (!seeking && tip) tip.hidden = true; });
    }

    // ── Tracks: play, queue, like ────────────────────────────────────────
    function trackOf(el) {
        if (el.hasAttribute('data-np')) { const t = np() || {}; return { uri: t.uri || '', title: t.title || '', author: t.author || '', artwork: t.artwork || '', artistUrl: t.artist_url || '', albumUrl: (t.album && t.album.url) || '', albumName: (t.album && t.album.name) || '' }; }
        const row = el.closest('[data-uri]');
        return row ? { uri: row.dataset.uri || '', title: row.dataset.title || '', author: row.dataset.author || '', artwork: row.dataset.artwork || '', artistUrl: row.dataset.artistUrl || '', albumUrl: row.dataset.albumUrl || '', albumName: row.dataset.albumName || '' } : { uri: '', title: '', author: '' };
    }

    async function playTrack(t, now) {
        if (blocked(now ? 'playnow' : 'add')) return;
        const query = t.query || t.uri || [t.title, t.author].filter(Boolean).join(' ');
        if (!query) { toast('error', 'This track has no link to play.'); return; }
        const r = await api(`${BASE}/play`, { method: 'POST', body: t.query ? { query, limit: 15 } : now && canDestroy() ? { query, play_now: true } : { query } }); // vibes queue a mix
        if (!r.ok) { toast('error', r.error); return; }
        if (t.query) {
            const n = (r.data.queued || []).length;
            toast('success', `Queued ${n} song${n === 1 ? '' : 's'} from ${q(t.label)}`);
            soon(400); return;
        }
        const found = r.data.queued && r.data.queued[0] && r.data.queued[0].title;
        const name = (t.query ? found || `${t.label} mix` : t.title || found) || 'that track';
        if (r.data.started || (now && canDestroy())) { toast('success', `Now playing ${q(name)}`); soon(400); return; }
        if (now) {
            toast('info', `Added ${q(name)} to the queue. Skipping ahead needs the DJ role.`);
        } else {
            const extra = (r.data.queued || []).length > 1 ? ` (+${r.data.queued.length - 1} more)` : '';
            toast('success', `Added ${q(name)} to the queue${extra}`);
        }
        soon(400);
    }

    async function toggleLike(btn) {
        const t = trackOf(btn);
        if (!t.uri) { toast('info', 'This track cannot be saved: it has no link.'); return; }
        await once(`like:${t.uri}`, null, async () => {
            const on = liked.has(t.uri);
            const name = on ? liked.get(t.uri) || t.title : t.title;
            if (on) liked.delete(t.uri); else { liked.set(t.uri, name); likedMeta.set(t.uri, { artwork: t.artwork || '', author: t.author || '' }); }
            syncHearts();
            $$('.mapp-like').forEach((b) => { if (trackOf(b).uri === t.uri) { b.classList.remove('is-bump'); void b.offsetWidth; b.classList.add('is-bump'); } });
            const r = await api(on ? '/account/music/liked/remove' : '/account/music/liked', { method: 'POST', body: on ? { url: t.uri } : { name, url: t.uri, author: t.author || null, artwork: t.artwork || null } });
            if (!r.ok) {
                if (on) liked.set(t.uri, name); else liked.delete(t.uri);
                syncHearts();
                toast('error', r.error);
                return;
            }
            toast('success', on ? `Removed ${q(name)} from Liked Songs` : `Saved ${q(name)} to Liked Songs`);
            renderLiked();
        });
    }

    /* Save to Liked Songs (a drop onto it): never unlikes, unlike the heart. */
    async function likeTrack(t) {
        if (!t.uri) { toast('info', 'This track cannot be saved: it has no link.'); return; }
        if (liked.has(t.uri)) { toast('info', `${q(t.title)} is already in Liked Songs`); return; }
        await once(`like:${t.uri}`, null, async () => {
            liked.set(t.uri, t.title); likedMeta.set(t.uri, { artwork: t.artwork || '', author: t.author || '' });
            syncHearts();
            const r = await api('/account/music/liked', { method: 'POST', body: { name: t.title, url: t.uri, author: t.author || null, artwork: t.artwork || null } });
            if (!r.ok) { liked.delete(t.uri); syncHearts(); toast('error', r.error); return; }
            toast('success', `Saved ${q(t.title)} to Liked Songs`);
            renderLiked();
        });
    }

    // ── Lists: history, recents, liked, playlist, search ────────────────
    function empty(icon, title, text, actions, size, isError) {
        return `<div class="mapp-empty${size ? ' mapp-empty--' + size : ''}${isError ? ' is-error' : ''}">
            <span class="mapp-empty-ico">${ico(icon)}</span><h3>${esc(title)}</h3><p>${esc(text)}</p>
            ${actions ? `<div class="mapp-empty-acts">${actions}</div>` : ''}</div>`;
    }
    const skeleton = (n) => `<div class="mapp-skel" aria-hidden="true">${Array.from({ length: n || 6 }, () => '<div class="mapp-skel-row"><i></i><span><i></i><i></i></span><i></i></div>').join('')}</div>`;

    /* t: {uri, title, author, artwork, ms, meta, stream, likedIcon} */
    function rowHtml(t, i) {
        const ttl = esc(t.title);
        return `<li class="mapp-row"${t.uri ? ' draggable="true"' : ''} data-uri="${esc(t.uri || '')}" data-title="${ttl}" data-author="${esc(t.author || '')}" data-artwork="${esc(t.artwork || '')}"${t.artistUrl ? ` data-artist-url="${esc(t.artistUrl)}"` : ''}${t.album && t.album.url ? ` data-album-url="${esc(t.album.url)}" data-album-name="${esc(t.album.name)}"` : ''}>
            <span class="mapp-row-idx"><span>${i + 1}</span><button type="button" data-act="playnow" aria-label="Play ${ttl} now" title="Play now">${ico('play')}</button></span>
            ${t.likedIcon ? `<span class="mapp-art mapp-art--sm g${grad(t.title)}" aria-hidden="true">${ico('heart-fill')}</span>` : art(t.artwork, t.title, 'mapp-art--sm')}
            <span class="mapp-row-text"><span class="mapp-row-title">${ttl}</span><span class="mapp-row-sub">${t.author ? subHtml(t, t.withAlbum) : esc(t.sub || 'Unknown artist')}</span></span>
            <span class="mapp-row-meta">${t.meta || ''}</span>
            <span class="mapp-row-acts">
                <button type="button" class="mapp-ibtn" data-act="add" aria-label="Add ${ttl} to queue" title="Add to queue">${ico('plus')}</button>
                ${t.uri ? `<button type="button" class="mapp-ibtn" data-d="more" aria-haspopup="menu" aria-label="More options for ${ttl}" title="More">${ico('more')}</button>` : ''}
                ${likeBtn(t)}
            </span>
            <span class="mapp-row-dur">${t.stream ? 'LIVE' : t.ms ? fmt(t.ms) : ''}</span>
        </li>`;
    }
    function cardHtml(t) {
        const ttl = esc(t.title);
        return `<div class="mapp-card"${t.uri ? ' draggable="true"' : ''} data-uri="${esc(t.uri || '')}" data-title="${ttl}" data-author="${esc(t.author || '')}" data-artwork="${esc(t.artwork || '')}">
            <div class="mapp-card-art">${art(t.artwork, t.title, 'mapp-art--fill')}
                ${t.when ? `<span class="mapp-card-badge">${ico('history')}<time datetime="${esc(t.when)}">${esc(ago(t.when))}</time></span>` : ''}
                ${t.rank ? `<span class="mapp-card-badge mapp-card-rank" title="${esc(t.rankNote || '')}">#${t.rank}</span>` : ''}
                <div class="mapp-card-hover">
                    <button type="button" class="mapp-card-play" data-act="playnow" aria-label="Play ${ttl} now" title="Play now">${ico('play')}</button>
                    <button type="button" class="mapp-card-add" data-act="add" aria-label="Add ${ttl} to queue" title="Add to queue">${ico('plus')}</button>
                </div>
            </div>
            <p class="mapp-card-title" title="${ttl}">${ttl}</p><p class="mapp-card-sub">${authorHtml(t.author, t.artistUrl)}</p>
        </div>`;
    }
    const fromHistory = (h) => ({ uri: h.uri, title: h.title, author: h.author, artwork: h.artwork, ms: h.length_ms, when: h.played_at, meta: `<time datetime="${esc(h.played_at)}">${esc(ago(h.played_at))}</time>` });

    // Newest play of each track (history is newest first): a song on repeat shows once.
    function uniquePlays(items) {
        const seen = new Set();
        return items.filter((h) => {
            const k = h.uri || JSON.stringify([(h.title || '').toLowerCase(), (h.author || '').toLowerCase()]);
            if (seen.has(k)) return false;
            seen.add(k);
            return true;
        });
    }
    let historyLoaded = false;
    async function refreshHistory() {
        if (!hasGuild) return;
        const box = $('#mapp-history');
        if (box && currentView === 'history' && !historyLoaded) box.innerHTML = skeleton(8);
        const r = await api(`${BASE}/history?limit=50`);
        if (!r.ok) {
            if (box && currentView === 'history' && !historyLoaded) box.innerHTML = empty('alert', 'Could not load history', r.error, '<button type="button" class="mapp-btn" data-act="retry-history">Try again</button>', '', true);
            return;
        }
        historyLoaded = true;
        const items = r.data.items || [];
        if (!changed('history', items.map((h) => [h.title, h.played_at]))) return;
        if (box) {
            box.innerHTML = items.length
                ? `<ol class="mapp-rows">${items.map((h, i) => rowHtml(fromHistory(h), i)).join('')}</ol>`
                : empty('history', 'No history yet', 'Nothing has been played in this server. Start a session and it fills up.');
        }
        const rec = $('#mapp-recents');
        if (rec) {
            rec.innerHTML = items.length
                ? uniquePlays(items).slice(0, 12).map((h) => cardHtml(fromHistory(h))).join('')
                : empty('history', 'Nothing played here yet', 'Songs played in this server will show up here.');
        }
        applyRole(); syncHearts();
    }

    function renderLiked() {
        const box = $('#mapp-liked');
        if (!box) return;
        const items = Array.from(liked, ([url, name]) => {
            const m = likedMeta.get(url) || {};
            return { uri: url, title: name || url, author: m.author || '', artwork: m.artwork || '', likedIcon: !m.artwork };
        });
        box.innerHTML = items.length
            ? `<ol class="mapp-rows">${items.map(rowHtml).join('')}</ol>`
            : empty('heart', 'Songs you like appear here', 'Tap the heart on any track (in the player, your history or search) to save it.');
        const n = items.length;
        const c = $('#mapp-liked-count'); if (c) c.textContent = String(n);
        const s = $('#mapp-liked-sum'); if (s) s.textContent = `${n} song${n === 1 ? '' : 's'}`;
        applyRole(); syncHearts();
        document.dispatchEvent(new CustomEvent('mapp:liked'));
    }

    async function refreshLibrary() {
        const r = await api('/account/music');
        if (!r.ok || !r.data.library) return;
        liked.clear();
        likedMeta.clear();
        (r.data.library.liked || []).forEach((t) => { liked.set(t.url, t.name); likedMeta.set(t.url, { artwork: t.artwork || '', author: t.author || '' }); });
        renderLiked();
        playlists.splice(0, playlists.length, ...(r.data.library.playlists || []));
        renderPlaylists();
    }

    function renderPlaylists() {
        const c = $('#mapp-playlists-count'); if (c) c.textContent = String(playlists.length);
        const nav = $('#mapp-nav-playlists');
        if (nav) {
            nav.innerHTML = playlists.map((p) => `<a href="#playlist/${esc(p.id)}" class="mapp-nav-sub" data-drop-pl="${esc(p.id)}" title="${esc(p.name)}">${esc(p.name)}</a>`).join('');
            nav.hidden = !playlists.length;
        }
        const box = $('#mapp-playlists');
        if (!box) return;
        box.innerHTML = playlists.length
            ? `<div class="mapp-grid">${playlists.map((p) => `<a class="mapp-card mapp-card--link" href="#playlist/${esc(p.id)}">
                <div class="mapp-card-art">${p.cover ? art(p.cover, p.name, 'mapp-art--fill') : `<span class="mapp-art mapp-art--fill g${grad(p.name)}" aria-hidden="true">${ico('playlist')}</span>`}</div>
                <p class="mapp-card-title" title="${esc(p.name)}">${esc(p.name)}</p>
                <p class="mapp-card-sub">${p.track_count} track${p.track_count === 1 ? '' : 's'}</p></a>`).join('')}</div>`
            : empty('playlist', 'No playlists yet', 'Create one with New playlist, or in Discord with /playlist create.');
    }

    // Search: debounced, newest answer wins.
    const input = $('#mapp-search'), results = $('#mapp-results'), clearBtn = $('#mapp-search-clear');
    let searchSeq = 0;
    function searchIdle() {
        if (results) results.innerHTML = empty('search', 'Find something to play', 'Type a song, an artist or paste a link in the search bar above.');
    }
    // Session-local search cache (10 min) so retyping a query does not refetch; failures are never kept.
    const searchCache = new Map();
    async function cachedSearch(query) {
        const key = query.toLowerCase();
        const hit = searchCache.get(key);
        if (hit && Date.now() - hit.at < 600000) return hit.r;
        const r = await api(`${BASE}/search?q=${encodeURIComponent(query)}&limit=20`);
        if (r.ok) {
            if (searchCache.size >= 50) searchCache.delete(searchCache.keys().next().value);
            searchCache.set(key, { at: Date.now(), r });
        }
        return r;
    }
    async function runSearch(text) {
        const query = text.trim();
        const mine = ++searchSeq;
        if (!query) { searchIdle(); return; }
        if (results) results.innerHTML = skeleton(7);
        const r = await cachedSearch(query);
        if (mine !== searchSeq || !results) return;
        if (!r.ok) {
            results.innerHTML = empty('alert', 'Search failed', r.error, '<button type="button" class="mapp-btn" data-act="retry-search">Try again</button>', '', true);
            return;
        }
        const list = r.data.results || [];
        results.innerHTML = list.length
            ? `<div class="mapp-shelf-head"><h2>Songs</h2><span class="mapp-row-meta">${list.length} result${list.length === 1 ? '' : 's'} for ${esc(q(query))}</span></div>
               <ol class="mapp-rows">${list.map((t, i) => rowHtml({ uri: t.uri, title: t.title, author: t.author, artwork: t.artwork, ms: t.duration, stream: t.is_stream, meta: esc(t.source || ''), artistUrl: t.artist_url, album: t.album, withAlbum: true }, i)).join('')}</ol>`
            : empty('search', `No results for ${q(query)}`, 'Check the spelling, try fewer words, or paste a link instead.');
        applyRole(); syncHearts();
    }
    // Suggestions under the search field: recent searches, library matches and vibes. Typing only
    // filters these locally; Enter (or picking a search) runs the live search view.
    const suggest = $('#mapp-suggest');
    const RECENT_KEY = 'mapp-recent-searches';
    const vibes = boot.vibes || [];
    function recent() {
        try { const v = JSON.parse(store.get(RECENT_KEY) || '[]'); return Array.isArray(v) ? v.filter((s) => typeof s === 'string') : []; } catch (_) { return []; }
    }
    function remember(text) {
        const s = text.trim();
        if (s.length < 2) return;
        store.set(RECENT_KEY, JSON.stringify([s, ...recent().filter((r) => r.toLowerCase() !== s.toLowerCase())].slice(0, 8)));
    }
    function libraryTracks() {
        const seen = new Set(), out = [];
        const add = (title, author, uri) => { const k = (title || '').toLowerCase(); if (k && !seen.has(k)) { seen.add(k); out.push({ title, author: author || '', uri: uri || '' }); } };
        $$('.mapp-row[data-title], .mapp-card[data-title]').forEach((el) => { if (!el.closest('#mapp-results, #mapp-recs')) add(el.dataset.title, el.dataset.author, el.dataset.uri); });
        liked.forEach((name, url) => add(name, '', url));
        return out;
    }
    let sugItems = [], sugActive = -1;
    function setSuggestOpen(open) {
        if (!suggest) return;
        suggest.hidden = !open;
        input.setAttribute('aria-expanded', String(open));
        if (!open) { sugActive = -1; input.removeAttribute('aria-activedescendant'); }
    }
    function buildSuggest() {
        if (!suggest || input.disabled) return;
        const raw = input.value.trim(), qv = raw.toLowerCase();
        const rec = recent().filter((r) => !qv || r.toLowerCase().includes(qv)).slice(0, qv ? 4 : 6);
        const tracks = qv ? libraryTracks().filter((t) => `${t.title} ${t.author}`.toLowerCase().includes(qv)).slice(0, 5) : [];
        const vb = vibes.filter((v) => !qv || v.label.toLowerCase().includes(qv) || v.query.includes(qv)).slice(0, qv ? 4 : 10);
        sugItems = [];
        const opt = (item, inner) => {
            const i = sugItems.push(item) - 1;
            return `<div class="mapp-sug${item.kind === 'vibe' ? ' mapp-sug--vibe' : ''}" role="option" id="mapp-sug-${i}" data-sug="${i}" aria-selected="false">${inner}</div>`;
        };
        let html = '';
        if (raw) html += opt({ kind: 'search', value: raw }, `${ico('search')}<span class="mapp-sug-text"><span>Search for <b>${esc(raw)}</b></span></span><kbd>Enter</kbd>`);
        if (rec.length) {
            html += '<div class="mapp-sug-head"><span>Recent searches</span><button type="button" class="mapp-sug-clear" data-sug-clear>Clear</button></div>';
            html += rec.map((r) => opt({ kind: 'recent', value: r }, `${ico('history')}<span class="mapp-sug-text">${esc(r)}</span>`)).join('');
        }
        if (tracks.length) {
            html += '<div class="mapp-sug-head"><span>From your library</span></div>';
            html += tracks.map((t) => opt({ kind: 'track', value: [t.title, t.author].filter(Boolean).join(' ') },
                `${ico('music')}<span class="mapp-sug-text">${esc(t.title)}${t.author ? `<small>${esc(t.author)}</small>` : ''}</span>`)).join('');
        }
        if (vb.length) {
            html += '<div class="mapp-sug-head"><span>Vibes</span></div><div class="mapp-sug-vibes">';
            html += vb.map((v) => opt({ kind: 'vibe', value: v.query, label: v.label },
                `<span class="mapp-sug-vibe vibe-${esc(v.key)}">${ico(v.icon)}</span><span class="mapp-sug-text">${esc(v.label)}</span>`)).join('');
            html += '</div>';
        }
        suggest.innerHTML = html;
        sugActive = -1;
        setSuggestOpen(!!html);
    }
    function moveSug(d) {
        if (!suggest || suggest.hidden || !sugItems.length) return;
        sugActive += d;
        if (sugActive < -1) sugActive = sugItems.length - 1;
        if (sugActive >= sugItems.length) sugActive = -1;
        $$('[data-sug]', suggest).forEach((el) => el.setAttribute('aria-selected', String(+el.dataset.sug === sugActive)));
        if (sugActive >= 0) {
            input.setAttribute('aria-activedescendant', `mapp-sug-${sugActive}`);
            const el = $(`#mapp-sug-${sugActive}`); if (el) el.scrollIntoView({ block: 'nearest' });
        } else input.removeAttribute('aria-activedescendant');
    }
    function submitSearch(text) {
        const s = text.trim();
        setSuggestOpen(false);
        if (!s) return;
        remember(s);
        if (input.value !== s) input.value = s;
        if (clearBtn) clearBtn.hidden = false;
        if (currentView !== 'search') go('search');
        runSearch(s);
    }
    function pickSuggestion(item) {
        if (!item) return;
        if (item.kind === 'vibe') { setSuggestOpen(false); input.blur(); playVibe(item.value, item.label); return; }
        submitSearch(item.value);
    }
    if (input) {
        input.addEventListener('input', () => { if (clearBtn) clearBtn.hidden = !input.value; buildSuggest(); });
        input.addEventListener('focus', buildSuggest);
        input.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== input) setSuggestOpen(false); }, 150));
        $('#mapp-search-form').addEventListener('submit', (e) => { e.preventDefault(); submitSearch(input.value); });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); if (suggest.hidden) buildSuggest(); else moveSug(1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); moveSug(-1); }
            else if (e.key === 'Enter' && sugActive >= 0 && !suggest.hidden) { e.preventDefault(); pickSuggestion(sugItems[sugActive]); }
            else if (e.key === 'Escape') {
                if (!suggest.hidden) { setSuggestOpen(false); return; }
                input.value = ''; if (clearBtn) clearBtn.hidden = true; input.blur();
            }
        });
    }
    if (suggest) {
        suggest.addEventListener('mousedown', (e) => e.preventDefault()); // keep focus in the field
        suggest.addEventListener('click', (e) => {
            if (e.target.closest('[data-sug-clear]')) { store.set(RECENT_KEY, '[]'); buildSuggest(); return; }
            const el = e.target.closest('[data-sug]');
            if (el) pickSuggestion(sugItems[+el.dataset.sug]);
        });
    }
    if (clearBtn) clearBtn.addEventListener('click', () => { input.value = ''; clearBtn.hidden = true; runSearch(''); input.focus(); });

    // Vibes: queue a mix when the viewer may; otherwise show what the search finds.
    function playVibe(query, label) {
        if (queueHint()) {
            input.value = query; if (clearBtn) clearBtn.hidden = false;
            go('search'); runSearch(query);
            toast('info', `${queueHint()}. Showing ${label} results instead.`);
            return;
        }
        once(`vibe:${query}`, null, () => playTrack({ query, label }, false));
    }

    // Trending: this server's and Percy's most-picked tracks of the week, fetched after first paint.
    // Hidden when both charts are empty (a new server, or nobody has queued anything yet).
    async function loadTrending() {
        const box = $('#mapp-trending-body'), sec = $('#mapp-trending');
        if (!box || !hasGuild) return;
        const r = await api(`${BASE}/trending`);
        if (!r.ok) return;
        const listeners = (n) => `${n} listener${n === 1 ? '' : 's'}`;
        const shelf = (cap, icon, list) => (list.length ? `<div class="mapp-recs-group">
                <p class="mapp-recs-cap">${ico(icon)}${cap}</p>
                <div class="mapp-shelf">${list.map((t, i) => cardHtml({ uri: t.uri, title: t.title, author: t.author, artwork: t.artwork, rank: i + 1, rankNote: listeners(t.listeners) })).join('')}</div>
            </div>` : '');
        const html = shelf('Popular in this server', 'users', r.data.server || []) + shelf('Trending on Percy', 'trend', r.data.global || []);
        if (sec) sec.hidden = !html;
        box.innerHTML = html;
        applyRole(); syncHearts();
    }

    // Recommendations: tracks by the user's top artists, fetched after first paint.
    async function loadRecs() {
        const box = $('#mapp-recs-body'), sec = $('#mapp-recs');
        if (!box || !hasGuild) return;
        const r = await api(`${BASE}/recommendations`);
        if (!r.ok) {
            box.innerHTML = empty('alert', 'Could not load recommendations', r.error, '<button type="button" class="mapp-btn" data-act="retry-recs">Try again</button>', 'inline', true);
            return;
        }
        const groups = r.data.groups || [];
        if (!groups.length) { if (sec) sec.hidden = true; return; }
        box.innerHTML = groups.map((g) => `<div class="mapp-recs-group">
                <p class="mapp-recs-cap">${ico('sparkles')}Because you listen to <b>${esc(g.artist)}</b></p>
                <div class="mapp-shelf">${(g.tracks || []).map((t) => cardHtml({ uri: t.uri, title: t.title, author: t.author, artwork: t.artwork, artistUrl: t.artist_url })).join('')}</div>
            </div>`).join('');
        applyRole(); syncHearts();
    }
    function focusSearch() { if (input && !input.disabled) { input.focus(); input.select(); } }

    // ── Lyrics ───────────────────────────────────────────────────────────
    let lyrics = null, lyricIdx = -1, lyricsFor = '', userScrolledAt = 0;
    const main = $('#mapp-main');
    ['wheel', 'touchmove'].forEach((ev) => main.addEventListener(ev, () => { userScrolledAt = performance.now(); }, { passive: true }));
    async function loadLyrics(force) {
        const box = $('#mapp-lyrics'), h = $('#mapp-h-lyrics'), view = $('[data-view="lyrics"]');
        if (!box) return;
        const t = np();
        const key = t ? `${t.title}|${t.uri}` : '';
        if (h) h.textContent = t ? t.title : 'Nothing playing';
        if (view && t) view.className = `mapp-view mapp-view--lyrics g${grad(t.title)}`;
        if (!t) { lyrics = null; lyricsFor = ''; box.innerHTML = empty('lyrics', 'Nothing is playing', 'Lyrics show up here, line by line, while a song plays.'); return; }
        if (!force && lyricsFor === key && lyrics) return;
        box.innerHTML = `<div aria-hidden="true">${[78, 62, 84, 55, 70, 66].map((w) => `<div class="mapp-skel-line" style="--w:${w}%"></div>`).join('')}</div>`;
        const r = await api(`${BASE}/lyrics`);
        if (currentView !== 'lyrics') return;
        if (!r.ok) {
            box.innerHTML = empty('alert', 'Could not load lyrics', r.error, '<button type="button" class="mapp-btn" data-act="retry-lyrics">Try again</button>', '', true);
            return;
        }
        lyrics = r.data; lyricsFor = key; lyricIdx = -1;
        const src = lyrics.source ? `<p class="mapp-lyrics-src">Lyrics provided by ${esc(lyrics.source)}</p>` : '';
        if (lyrics.has_synced && (lyrics.lines || []).length) {
            const seekable = canControl() && !isStream;
            box.innerHTML = `<div class="mapp-lyrics${seekable ? ' is-seekable' : ''}">${lyrics.lines.map((l, i) => `<button type="button" class="mapp-lyric" data-i="${i}" data-t="${l.time}"${seekable ? '' : ' tabindex="-1"'}>${esc(l.text || '♪')}</button>`).join('')}</div>${src}`;
        } else if (lyrics.plain) {
            box.innerHTML = `<div class="mapp-lyrics-plain">${esc(lyrics.plain)}</div>${src}`;
        } else {
            box.innerHTML = empty('lyrics', 'No lyrics found', 'We could not find lyrics for this track.');
        }
    }
    function highlightLyric() {
        if (currentView !== 'lyrics' || !lyrics || !lyrics.has_synced || !np()) return;
        const p = pos();
        const lines = lyrics.lines || [];
        let i = -1;
        for (let k = 0; k < lines.length && lines[k].time <= p; k++) i = k;
        if (i === lyricIdx) return;
        lyricIdx = i;
        $$('#mapp-lyrics .mapp-lyric').forEach((n, k) => { n.classList.toggle('is-on', k === i); n.classList.toggle('is-past', k < i); });
        const cur = $(`#mapp-lyrics .mapp-lyric[data-i="${i}"]`);
        if (cur && performance.now() - userScrolledAt > 2500) {
            const mr = main.getBoundingClientRect(), cr = cur.getBoundingClientRect();
            main.scrollTo({ top: main.scrollTop + cr.top - mr.top - mr.height * 0.38, behavior: 'smooth' });
        }
    }

    // ── Views (hash router) ──────────────────────────────────────────────
    const views = $$('.mapp-view').map((v) => v.dataset.view);
    let currentView = '', lastView = 'home';
    const go = (v) => { if (location.hash !== '#' + v) location.hash = v; else route(); };
    function show(v) {
        if (currentView && currentView !== v && currentView !== 'lyrics') lastView = currentView;
        currentView = v;
        $$('.mapp-view').forEach((s) => { s.hidden = s.dataset.view !== v; });
        const navKey = v === 'playlist' ? 'playlists' : v;
        $$('[data-view-link]').forEach((a) => {
            const on = a.dataset.viewLink === navKey;
            a.classList.toggle('is-on', on);
            if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
        });
        const lb = $('[data-act="lyrics"]');
        if (lb) lb.setAttribute('aria-pressed', String(v === 'lyrics'));
        main.scrollTop = 0;
    }
    /* "#view/arg?k=v": the view, one path argument and query params (artist and album links). */
    function parseHash() {
        const h = location.hash.slice(1), qi = h.indexOf('?');
        const dec = (x) => { try { return decodeURIComponent(x); } catch (_) { return x; } };
        const [raw, ...rest] = (qi < 0 ? h : h.slice(0, qi)).split('/');
        return { view: dec(raw), arg: dec(rest.join('/')), params: new URLSearchParams(qi < 0 ? '' : h.slice(qi + 1)) };
    }
    /* Browser-style back/forward buttons: the index rides in history.state, so a push is
       "previous + 1" and back/forward restore the stored one. */
    let navIdx = 0, navMax = 0, navSeen = false;
    function trackNav() {
        const st = history.state;
        if (st && typeof st.mapp === 'number') navIdx = st.mapp;
        else {
            navIdx = navSeen ? navIdx + 1 : 0;
            navMax = navIdx;
            try { history.replaceState({ mapp: navIdx }, ''); } catch (_) { /* sandboxed */ }
        }
        navSeen = true;
        navMax = Math.max(navMax, navIdx);
        const b = $('#mapp-hist-back'), f = $('#mapp-hist-fwd');
        if (b) b.disabled = navIdx <= 0;
        if (f) f.disabled = navIdx >= navMax;
    }
    function route() {
        trackNav();
        const { view, arg, params } = parseHash();
        let v = view;
        if (v === 'queue') { openRail(true); v = currentView || 'home'; }
        if (!views.includes(v)) v = 'home';
        show(v);
        if (v === 'lyrics') loadLyrics(false);
        else if (v === 'history' && !historyLoaded) refreshHistory();
        else if (v === 'search') { if (input && !input.value) searchIdle(); }
        document.dispatchEvent(new CustomEvent('mapp:route', { detail: { view: v, arg, params } }));
    }
    window.addEventListener('hashchange', route);

    // ── Rail: column on desktop, drawer below 1200px ─────────────────────
    const wide = window.matchMedia('(min-width: 1200px)');
    const scrim = $('#mapp-scrim');
    function railShown() { return wide.matches ? !root.classList.contains('is-rail-off') : root.classList.contains('is-rail-open'); }
    function syncRailBtns() { $$('.mapp-bar [data-rail-toggle]').forEach((b) => b.setAttribute('aria-pressed', String(railShown()))); }
    function openRail(open) {
        if (wide.matches) {
            root.classList.toggle('is-rail-off', !open);
            store.set('mapp-rail', open ? 'on' : 'off');
        } else {
            root.classList.toggle('is-rail-open', open);
            if (scrim) scrim.hidden = !open;
            if (open) { const c = $('.mapp-rail-close'); if (c) c.focus({ preventScroll: true }); }
        }
        syncRailBtns();
    }
    if (store.get('mapp-rail') === 'off') root.classList.add('is-rail-off');
    wide.addEventListener('change', () => { root.classList.remove('is-rail-open'); if (scrim) scrim.hidden = true; syncRailBtns(); });

    // ── Queue editing (managers): drag to reorder, remove ────────────────
    const queueBox = $('#mapp-queue');
    let dragFrom = -1;
    const manualCount = () => ((status && status.queue) || []).filter((t) => !t.autoplay).length;
    function clearOver() { $$('.is-over-top, .is-over-bottom', queueBox).forEach((r) => r.classList.remove('is-over-top', 'is-over-bottom')); }
    if (queueBox) {
        queueBox.addEventListener('dragstart', (e) => {
            const li = e.target.closest('.mapp-qrow[data-reorder]');
            if (!li) return;
            dragFrom = +li.dataset.index;
            li.classList.add('is-dragging');
            if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'copyMove'; try { e.dataTransfer.setData('text/plain', String(dragFrom)); } catch (_) { /* old browsers */ } }
        });
        queueBox.addEventListener('dragover', (e) => {
            if (dragFrom < 0) return;
            const li = e.target.closest('.mapp-qrow[data-reorder]');
            if (!li) return;
            e.preventDefault();
            const r = li.getBoundingClientRect();
            const after = e.clientY > r.top + r.height / 2;
            clearOver();
            li.classList.add(after ? 'is-over-bottom' : 'is-over-top');
        });
        queueBox.addEventListener('drop', (e) => {
            const li = e.target.closest('.mapp-qrow[data-reorder]');
            if (dragFrom < 0 || !li) return;
            e.preventDefault();
            const target = +li.dataset.index;
            const after = li.classList.contains('is-over-bottom');
            let to = target + (after ? 1 : 0);
            if (dragFrom < to) to -= 1;
            to = Math.max(0, Math.min(manualCount() - 1, to));
            const from = dragFrom;
            dragFrom = -1; clearOver();
            if (to === from || !status) { renderQueue(); return; }
            const list = status.queue;
            const before = list.slice();
            const [moved] = list.splice(from, 1);
            list.splice(to, 0, moved);
            keys.queue = ''; renderQueue();
            control('move', { from, to }, { done: `Moved ${q(moved.title)} to position ${to + 1}`, rollback: () => { status.queue = before; keys.queue = ''; } });
        });
        queueBox.addEventListener('dragend', () => { dragFrom = -1; clearOver(); $$('.is-dragging', queueBox).forEach((r) => r.classList.remove('is-dragging')); });
    }
    async function removeFromQueue(btn) {
        const li = btn.closest('.mapp-qrow');
        if (!li || !status) return;
        const index = +li.dataset.index, title = li.dataset.title;
        const ok = await PercyConfirm.ask({
            title: 'Remove from the queue?',
            consequence: `${q(title)} will be dropped from the queue.`,
            undo: 'Add it again from search or your history.',
            confirmLabel: 'Remove',
        });
        if (!ok) return;
        const before = status.queue.slice();
        status.queue.splice(index, 1);
        keys.queue = ''; renderQueue();
        control('remove', { index }, { done: `Removed ${q(title)} from the queue`, rollback: () => { status.queue = before; keys.queue = ''; } });
    }

    // ── Clicks (one delegated handler) ───────────────────────────────────
    document.addEventListener('click', async (e) => {
        const hist = e.target.closest('[data-hist]');
        if (hist) { history.go(+hist.dataset.hist); return; }
        const railT = e.target.closest('[data-rail-toggle]');
        if (railT) { e.preventDefault(); openRail(!railShown()); return; }
        if (e.target.closest('[data-focus-search]')) { if (!wide.matches) openRail(false); setTimeout(focusSearch, 0); }
        const shelfBtn = e.target.closest('[data-shelf]');
        if (shelfBtn) { const sh = document.getElementById(shelfBtn.dataset.target || 'mapp-recents'); if (sh) sh.scrollBy({ left: +shelfBtn.dataset.shelf * sh.clientWidth * 0.8 }); return; }
        // Close popovers on outside click.
        $$('details.mapp-picker[open], details.shell-me[open]').forEach((d) => { if (!d.contains(e.target)) d.open = false; });
        const btn = e.target.closest('[data-act]');
        if (!btn || btn.disabled) return;
        const act = btn.dataset.act;
        switch (act) {
            case 'toggle': togglePlay(); break;
            case 'skip':
                if (!blocked('skip')) once('skip', btn, () => control('skip', null, { done: 'Skipped to the next track' }));
                break;
            case 'back':
                if (!blocked('back')) once('back', btn, () => control('back', null, { done: 'Playing the previous track' }));
                break;
            case 'shuffle': {
                if (blocked('shuffle')) break;
                const t = np(), was = !!t.shuffle;
                t.shuffle = !was; renderTransport();
                once('shuffle', btn, () => control('shuffle', { value: !was }, { done: was ? 'Shuffle off' : 'Shuffle on', rollback: () => { t.shuffle = was; } }));
                break;
            }
            case 'loop': {
                if (blocked('loop')) break;
                const t = np(), was = t.loop || 0, mode = (was + 1) % 3;
                t.loop = mode; renderTransport();
                once('loop', btn, () => control('loop', { mode }, { done: ['Repeat off', 'Repeating this track', 'Repeating the queue'][mode], rollback: () => { t.loop = was; } }));
                break;
            }
            case 'stop': {
                const ok = await PercyConfirm.ask({ title: 'Stop the music?', consequence: 'Playback stops, the queue is cleared and Percy leaves the voice channel.', undo: null, confirmLabel: 'Stop and disconnect', danger: true });
                if (ok) control('stop', null, { done: 'Stopped. Percy left the voice channel.' });
                break;
            }
            case 'mute': toggleMute(); break;
            case 'lyrics': if (currentView === 'lyrics') go(lastView || 'home'); else go('lyrics'); break;
            case 'add': once(`add:${trackOf(btn).uri || trackOf(btn).title}`, null, () => playTrack(trackOf(btn), false)); break;
            case 'playnow': once(`play:${trackOf(btn).uri || trackOf(btn).title}`, null, () => playTrack(trackOf(btn), true)); break;
            case 'like': toggleLike(btn); break;
            case 'qremove': removeFromQueue(btn); break;
            case 'start': {
                const qh = queueHint();
                if (qh) { toast('info', qh); break; }
                go('search');
                setTimeout(focusSearch, 0);
                const ch = viewer.voice_channel && viewer.voice_channel.name;
                toast('info', `Search for a song. It starts playing in ${ch ? '#' + ch : 'your voice channel'}.`);
                break;
            }
            case 'vibe': playVibe(btn.dataset.query, btn.dataset.label); break;
            case 'retry-recs': loadRecs(); break;
            case 'retry-history': historyLoaded = false; refreshHistory(); break;
            case 'retry-search': runSearch(input ? input.value : ''); break;
            case 'retry-lyrics': loadLyrics(true); break;
            default: break;
        }
    });
    // Synced lyric lines seek to their timestamp (when the viewer may seek).
    document.addEventListener('click', (e) => {
        const line = e.target.closest('.mapp-lyrics.is-seekable .mapp-lyric');
        if (line) { userScrolledAt = 0; seekTo(+line.dataset.t); }
    });
    // Broken artwork falls back to the gradient underneath.
    document.addEventListener('error', (e) => {
        const img = e.target;
        if (img.tagName !== 'IMG') return;
        if (img.closest('.mapp-art')) nextCover(img);
        else if (img.dataset.initial !== undefined) {
            const s = document.createElement('span');
            s.className = 'shell-me-fallback'; s.textContent = img.dataset.initial || '?';
            img.replaceWith(s);
        }
    }, true);

    // A missing maxres thumbnail is served as a 120x90 grey stand-in, not an error.
    const tooSmall = (img) => img.naturalWidth > 0 && img.naturalWidth <= 120 && /ytimg\.com/.test(img.src);
    document.addEventListener('load', (e) => { const img = e.target; if (img.tagName === 'IMG' && img.closest('.mapp-art') && tooSmall(img)) nextCover(img); }, true);
    // Server-rendered covers may have settled before this script ran.
    $$('.mapp-art img').forEach((img) => { if (img.complete && (img.naturalWidth === 0 || tooSmall(img))) nextCover(img); });

    // ── Keyboard shortcuts ───────────────────────────────────────────────
    document.addEventListener('keydown', (e) => {
        if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
        const t = e.target;
        if (e.key === 'Escape') {
            if (!wide.matches && root.classList.contains('is-rail-open')) openRail(false);
            $$('details.mapp-picker[open], details.shell-me[open]').forEach((d) => { d.open = false; });
            return;
        }
        if (t.closest && t.closest('input, textarea, select, [contenteditable="true"], .pc-dialog, dialog')) return;
        if (e.key === '/') { e.preventDefault(); focusSearch(); return; }
        if (e.key === ' ' || e.code === 'Space') {
            if (t.closest('button, a, summary')) return; // Space activates the focused control instead
            e.preventDefault(); togglePlay(); return;
        }
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            if (!np() || isStream) return;
            e.preventDefault(); seekBy(e.key === 'ArrowRight' ? 5000 : -5000); return;
        }
        if (e.key === 'm' || e.key === 'M') { e.preventDefault(); toggleMute(); }
    });

    Object.assign(window.mapp, {
        fmt, art, grad, empty, skeleton, subHtml, authorHtml, artistHref, albumHref, playTrack, queueHint, blocked, np, go, route,
        liked, likedMeta, playlists, refreshLibrary, renderPlaylists, renderLiked, likeTrack,
        once, trackOf, canDestroy, refresh: () => { applyRole(); syncHearts(); }, status: () => status,
    });

    // ── Boot ─────────────────────────────────────────────────────────────
    $$('.mapp time[datetime]').forEach((el) => { el.textContent = ago(el.getAttribute('datetime')); });
    if (status) applyStatus(status); else renderAll();
    keys.history = ''; // the server rendered history; the first change refetches it
    syncRailBtns();
    route();
    requestAnimationFrame(frame);
    refreshLibrary();
    loadRecs();
    loadTrending();
    if (hasGuild) soon(boot.status ? 5000 : 50);
})();
