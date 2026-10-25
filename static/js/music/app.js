/* Music app client. Upgrades the server-rendered page in templates/music/app.html:
 * live poll, controls, search, like, queue management, lyrics, library panes.
 * Percy decides what a viewer may do (viewer JSON); this script only mirrors it. */
(function () {
    'use strict';

    const GID = document.body.dataset.guildId || '';
    const BASE = `/dashboard/music/${GID}`;
    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const ico = (n) => `<svg aria-hidden="true"><use href="/static/icons/sprite.svg#i-${n}"/></svg>`;
    const fmt = (ms) => { const s = Math.max(0, Math.floor((ms || 0) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
    const toast = (msg) => (window.showToast ? window.showToast('error', msg) : console.error(msg));

    async function api(url, method, body, quiet) {
        try {
            const r = await fetch(url, {
                method: method || 'GET',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: body ? JSON.stringify(body) : undefined,
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok || d.ok === false) { if (!quiet) toast(d.error || 'Something went wrong.'); return null; }
            return d;
        } catch (_) { if (!quiet) toast('Could not reach the server.'); return null; }
    }

    const main = $('.mapp-main');
    let viewer = {};
    let status = null;
    let serverPos = 0, serverAt = 0, paused = true, duration = 0, isStream = false;
    let dragging = false;
    let lyrics = null, lyricIdx = -1;
    const liked = new Set($$('#liked .mapp-track').map((r) => r.dataset.uri));

    const canControl = () => !!viewer.can_control;
    const canQueue = () => !!(viewer.can_control || viewer.can_start);

    // ── Right-hand state: bar, rail, controls ───────────────────────────
    // Why the player is locked for this viewer; Percy decides, this only words it.
    function lockHint() {
        if (viewer.dj_mode === 1 && !viewer.is_manager) return 'Only DJs can control the player';
        const ch = viewer.bot_channel && viewer.bot_channel.name;
        return ch ? `Join #${ch} to control` : "Join Percy's voice channel to control";
    }
    const DJ_ONLY = ['skip', 'back', 'shuffle', 'loop']; // hybrid mode: everyone pauses, only DJs steer
    function applyViewer() {
        const locked = !canControl();
        $$('.mapp-bar [data-action]').forEach((b) => {
            const a = b.dataset.action;
            if (a === 'lyrics') return;
            const needsDj = !locked && DJ_ONLY.includes(a) && !viewer.can_destroy;
            b.disabled = locked || needsDj;
            b.title = locked ? lockHint() : needsDj ? 'Needs the DJ role' : '';
        });
        const vol = $('#mapp-volume');
        if (vol) { vol.disabled = locked; vol.title = locked ? lockHint() : 'Volume'; }
        const stop = $('.mapp-bar [data-action="stop"]');
        if (stop) stop.hidden = !viewer.can_destroy;
        $$('[data-action="queue"]').forEach((b) => { b.disabled = !canQueue(); b.title = canQueue() ? 'Add to queue' : lockHint(); });
        const start = $('[data-action="start"]');
        if (start) start.disabled = !viewer.can_start;
    }

    function renderRail() {
        const rail = $('.mapp-rail');
        if (!rail || !status) return;
        const np = status.now_playing;
        const q = status.queue || [];
        const thumb = (t, cls) => t.artwork ? `<img class="mapp-thumb ${cls || ''}" src="${esc(t.artwork)}" alt="">` : `<span class="mapp-thumb ${cls || ''} icon-tile">${ico('music')}</span>`;
        const mgr = !!viewer.is_manager;
        rail.innerHTML = `
            <div class="card mapp-np">${np
                ? `${thumb(np, 'mapp-thumb--lg')}<div><small class="mapp-muted">Now playing${status.channel_name ? ' in #' + esc(status.channel_name) : ''}</small><br><b>${esc(np.title)}</b><br><small>${esc(np.author)}</small></div>`
                : '<span class="mapp-muted">Nothing is playing.</span>'}</div>
            <h3 class="mapp-h">Queue</h3>
            ${q.length ? `<ol class="mapp-queue" id="mapp-queue">${q.slice(0, 25).map((t, i) => `
                <li data-index="${i}"${mgr && !t.autoplay ? ' draggable="true"' : ''}>
                    ${mgr && !t.autoplay ? `<span class="mapp-grip" aria-hidden="true">${ico('grip')}</span>` : ''}
                    <span>${esc(t.title)}</span><small>${esc(t.author)}</small>
                    ${mgr && !t.autoplay ? `<button type="button" class="mapp-ico" data-q-remove aria-label="Remove from queue">${ico('x')}</button>` : ''}
                </li>`).join('')}</ol>` : '<p class="mapp-muted">The queue is empty.</p>'}
            <p class="mapp-muted">${status.listeners || 0} listening</p>`;
    }

    function renderBar() {
        const np = status && status.now_playing;
        const now = $('.mapp-bar-now');
        if (now) now.innerHTML = np ? `<b>${esc(np.title)}</b><br><small class="mapp-muted">${esc(np.author)}</small>` : '<span class="mapp-muted">Nothing playing</span>';
        const lk = $('.mapp-bar [data-action="like"]');
        if (lk) { lk.hidden = !(np && np.uri); lk.dataset.uri = (np && np.uri) || ''; lk.dataset.name = (np && np.title) || ''; syncHearts(); }
        const t = $('[data-action="toggle"]');
        if (t) t.innerHTML = ico(np && !np.paused ? 'pause' : 'play');
        const l = $('[data-action="loop"]');
        if (l) { const m = np ? np.loop : 0; l.innerHTML = ico(m === 1 ? 'repeat-one' : 'repeat'); l.classList.toggle('on', m !== 0); }
        const s = $('[data-action="shuffle"]');
        if (s) s.classList.toggle('on', !!(np && np.shuffle));
    }

    function applyStatus(d) {
        status = d;
        const np = d.now_playing;
        serverPos = np ? np.position : 0;
        serverAt = performance.now();
        paused = !np || np.paused;
        duration = np ? np.duration : 0;
        isStream = !!(np && np.is_stream);
        renderBar();
        const vol = $('#mapp-volume');
        if (vol && np && performance.now() - volTouched > 1500) vol.value = np.volume;
        if (!dragging) renderRail();
        applyViewer();
        loadLyrics(true);
    }

    // One persistent notice for a lost connection, cleared by the next good poll.
    function connNotice(lost) {
        let n = $('#mapp-conn');
        if (!n && lost) {
            n = document.createElement('div');
            n.id = 'mapp-conn'; n.className = 'card mapp-banner'; n.setAttribute('role', 'status');
            n.textContent = 'Connection lost - retrying';
            main.prepend(n);
        }
        if (n) n.hidden = !lost;
    }

    async function poll() {
        if (!GID || document.hidden) return;
        const [s, v] = await Promise.all([api(`${BASE}/status`, 'GET', null, true), api(`${BASE}/viewer`, 'GET', null, true)]);
        connNotice(!(s && v));
        if (v) viewer = v;
        if (s) applyStatus(s); else applyViewer();
    }

    // Position interpolation between polls.
    function pos() { return paused ? serverPos : serverPos + (performance.now() - serverAt); }
    function frame() {
        const fill = $('.mapp-prog i');
        if (fill && duration && !isStream) fill.style.width = `${Math.min(100, (pos() / duration) * 100)}%`;
        else if (fill && isStream) fill.style.width = '100%';
        highlightLyric();
        requestAnimationFrame(frame);
    }

    // ── Actions ─────────────────────────────────────────────────────────
    async function control(action, extra) {
        const d = await api(`${BASE}/control`, 'POST', Object.assign({ action }, extra || {}));
        if (d) setTimeout(poll, 400);
        return d;
    }
    const play = async (query) => { const d = await api(`${BASE}/play`, 'POST', { query }); if (d) setTimeout(poll, 600); return d; };

    async function toggleLike(row) {
        const url = row.dataset.uri, name = row.dataset.name || '';
        if (!url) return;
        const on = liked.has(url);
        const d = await api(on ? '/account/music/liked/remove' : '/account/music/liked', 'POST', on ? { url } : { name, url });
        if (!d) return;
        if (on) liked.delete(url); else liked.add(url);
        syncHearts();
    }
    function syncHearts() {
        $$('[data-action="like"]').forEach((b) => {
            const u = b.closest('[data-uri]');
            b.innerHTML = ico(u && liked.has(u.dataset.uri) ? 'heart-fill' : 'heart');
        });
    }

    document.addEventListener('click', async (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;
        const act = btn.dataset.action;
        const row = btn.closest('[data-uri]');
        if (btn.hasAttribute('data-q-remove')) {
            const li = btn.closest('li');
            const ok = await PercyConfirm.ask({ title: 'Remove this track from the queue?', consequence: li.firstElementChild && li.querySelector('span:not(.mapp-grip)').textContent + ' will be dropped from the queue.', undo: 'Queue it again from search or history.', confirmLabel: 'Remove' });
            if (ok) control('remove', { index: parseInt(li.dataset.index, 10) });
        } else if (act === 'queue' && row) play(row.dataset.uri);
        else if (act === 'like' && row) toggleLike(row);
        else if (act === 'start') { const s = $('#mapp-search'); if (s) s.focus(); }
        else if (act === 'lyrics') openLyrics();
        else if (act === 'toggle') { paused = !paused; serverPos = pos(); serverAt = performance.now(); control(status && status.now_playing && status.now_playing.paused ? 'resume' : 'pause'); }
        else if (act === 'loop') control('loop', { mode: (((status && status.now_playing && status.now_playing.loop) || 0) + 1) % 3 });
        else if (act === 'shuffle') control('shuffle', { value: !(status && status.now_playing && status.now_playing.shuffle) });
        else if (act === 'stop') {
            const ok = await PercyConfirm.ask({ title: 'Stop the music?', consequence: 'Playback stops, the queue is cleared and Percy leaves the voice channel.', undo: null, confirmLabel: 'Stop and disconnect', danger: true });
            if (ok) control('stop');
        } else if (act === 'back' || act === 'skip') control(act);
    });

    // Volume: debounced so a drag sends one request, not hundreds.
    let volTouched = 0, volTimer = 0;
    $('#mapp-volume')?.addEventListener('input', (e) => {
        volTouched = performance.now();
        clearTimeout(volTimer);
        const value = parseInt(e.target.value, 10);
        volTimer = setTimeout(() => control('volume', { value }), 250);
    });

    // Seek on the progress bar.
    $('.mapp-prog')?.addEventListener('click', (e) => {
        if (!canControl() || isStream || !duration) return;
        const r = e.currentTarget.getBoundingClientRect();
        const ms = Math.round(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * duration);
        serverPos = ms; serverAt = performance.now();
        control('seek', { position: ms });
    });

    // Drag-to-move queue rows (managers only: rows are only draggable for them).
    const rail = $('.mapp-rail');
    let dragFrom = -1;
    rail.addEventListener('dragstart', (e) => {
        const li = e.target.closest('li[draggable]');
        if (!li) return;
        dragFrom = parseInt(li.dataset.index, 10); dragging = true;
        li.classList.add('dragging');
        if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', String(dragFrom)); } catch (_) { /* old browsers */ } }
    });
    rail.addEventListener('dragover', (e) => { if (dragFrom >= 0) e.preventDefault(); });
    rail.addEventListener('drop', (e) => {
        const li = e.target.closest('li[draggable]');
        if (dragFrom >= 0 && li) {
            const to = parseInt(li.dataset.index, 10);
            if (to !== dragFrom) control('move', { from: dragFrom, to });
        }
        dragFrom = -1; dragging = false;
    });
    rail.addEventListener('dragend', () => { dragFrom = -1; dragging = false; $$('.dragging', rail).forEach((r) => r.classList.remove('dragging')); });

    // ── Search ──────────────────────────────────────────────────────────
    const input = $('#mapp-search'), results = $('#mapp-results');
    if (!GID) { input.disabled = true; input.placeholder = 'Pick a server to search'; }
    let timer = 0, seq = 0;
    $('.mapp-search').addEventListener('submit', (e) => e.preventDefault());
    input.addEventListener('input', () => {
        clearTimeout(timer);
        const q = input.value.trim();
        if (!q || !GID) { results.hidden = true; return; }
        timer = setTimeout(async () => {
            const mine = ++seq;
            const d = await api(`${BASE}/search?q=${encodeURIComponent(q)}`);
            if (!d || mine !== seq) return;
            results.innerHTML = d.results.length ? d.results.map((t) => `
                <div class="mapp-track" data-uri="${esc(t.uri || '')}" data-name="${esc(t.title)}">
                    ${t.artwork ? `<img class="mapp-thumb" src="${esc(t.artwork)}" alt="">` : `<span class="mapp-thumb icon-tile">${ico('music')}</span>`}
                    <span class="mapp-track-text">${esc(t.title)}<small>${esc(t.author)}</small></span>
                    <span class="mapp-muted">${fmt(t.duration)}</span>
                    <button type="button" class="mapp-ico" data-action="queue" aria-label="Add to queue">${ico('plus')}</button>
                </div>`).join('') : '<p class="mapp-muted" style="padding:.6rem">No results.</p>';
            results.hidden = false;
            applyViewer();
        }, 300);
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('.mapp-search')) results.hidden = true; });

    // ── Panes (Home / History / Liked / Playlists / Lyrics) ─────────────
    function decorateRows() {
        const btn = (a, label, i) => `<button type="button" class="mapp-ico" data-action="${a}" aria-label="${label}">${ico(i)}</button>`;
        // Already-played tracks get a re-queue button; the upcoming queue never does.
        $$('#history .mapp-track[data-uri], #liked .mapp-track[data-uri]').forEach((r) => {
            if (!$('button', r)) r.insertAdjacentHTML('beforeend', btn('queue', 'Add to queue', 'plus') + btn('like', 'Like', 'heart'));
        });
        $$('.mapp-card[data-uri]').forEach((c) => {
            if (!$('button', c)) { c.dataset.name = c.dataset.name || $('p', c).textContent; c.insertAdjacentHTML('beforeend', btn('queue', 'Add to queue', 'plus')); }
        });
        $$('.mapp-track[data-uri] [data-action="like"]').forEach((b) => b.removeAttribute('disabled'));
        syncHearts();
        applyViewer();
    }

    function show(id) {
        const ids = id === 'eq-section' ? ['eq-section', 'filters-section'] : id === 'dj-mode-section' ? ['dj-mode-section', 'always-on-section'] : [id];
        $$(':scope > section', main).forEach((s) => { s.hidden = !ids.includes(s.id); });
        $$('.mapp-side a').forEach((a) => a.classList.toggle('on', a.getAttribute('href') === '#' + id));
    }
    function route() {
        // No server picked means no #home: land on the first pane that exists (Liked Songs).
        const first = ['home', 'liked'].find((i) => $('#' + i)) || 'liked';
        const id = location.hash.slice(1) || first;
        show($('#' + id) ? id : first);
    }
    window.addEventListener('hashchange', route);

    // Playlist rows open their tracks in place.
    document.addEventListener('click', async (e) => {
        const row = e.target.closest('[data-playlist-id]');
        if (!row) return;
        const d = await api(`/account/music/playlists/${encodeURIComponent(row.dataset.playlistId)}`);
        if (!d) return;
        const sec = $('#playlists');
        sec.innerHTML = `<h2 class="mapp-h"><a href="#playlists" id="mapp-pl-back">Playlists</a> / ${esc(d.playlist.name)}</h2>
            <ul class="mapp-tracks">${(d.playlist.tracks || []).map((t) => `
                <li class="mapp-track" data-uri="${esc(t.url)}" data-name="${esc(t.name)}">
                    <span class="mapp-thumb icon-tile">${ico('music')}</span><span class="mapp-track-text">${esc(t.name)}</span>
                    <button type="button" class="mapp-ico" data-action="queue" aria-label="Add to queue">${ico('plus')}</button>
                    <button type="button" class="mapp-ico" data-action="like" aria-label="Like">${ico('heart')}</button>
                </li>`).join('')}</ul>`;
        $('#mapp-pl-back').addEventListener('click', (ev) => { ev.preventDefault(); location.reload(); });
        syncHearts(); applyViewer();
    });

    // ── Lyrics ──────────────────────────────────────────────────────────
    async function loadLyrics(onlyIfOpen) {
        const pane = $('#lyrics');
        if (onlyIfOpen && (!pane || pane.hidden)) return;
        const title = status && status.now_playing && status.now_playing.title;
        if (lyrics && lyrics.for === title) return;
        const d = await api(`${BASE}/lyrics`, "GET", null, onlyIfOpen); // background refetches stay quiet
        if (!d) { if (pane) pane.innerHTML = '<h2 class="mapp-h">Lyrics</h2><p class="mapp-muted">Could not load lyrics. Open this tab again to retry.</p>'; return; } // not cached, so a retry refetches
        lyrics = Object.assign(d, { for: title });
        lyricIdx = -1;
        if (!pane) return;
        pane.innerHTML = `<h2 class="mapp-h">Lyrics${lyrics.title ? ' - ' + esc(lyrics.title) : ''}</h2>` + (lyrics.has_synced
            ? `<div class="mapp-lyrics">${lyrics.lines.map((l, i) => `<p data-i="${i}">${esc(l.text)}</p>`).join('')}</div>`
            : lyrics.plain ? `<pre class="mapp-lyrics">${esc(lyrics.plain)}</pre>` : '<p class="mapp-muted">No lyrics found for this track.</p>');
    }
    function openLyrics() {
        if (!GID) return;
        let pane = $('#lyrics');
        if (!pane) { pane = document.createElement('section'); pane.id = 'lyrics'; pane.className = 'mapp-section'; main.appendChild(pane); }
        if (!pane.hidden && location.hash === '#lyrics') { location.hash = '#home'; return; }
        location.hash = '#lyrics';
        show('lyrics');
        loadLyrics(false);
    }
    function highlightLyric() {
        if (!lyrics || !lyrics.has_synced || !$('#lyrics') || $('#lyrics').hidden) return;
        const p = pos();
        let i = -1;
        for (let k = 0; k < lyrics.lines.length && lyrics.lines[k].time <= p; k++) i = k;
        if (i === lyricIdx) return;
        lyricIdx = i;
        $$('#lyrics p').forEach((n, k) => n.classList.toggle('on', k === i));
        const cur = $(`#lyrics p[data-i="${i}"]`);
        if (cur) cur.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }

    // ── Boot ────────────────────────────────────────────────────────────
    decorateRows();
    route();
    if (GID) {
        poll();
        setInterval(poll, 5000);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
        // Destructive control, only revealed by the viewer's role.
        const ctrls = $('.mapp-ctrls');
        if (ctrls) ctrls.insertAdjacentHTML('beforeend', `<button type="button" class="mapp-ico" data-action="stop" aria-label="Stop and disconnect" hidden>${ico('x')}</button>`);
        requestAnimationFrame(frame);
    }
})();
