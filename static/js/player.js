/* Percy Dashboard — shared Apple-Music-style live player.
 *
 * One component, mounted on both the admin Music page and the public server
 * overview. It owns a single status poll, interpolates the playback position
 * locally between polls (so the progress bar and synced-lyrics highlight move
 * smoothly at 60fps without hammering the bot), and drives every control.
 *
 * Mount with:
 *   PercyPlayer.mount({
 *     root:        HTMLElement,        // empty container to render into
 *     statusUrl:   '…/music/status',  // GET → { active, now_playing, queue, can_control, … }
 *     controlUrl:  '…/music/control',  // POST { action, … }
 *     lyricsUrl:   '…/music/lyrics',   // GET → { has_synced, lines:[{time,text}], plain }
 *     onStatus:    fn(data) {},        // optional: called after every poll
 *   });
 *
 * Relies on window.showToast (percy_common.js).
 */
(function () {
    'use strict';

    function el(tag, cls, html) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (html != null) e.innerHTML = html;
        return e;
    }

    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function fmtTime(ms) {
        if (!isFinite(ms) || ms < 0) ms = 0;
        const total = Math.floor(ms / 1000);
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        const pad = (n) => String(n).padStart(2, '0');
        return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
    }

    const SOURCE_LABELS = {
        spotify: 'Spotify',
        youtube: 'YouTube',
        youtubemusic: 'YouTube Music',
        soundcloud: 'SoundCloud',
        applemusic: 'Apple Music',
        twitch: 'Twitch',
        http: 'Stream',
    };

    function MARKUP() {
        return `
<div class="ap-player" data-state="idle">
  <div class="ap-idle">
    <div class="ap-idle-art">&#9835;</div>
    <div class="ap-idle-text">
      <strong class="ap-idle-title">Nothing playing</strong>
      <span class="ap-idle-sub">No active music session in this server.</span>
    </div>
  </div>

  <div class="ap-live">
    <div class="ap-stage">
      <div class="ap-artwork">
        <div class="ap-artwork-bg"></div>
        <img class="ap-artwork-img" alt="" />
        <div class="ap-artwork-ph">&#9835;</div>
      </div>
      <div class="ap-main">
        <div class="ap-meta">
          <a class="ap-title" target="_blank" rel="noopener"></a>
          <a class="ap-artist" target="_blank" rel="noopener"></a>
          <div class="ap-tags"></div>
        </div>

        <div class="ap-progress">
          <span class="ap-time ap-time-cur">0:00</span>
          <div class="ap-bar" tabindex="0" role="slider" aria-label="Seek">
            <div class="ap-bar-track"><div class="ap-bar-fill"></div></div>
            <div class="ap-bar-knob"></div>
          </div>
          <span class="ap-time ap-time-dur">0:00</span>
        </div>

        <div class="ap-controls">
          <button class="ap-btn ap-shuffle" data-action="shuffle" title="Shuffle" aria-label="Shuffle">${ICON.shuffle}</button>
          <button class="ap-btn ap-back" data-action="back" title="Previous" aria-label="Previous">${ICON.back}</button>
          <button class="ap-btn ap-play" data-action="toggle" title="Play / Pause" aria-label="Play or pause">${ICON.play}</button>
          <button class="ap-btn ap-skip" data-action="skip" title="Next" aria-label="Next">${ICON.skip}</button>
          <button class="ap-btn ap-loop" data-action="loop" title="Repeat" aria-label="Repeat">${ICON.loop}</button>
        </div>

        <div class="ap-secondary">
          <div class="ap-volume">
            <span class="ap-vol-icon">${ICON.volume}</span>
            <input class="ap-vol-slider" type="range" min="0" max="100" step="1" value="100" aria-label="Volume" />
            <span class="ap-vol-val">100%</span>
          </div>
          <div class="ap-requester"></div>
          <button class="ap-btn ap-stop" data-action="stop" title="Stop & disconnect" aria-label="Stop">${ICON.stop}</button>
        </div>
        <p class="ap-hint" hidden>Join the bot's voice channel to control playback.</p>
      </div>
    </div>

    <div class="ap-panel">
      <div class="ap-tabs">
        <button class="ap-tab is-active" data-tab="queue">Up Next</button>
        <button class="ap-tab" data-tab="history">History</button>
        <button class="ap-tab" data-tab="lyrics">Lyrics</button>
      </div>
      <div class="ap-tabpane ap-queue is-active"></div>
      <div class="ap-tabpane ap-history"></div>
      <div class="ap-tabpane ap-lyrics"></div>
    </div>
  </div>
</div>`;
    }

    const ICON = {
        play: '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>',
        pause: '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M6 5h4v14H6zM14 5h4v14h-4z" fill="currentColor"/></svg>',
        skip: '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M6 5l8 7-8 7zM16 5h2v14h-2z" fill="currentColor"/></svg>',
        back: '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M18 5l-8 7 8 7zM6 5h2v14H6z" fill="currentColor"/></svg>',
        shuffle: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M16 3h5v5M21 3l-7 7M4 20l16-16M16 21h5v-5M21 21l-6-6M4 4l5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        loop: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M17 2l4 4-4 4M3 11V9a4 4 0 014-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 01-4 4H3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        loopOne: '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M17 2l4 4-4 4M3 11V9a4 4 0 014-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 01-4 4H3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><text x="12" y="15.5" font-size="9" font-weight="700" text-anchor="middle" fill="currentColor" stroke="none">1</text></svg>',
        stop: '<svg viewBox="0 0 24 24" width="18" height="18"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>',
        volume: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M4 9v6h4l5 5V4L8 9H4z" fill="currentColor"/><path d="M16 8a5 5 0 010 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    };

    function mount(opts) {
        const root = opts.root;
        if (!root) return null;
        root.innerHTML = MARKUP();

        const q = (sel) => root.querySelector(sel);
        const player = q('.ap-player');
        const live = q('.ap-live');
        const artImg = q('.ap-artwork-img');
        const artBg = q('.ap-artwork-bg');
        const artPh = q('.ap-artwork-ph');
        const titleEl = q('.ap-title');
        const artistEl = q('.ap-artist');
        const tagsEl = q('.ap-tags');
        const curEl = q('.ap-time-cur');
        const durEl = q('.ap-time-dur');
        const bar = q('.ap-bar');
        const fill = q('.ap-bar-fill');
        const knob = q('.ap-bar-knob');
        const playBtn = q('.ap-play');
        const shuffleBtn = q('.ap-shuffle');
        const loopBtn = q('.ap-loop');
        const volSlider = q('.ap-vol-slider');
        const volVal = q('.ap-vol-val');
        const requesterEl = q('.ap-requester');
        const hintEl = q('.ap-hint');
        const queuePane = q('.ap-queue');
        const historyPane = q('.ap-history');
        const lyricsPane = q('.ap-lyrics');

        // Artwork loads asynchronously: only reveal the <img> once it has actually
        // decoded, otherwise fall back to the note placeholder. `no-referrer` keeps
        // CDNs that gate on Referer (some Spotify/Apple art) from 403-ing.
        artImg.referrerPolicy = 'no-referrer';
        let artUrl = null;
        // YouTube thumbnails come in fixed resolution tiers; the higher ones 404 for
        // many videos. On error, step down to the next tier before giving up so any
        // YouTube track still shows art (hqdefault/default always exist).
        const YT_TIERS = ['maxresdefault', 'sddefault', 'hqdefault', 'mqdefault', 'default'];
        function ytDowngrade(url) {
            const m = /^(https?:\/\/i\.ytimg\.com\/vi\/[^/]+\/)([a-z]+default)(\.jpg.*)$/i.exec(url || '');
            if (!m) return null;
            const i = YT_TIERS.indexOf(m[2]);
            return i >= 0 && i < YT_TIERS.length - 1 ? m[1] + YT_TIERS[i + 1] + m[3] : null;
        }
        artImg.addEventListener('load', () => { artImg.style.display = ''; artPh.style.display = 'none'; });
        artImg.addEventListener('error', () => {
            const next = ytDowngrade(artImg.src);
            if (next) { artImg.src = next; return; }
            artImg.style.display = 'none';
            artPh.style.display = '';
        });
        function setArtwork(url) {
            if (url === artUrl) return;
            artUrl = url || null;
            if (url) {
                artBg.style.backgroundImage = `url("${url}")`;
                artImg.src = url; // load/error listeners toggle visibility
            } else {
                artImg.removeAttribute('src');
                artImg.style.display = 'none';
                artBg.style.backgroundImage = '';
                artPh.style.display = '';
            }
        }

        // Loop has three stages (off → repeat-one → repeat-queue), each with its
        // own icon + active colour. Shuffle is a simple on/off highlight.
        let loopMode = 0;
        let shuffleOn = false;
        function setLoopState(mode) {
            loopMode = mode;
            loopBtn.dataset.mode = String(mode);
            loopBtn.classList.toggle('is-active', mode !== 0);
            loopBtn.innerHTML = mode === 1 ? ICON.loopOne : ICON.loop;
            loopBtn.title = mode === 1 ? 'Repeat: Track' : mode === 2 ? 'Repeat: Queue' : 'Repeat: Off';
        }
        function setShuffleState(on) {
            shuffleOn = on;
            shuffleBtn.classList.toggle('is-active', on);
            shuffleBtn.title = on ? 'Shuffle: On' : 'Shuffle: Off';
        }

        // ── Live state ──────────────────────────────────────────────
        let canControl = false;
        let np = null;             // last now_playing payload
        let trackKey = null;       // uri (or title) of current track, to detect changes
        let serverPos = 0;         // position (ms) reported by last poll
        let serverAt = performance.now();
        let paused = false;
        let duration = 0;
        let isStream = false;
        let seeking = false;       // user dragging the bar
        let seekPreviewMs = 0;
        let volTouched = 0;        // timestamp of last local volume edit (suppress poll overwrite)
        let dragPaused = false;    // true while a queue row is being dragged (freeze queue re-render)

        // Lyrics
        let lyricLines = [];       // [{time, text}]
        let lyricEls = [];         // rendered line elements
        let lyricActive = -1;
        let lyricsForKey = null;   // trackKey the loaded lyrics belong to
        let lyricsFetching = false;

        function effectivePos() {
            if (seeking) return seekPreviewMs;
            if (paused) return serverPos;
            return Math.min(serverPos + (performance.now() - serverAt), duration || Infinity);
        }

        // ── Rendering ───────────────────────────────────────────────
        function setControlAvail() {
            live.classList.toggle('no-control', !canControl);
            if (hintEl) hintEl.hidden = canControl;
        }

        function renderTrack(data) {
            np = data;
            const key = data.uri || data.title;
            const changed = key !== trackKey;
            trackKey = key;

            duration = data.duration || 0;
            isStream = !!data.is_stream;
            paused = !!data.paused;
            serverPos = data.position || 0;
            serverAt = performance.now();

            if (changed) {
                setArtwork(data.artwork);
                titleEl.textContent = data.title || 'Unknown';
                if (data.uri) { titleEl.href = data.uri; titleEl.classList.add('linked'); }
                else { titleEl.removeAttribute('href'); titleEl.classList.remove('linked'); }

                artistEl.textContent = data.author || '';
                if (data.artist_url) { artistEl.href = data.artist_url; artistEl.classList.add('linked'); }
                else { artistEl.removeAttribute('href'); artistEl.classList.remove('linked'); }

                renderTags(data);
                renderRequester(data.requester);

                // New track → reset & (re)load lyrics.
                lyricActive = -1;
                maybeLoadLyrics();
            }

            // Loop / shuffle button states
            setLoopState(data.loop || 0);
            setShuffleState(!!data.shuffle);

            // Play / pause icon
            playBtn.innerHTML = paused ? ICON.play : ICON.pause;

            // Volume (don't fight an in-flight local drag)
            if (performance.now() - volTouched > 1500) {
                volSlider.value = String(data.volume != null ? data.volume : 100);
                volVal.textContent = `${volSlider.value}%`;
            }

            durEl.textContent = isStream ? 'LIVE' : fmtTime(duration);
            bar.classList.toggle('is-stream', isStream);
        }

        function renderTags(data) {
            const tags = [];
            const src = (data.source || '').toLowerCase();
            if (src) tags.push(`<span class="ap-tag ap-tag-src ap-src-${escapeHtml(src)}">${escapeHtml(SOURCE_LABELS[src] || data.source)}</span>`);
            if (data.album && data.album.name) {
                const a = data.album;
                tags.push(a.url
                    ? `<a class="ap-tag" href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${escapeHtml(a.name)}</a>`
                    : `<span class="ap-tag">${escapeHtml(a.name)}</span>`);
            }
            if (data.recommended) tags.push('<span class="ap-tag ap-tag-auto">Autoplay</span>');
            tagsEl.innerHTML = tags.join('');
        }

        function renderRequester(r) {
            if (!r) { requesterEl.innerHTML = ''; return; }
            const av = r.avatar ? `<img class="ap-req-av" src="${escapeHtml(r.avatar)}" alt="" />` : '';
            const name = r.name || 'someone';
            requesterEl.innerHTML = `<span class="ap-req-label">Added by</span>${av}<span class="ap-req-name">${escapeHtml(name)}</span>`;
        }

        function renderQueue(queue) {
            queue = queue || [];
            if (!queue.length) {
                queuePane.innerHTML = '<p class="ap-empty">The queue is empty — nothing up next.</p>';
                return;
            }
            const rows = queue.map((t, i) => {
                // Autoplay recommendations (wavelink's auto_queue) aren't part of the
                // manual queue, so they can't be jumped to or reordered by index.
                const isAuto = !!t.autoplay;
                const art = t.artwork
                    ? `<img class="ap-q-art" src="${escapeHtml(t.artwork)}" alt="" loading="lazy" />`
                    : '<span class="ap-q-art ap-q-art-ph">&#9835;</span>';
                const dur = t.is_stream ? 'LIVE' : fmtTime(t.duration || 0);
                const who = t.requester && t.requester.name
                    ? `<span class="ap-q-req">${escapeHtml(t.requester.name)}</span>` : '';
                const badge = isAuto ? '<span class="ap-q-badge">Autoplay</span>' : '';
                const title = t.uri
                    ? `<a class="ap-q-title" href="${escapeHtml(t.uri)}" target="_blank" rel="noopener">${escapeHtml(t.title)}</a>`
                    : `<span class="ap-q-title">${escapeHtml(t.title)}</span>`;
                const jump = (canControl && !isAuto)
                    ? `<button class="ap-q-jump" data-jump="${i}" title="Play now" aria-label="Play now">${ICON.play}</button>` : '';
                const draggable = (canControl && !isAuto) ? ' draggable="true"' : '';
                return `<li class="ap-q-row${isAuto ? ' is-autoplay' : ''}" data-idx="${i}"${draggable}>
                    <span class="ap-q-idx">${isAuto ? '&#8226;' : (i + 1)}</span>
                    ${art}
                    <span class="ap-q-meta">${title}<span class="ap-q-author">${escapeHtml(t.author || 'Unknown')}${who ? ' · ' : ''}${who}${badge}</span></span>
                    <span class="ap-q-dur">${dur}</span>
                    ${jump}
                </li>`;
            }).join('');
            queuePane.innerHTML = `<ol class="ap-q-list">${rows}</ol>`;
        }

        function renderHistory(history) {
            history = history || [];
            if (!history.length) {
                historyPane.innerHTML = '<p class="ap-empty">Nothing has played yet.</p>';
                return;
            }
            // Read-only list (most-recent-first): no jump/drag affordances.
            const rows = history.map((t, i) => {
                const art = t.artwork
                    ? `<img class="ap-q-art" src="${escapeHtml(t.artwork)}" alt="" loading="lazy" />`
                    : '<span class="ap-q-art ap-q-art-ph">&#9835;</span>';
                const dur = t.is_stream ? 'LIVE' : fmtTime(t.duration || 0);
                const who = t.requester && t.requester.name
                    ? `<span class="ap-q-req">${escapeHtml(t.requester.name)}</span>` : '';
                const title = t.uri
                    ? `<a class="ap-q-title" href="${escapeHtml(t.uri)}" target="_blank" rel="noopener">${escapeHtml(t.title)}</a>`
                    : `<span class="ap-q-title">${escapeHtml(t.title)}</span>`;
                return `<li class="ap-q-row" data-idx="${i}">
                    <span class="ap-q-idx">${i + 1}</span>
                    ${art}
                    <span class="ap-q-meta">${title}<span class="ap-q-author">${escapeHtml(t.author || 'Unknown')}${who ? ' · ' : ''}${who}</span></span>
                    <span class="ap-q-dur">${dur}</span>
                </li>`;
            }).join('');
            historyPane.innerHTML = `<ol class="ap-q-list">${rows}</ol>`;
        }

        // ── Lyrics ──────────────────────────────────────────────────
        function maybeLoadLyrics() {
            if (!opts.lyricsUrl) {
                lyricsPane.innerHTML = '<p class="ap-empty">Lyrics unavailable.</p>';
                return;
            }
            if (lyricsForKey === trackKey || lyricsFetching) return;
            lyricsFetching = true;
            lyricLines = [];
            lyricEls = [];
            lyricActive = -1;
            lyricsPane.innerHTML = '<p class="ap-empty ap-lyrics-loading">Looking for lyrics…</p>';
            const keyAtFetch = trackKey;
            fetch(opts.lyricsUrl, { headers: { Accept: 'application/json' } })
                .then((r) => r.json())
                .then((d) => {
                    if (keyAtFetch !== trackKey) return; // track changed mid-fetch
                    lyricsForKey = trackKey;
                    if (d && d.has_synced && d.lines && d.lines.length) {
                        lyricLines = d.lines;
                        renderLyricLines();
                    } else if (d && d.plain) {
                        lyricsPane.innerHTML = `<div class="ap-lyrics-plain">${escapeHtml(d.plain).replace(/\n/g, '<br>')}</div>`;
                    } else {
                        lyricsPane.innerHTML = '<p class="ap-empty">No lyrics found for this track.</p>';
                    }
                })
                .catch(() => {
                    lyricsPane.innerHTML = '<p class="ap-empty">Couldn\'t load lyrics.</p>';
                })
                .finally(() => { lyricsFetching = false; });
        }

        function renderLyricLines() {
            const ul = el('div', 'ap-lyrics-synced');
            lyricEls = lyricLines.map((line) => {
                const d = el('p', 'ap-lyric', escapeHtml(line.text || '♪'));
                ul.appendChild(d);
                return d;
            });
            lyricsPane.innerHTML = '';
            lyricsPane.appendChild(ul);
        }

        function updateLyricHighlight(pos) {
            if (!lyricLines.length) return;
            // Binary search for the last line whose time <= pos.
            let lo = 0, hi = lyricLines.length - 1, idx = -1;
            while (lo <= hi) {
                const mid = (lo + hi) >> 1;
                if (lyricLines[mid].time <= pos) { idx = mid; lo = mid + 1; }
                else hi = mid - 1;
            }
            if (idx === lyricActive) return;
            if (lyricActive >= 0 && lyricEls[lyricActive]) lyricEls[lyricActive].classList.remove('is-active');
            lyricActive = idx;
            if (idx >= 0 && lyricEls[idx]) {
                lyricEls[idx].classList.add('is-active');
                centerLyric(lyricEls[idx]);
            }
        }

        // Scroll the active line to the vertical centre of the lyrics pane. Uses
        // rect math (not offsetTop, which is measured from the page, not the
        // scroll container — that's what made it jump to the bottom).
        function centerLyric(node) {
            if (!lyricsPane.classList.contains('is-active')) return;
            const paneRect = lyricsPane.getBoundingClientRect();
            const nodeRect = node.getBoundingClientRect();
            const delta = (nodeRect.top - paneRect.top) - (lyricsPane.clientHeight - node.clientHeight) / 2;
            lyricsPane.scrollTo({ top: lyricsPane.scrollTop + delta, behavior: 'smooth' });
        }

        // ── Animation loop (smooth bar + lyric sync) ────────────────
        function frame() {
            if (player.dataset.state === 'live' && !isStream) {
                const pos = effectivePos();
                const ratio = duration > 0 ? Math.min(pos / duration, 1) : 0;
                fill.style.width = `${ratio * 100}%`;
                knob.style.left = `${ratio * 100}%`;
                if (!seeking) curEl.textContent = fmtTime(pos);
                updateLyricHighlight(pos);
            } else if (player.dataset.state === 'live' && isStream) {
                fill.style.width = '100%';
                curEl.textContent = 'LIVE';
            }
            requestAnimationFrame(frame);
        }

        // ── Controls ────────────────────────────────────────────────
        async function send(action, extra) {
            if (!canControl) { showToast('error', 'Join the voice channel to control playback.'); return; }
            const body = Object.assign({ action }, extra || {});
            try {
                const r = await fetch(opts.controlUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify(body),
                });
                const d = await r.json();
                if (!d.ok) { showToast('error', d.error || 'Action failed.'); return false; }
                setTimeout(poll, 400);
                return true;
            } catch (_) {
                showToast('error', 'Could not reach the bot.');
                return false;
            }
        }

        root.querySelectorAll('.ap-controls .ap-btn, .ap-stop').forEach((btn) => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.action;
                if (action === 'toggle') {
                    // Optimistic flip for snappy feel: re-anchor the local clock to
                    // the current interpolated position so the bar freezes/continues
                    // from exactly where it is, not the last polled value.
                    serverPos = effectivePos();
                    serverAt = performance.now();
                    paused = !paused;
                    playBtn.innerHTML = paused ? ICON.play : ICON.pause;
                    send(paused ? 'pause' : 'resume');
                } else if (action === 'stop') {
                    if (!confirm('Stop playback and disconnect the bot?')) return;
                    send('stop');
                } else if (action === 'loop') {
                    const next = (loopMode + 1) % 3; // off → track → queue → off
                    setLoopState(next);
                    send('loop', { mode: next });
                } else if (action === 'shuffle') {
                    setShuffleState(!shuffleOn);
                    send('shuffle', { value: shuffleOn });
                } else {
                    send(action);
                }
            });
        });

        // Jump-to-queue (event-delegated; rows re-render on each poll).
        queuePane.addEventListener('click', (e) => {
            const b = e.target.closest('.ap-q-jump');
            if (!b) return;
            send('jump', { index: parseInt(b.dataset.jump, 10) });
        });

        // Drag-to-reorder the queue (HTML5 DnD, event-delegated). The new order is
        // sent to Percy as a `move` action; the next poll reflects the authoritative
        // queue. Polling is paused mid-drag so a refresh can't yank the row away.
        let dragFrom = -1;
        function cleanupDrag() {
            dragFrom = -1;
            queuePane.querySelectorAll('.dragging, .drag-over').forEach((r) => r.classList.remove('dragging', 'drag-over'));
            dragPaused = false;
        }
        queuePane.addEventListener('dragstart', (e) => {
            const row = e.target.closest('.ap-q-row');
            if (!row || !canControl) return;
            dragFrom = parseInt(row.dataset.idx, 10);
            dragPaused = true;
            row.classList.add('dragging');
            if (e.dataTransfer) {
                e.dataTransfer.effectAllowed = 'move';
                try { e.dataTransfer.setData('text/plain', String(dragFrom)); } catch (_) { /* IE guard */ }
            }
        });
        queuePane.addEventListener('dragover', (e) => {
            if (dragFrom < 0) return;
            e.preventDefault();
            if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
            const row = e.target.closest('.ap-q-row');
            queuePane.querySelectorAll('.ap-q-row.drag-over').forEach((r) => r.classList.remove('drag-over'));
            // Autoplay rows aren't reorderable, so never highlight them as a target.
            if (row && !row.classList.contains('dragging') && !row.classList.contains('is-autoplay')) {
                row.classList.add('drag-over');
            }
        });
        queuePane.addEventListener('drop', (e) => {
            if (dragFrom < 0) return;
            e.preventDefault();
            const row = e.target.closest('.ap-q-row');
            if (row && !row.classList.contains('is-autoplay')) {
                const to = parseInt(row.dataset.idx, 10);
                if (!isNaN(to) && to !== dragFrom) send('move', { from: dragFrom, to: to });
            }
            cleanupDrag();
        });
        queuePane.addEventListener('dragend', cleanupDrag);

        // Volume
        volSlider.addEventListener('input', () => {
            volTouched = performance.now();
            volVal.textContent = `${volSlider.value}%`;
        });
        let volTimer = null;
        volSlider.addEventListener('change', () => {
            if (!canControl) { showToast('error', 'Join the voice channel to control playback.'); return; }
            clearTimeout(volTimer);
            volTimer = setTimeout(() => send('volume', { value: parseInt(volSlider.value, 10) }), 120);
        });

        // Seeking on the progress bar (click + drag, mouse + touch).
        function barRatio(clientX) {
            const rect = bar.getBoundingClientRect();
            return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        }
        function pointerX(e) { return e.touches ? e.touches[0].clientX : e.clientX; }
        function startSeek(e) {
            if (!canControl || isStream || !duration) return;
            seeking = true;
            seekPreviewMs = barRatio(pointerX(e)) * duration;
            curEl.textContent = fmtTime(seekPreviewMs);
            e.preventDefault();
        }
        function moveSeek(e) {
            if (!seeking) return;
            seekPreviewMs = barRatio(pointerX(e)) * duration;
            const ratio = seekPreviewMs / duration;
            fill.style.width = `${ratio * 100}%`;
            knob.style.left = `${ratio * 100}%`;
            curEl.textContent = fmtTime(seekPreviewMs);
        }
        function endSeek() {
            if (!seeking) return;
            const ms = Math.round(seekPreviewMs);
            seeking = false;
            serverPos = ms; serverAt = performance.now();
            send('seek', { position: ms });
        }
        bar.addEventListener('mousedown', startSeek);
        window.addEventListener('mousemove', moveSeek);
        window.addEventListener('mouseup', endSeek);
        bar.addEventListener('touchstart', startSeek, { passive: false });
        window.addEventListener('touchmove', moveSeek, { passive: false });
        window.addEventListener('touchend', endSeek);

        // Tabs
        root.querySelectorAll('.ap-tab').forEach((tab) => {
            tab.addEventListener('click', () => {
                root.querySelectorAll('.ap-tab').forEach((t) => t.classList.toggle('is-active', t === tab));
                const which = tab.dataset.tab;
                queuePane.classList.toggle('is-active', which === 'queue');
                historyPane.classList.toggle('is-active', which === 'history');
                lyricsPane.classList.toggle('is-active', which === 'lyrics');
                if (which === 'lyrics' && lyricActive >= 0 && lyricEls[lyricActive]) {
                    centerLyric(lyricEls[lyricActive]);
                }
            });
        });

        // ── Polling ─────────────────────────────────────────────────
        let pollTimer = null;
        async function poll() {
            try {
                const r = await fetch(opts.statusUrl, { headers: { Accept: 'application/json' } });
                const d = await r.json();
                if (!d || !d.ok) return;
                if (typeof opts.onStatus === 'function') opts.onStatus(d);

                canControl = !!d.can_control;
                setControlAvail();

                if (d.active && d.now_playing) {
                    player.dataset.state = 'live';
                    renderTrack(d.now_playing);
                    if (!dragPaused) renderQueue(d.queue);
                    renderHistory(d.history);
                } else if (d.active) {
                    // Connected but nothing playing.
                    player.dataset.state = 'idle';
                    q('.ap-idle-title').textContent = 'Connected';
                    q('.ap-idle-sub').textContent = 'In a voice channel, but nothing is playing.';
                    trackKey = null;
                } else {
                    player.dataset.state = 'idle';
                    q('.ap-idle-title').textContent = 'Nothing playing';
                    q('.ap-idle-sub').textContent = 'No active music session in this server.';
                    trackKey = null;
                }
            } catch (_) {
                /* transient — keep last good render */
            }
        }

        function startPolling(interval) {
            poll();
            clearInterval(pollTimer);
            pollTimer = setInterval(poll, interval || 5000);
        }

        startPolling(5000);
        requestAnimationFrame(frame);

        // Back off when the tab is hidden; refresh immediately on return.
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) { clearInterval(pollTimer); }
            else { startPolling(5000); }
        });

        return { poll, refresh: poll };
    }

    window.PercyPlayer = { mount };
})();
