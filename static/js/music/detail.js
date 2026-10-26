/* Music app detail pages: artist, album/single, saved playlist and Liked Songs.
 *
 * Routes (hash, so back/forward and deep links work with no server support):
 *   #artist/<name>?u=<artist link>    #album?u=<album link>&n=<title>&a=<artist>    #playlist/<id>    #liked
 * Data comes from the BFF (/artist, /album, /account/music/playlists/:id), which is Percy's Lavalink
 * view of the world: no release dates, no full discographies. Anything Lavalink does not give
 * (year, track counts, a source link) is simply left out of the page instead of invented.
 * app.js owns routing and exposes what this file needs on window.mapp. */
(function () {
    'use strict';

    const M = window.mapp;
    if (!M || !document.getElementById('mapp')) return;
    const { api, toast, ico, esc, art, grad, fmt, empty, skeleton, BASE } = M;
    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    const q = (s) => `“${s}”`;
    const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

    // ── Small helpers ────────────────────────────────────────────────────
    /* "1 hr 5 min" / "23 min 14 sec": the total length line of a header. */
    function longDur(ms) {
        const s = Math.round((ms || 0) / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
        if (h) return `${h} hr ${m} min`;
        return m ? `${m} min ${s % 60} sec` : `${s} sec`;
    }
    const cache = new Map(); // path -> {at, r}: back/forward feels instant; failures are never kept
    async function cached(path) {
        const hit = cache.get(path);
        if (hit && Date.now() - hit.at < 60000) return hit.r;
        const r = await api(path);
        if (r.ok) {
            if (cache.size >= 40) cache.delete(cache.keys().next().value);
            cache.set(path, { at: Date.now(), r });
        }
        return r;
    }
    function copy(text, what) {
        const done = () => toast('success', `Copied the link to ${what}`);
        const fallback = () => {
            const ta = document.createElement('textarea');
            ta.value = text; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;opacity:0';
            document.body.appendChild(ta); ta.select();
            let ok = false;
            try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
            ta.remove();
            if (ok) done(); else toast('error', 'Could not copy the link. Copy it from the address bar.');
        };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
    }

    // ── Header tint: the cover's average colour, sampled client-side ─────
    /* Cross-origin covers only sample when the CDN allows it; otherwise the header keeps the
       placeholder colour (--gt of its g0..g7 class), which is also what an artless page gets. */
    function tint(header, url) {
        if (!url) return;
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            try {
                const c = document.createElement('canvas');
                c.width = c.height = 8;
                const x = c.getContext('2d', { willReadFrequently: true });
                x.drawImage(img, 0, 0, 8, 8);
                const d = x.getImageData(0, 0, 8, 8).data;
                let r = 0, g = 0, b = 0, n = 0;
                for (let i = 0; i < d.length; i += 4) {
                    const lum = (d[i] + d[i + 1] + d[i + 2]) / 3;
                    if (d[i + 3] < 200 || lum < 24 || lum > 235) continue; // skip bars, glare and transparency
                    r += d[i]; g += d[i + 1]; b += d[i + 2]; n++;
                }
                if (n) header.style.setProperty('--tint', `rgb(${Math.round(r / n)}, ${Math.round(g / n)}, ${Math.round(b / n)})`);
            } catch (_) { /* tainted canvas: keep the fallback */ }
        };
        img.src = url;
    }

    // ── Markup ───────────────────────────────────────────────────────────
    /* o: {kind, title, meta (html), artwork, round, icon, acts (html)} */
    function headerHtml(o) {
        const cover = o.icon
            ? `<span class="mapp-art mapp-art--fill g${grad(o.title)}" aria-hidden="true">${ico(o.icon)}</span>`
            : art(o.artwork, o.title, 'mapp-art--fill');
        const edit = o.coverEdit
            ? `<button type="button" class="mapp-dh-cover-edit" data-d="pl-cover" aria-haspopup="menu" aria-label="Change cover" title="Change cover">${ico('image')}<span>Change cover</span></button>`
            : '';
        return `<header class="mapp-dh g${grad(o.title)}">
            <div class="mapp-dh-cover${o.round ? ' is-round' : ''}">${cover}${edit}</div>
            <div class="mapp-dh-text">
                <p class="mapp-dh-kind">${esc(o.kind)}</p>
                <h1 class="mapp-dh-title" title="${esc(o.title)}">${esc(o.title)}</h1>
                ${o.meta ? `<p class="mapp-dh-meta">${o.meta}</p>` : ''}
                <div class="mapp-dh-acts">${o.acts || ''}</div>
            </div>
        </header>`;
    }
    const actsHtml = (opt) => `
        <button type="button" class="mapp-pill" data-d="play"${opt.noPlay ? ' disabled' : ''}>${ico('play')}Play</button>
        <button type="button" class="mapp-ibtn mapp-ibtn--ring" data-d="queue" aria-label="Add all to queue" title="Add all to queue"${opt.noPlay ? ' disabled' : ''}>${ico('queue')}</button>
        <button type="button" class="mapp-ibtn mapp-ibtn--ring" data-d="copy" aria-label="Copy link" title="Copy link">${ico('link')}</button>${opt.extra || ''}`;

    function headerSkeleton(kind, title, round) {
        return `<header class="mapp-dh g${grad(title)}" aria-busy="true">
            <div class="mapp-dh-cover${round ? ' is-round' : ''}"><span class="mapp-art mapp-art--fill mapp-skel-block" aria-hidden="true"></span></div>
            <div class="mapp-dh-text"><p class="mapp-dh-kind">${esc(kind)}</p><h1 class="mapp-dh-title">${esc(title || '')}</h1>
            <div class="mapp-skel-line" style="--w:220px"></div></div></header>`;
    }

    /* A numbered track row. t: Percy track dict, or {uri, title, sub} for a saved link. */
    function dRow(t, i, o) {
        const ttl = esc(t.title);
        const album = t.album && t.album.name ? t.album : null;
        const cover = o.icon ? `<span class="mapp-art mapp-art--sm g${grad(t.title)}" aria-hidden="true">${ico(o.icon)}</span>` : art(t.artwork, t.title, 'mapp-art--sm');
        const sub = t.sub ? esc(t.sub) : M.subHtml(t, o.withAlbum);
        const meta = o.albumCol ? `<span class="mapp-row-meta">${album && M.albumHref(album, t.author) ? `<a class="mapp-ln" href="${esc(M.albumHref(album, t.author))}">${esc(album.name)}</a>` : ''}</span>` : '';
        return `<li class="mapp-row mapp-row--d${o.albumCol ? ' has-meta' : ''}" data-uri="${esc(t.uri || '')}" data-title="${ttl}" data-author="${esc(t.author || '')}" data-artwork="${esc(t.artwork || '')}"${t.uri ? ' draggable="true"' : ''}${t.artist_url ? ` data-artist-url="${esc(t.artist_url)}"` : ''}${album && album.url ? ` data-album-url="${esc(album.url)}" data-album-name="${esc(album.name)}"` : ''}>
            <span class="mapp-row-idx"><span>${i + 1}</span><button type="button" data-act="playnow" aria-label="Play ${ttl} now" title="Play now">${ico('play')}</button></span>
            ${cover}
            <span class="mapp-row-text"><span class="mapp-row-title">${ttl}</span><span class="mapp-row-sub">${sub}</span></span>
            ${meta}
            <span class="mapp-row-acts">
                <button type="button" class="mapp-ibtn" data-d="more" aria-haspopup="menu" aria-label="More options for ${ttl}" title="More">${ico('more')}</button>
                <button type="button" class="mapp-ibtn mapp-like" data-act="like" aria-pressed="false" aria-label="Like ${ttl}" title="Like">${ico('heart')}</button>
            </span>
            <span class="mapp-row-dur">${t.is_stream ? 'LIVE' : t.duration ? fmt(t.duration) : ''}</span>
        </li>`;
    }
    const rows = (list, o) => `<ol class="mapp-rows">${list.map((t, i) => dRow(t, i, o)).join('')}</ol>`;

    function errorState(box, what, r, retry) {
        box.innerHTML = empty('alert', r.status === 404 ? `${what} not found` : `Could not load ${what.toLowerCase()}`,
            r.status === 404 ? 'It may be unavailable at the source, or the link has changed.' : r.error,
            `<button type="button" class="mapp-btn" data-d="retry">Try again</button><button type="button" class="mapp-btn mapp-btn--ghost" data-hist="-1">Go back</button>`, '', true);
        toast('error', r.status === 404 ? `${what} not found` : r.error);
        box.dataset.retry = retry || '';
    }

    // ── Playing a whole collection ───────────────────────────────────────
    /* One request when the collection has a link Lavalink resolves (album, playlist, artist top
       tracks): Percy queues every track. Otherwise tracks go in one at a time, in order. */
    async function queueTracks(tracks, link, label) {
        const why = M.queueHint();
        if (why) { toast('info', why); return false; }
        const list = tracks.filter((t) => t.uri);
        if (!link && !list.length) { toast('error', 'None of these tracks has a link to play.'); return false; }
        let n = 0;
        if (link) {
            const r = await api(`${BASE}/play`, { method: 'POST', body: { query: link } });
            if (r.ok) n = (r.data.queued || []).length || list.length;
            else if (!list.length) { toast('error', r.error); return false; }
        }
        if (!n) {
            for (const t of list) {
                const r = await api(`${BASE}/play`, { method: 'POST', body: { query: t.uri } });
                if (!r.ok) { toast('error', n ? `${r.error} (${plural(n, 'song')} were queued)` : r.error); return n > 0; }
                n++;
            }
        }
        toast('success', `Queued ${plural(n, 'song')} from ${q(label)}`);
        return true;
    }
    /* Play: a DJ with a running player jumps to the first track and the rest follow; anyone else
       (or an idle player) just queues the collection, which starts it when nothing is playing. */
    async function playCollection(c) {
        const st = M.status();
        const list = c.tracks.filter((t) => t.uri);
        if (st && st.active && M.canDestroy() && list.length > 1 && !M.queueHint()) {
            await M.playTrack(list[0], true);
            return queueTracks(list.slice(1), null, c.title);
        }
        return queueTracks(c.tracks, c.link, c.title);
    }

    let cur = null; // the page on screen: {kind, title, tracks, link, share}
    let seq = 0;
    const boxes = { artist: $('#mapp-artist'), album: $('#mapp-album'), playlist: $('#mapp-playlist') };

    // ── Artist ───────────────────────────────────────────────────────────
    let artistData = null, artistTab = 'all';
    const relHtml = (r, artist) => {
        const href = r.url ? M.albumHref(r, artist) : '';
        const label = r.kind === 'single' ? 'Single' : 'Album';
        const sub = [r.year, label, r.track_count ? plural(r.track_count, 'track') : ''].filter(Boolean).join(' · ');
        const inner = `<div class="mapp-card-art">${art(r.artwork, r.title, 'mapp-art--fill')}</div>
            <p class="mapp-card-title" title="${esc(r.title)}">${esc(r.title)}</p><p class="mapp-card-sub">${esc(sub)}</p>`;
        return href ? `<a class="mapp-card mapp-card--link" href="${esc(href)}">${inner}</a>` : `<div class="mapp-card">${inner}</div>`;
    };
    function artistBody() {
        const d = artistData, name = d.artist.name, tracks = d.top_tracks || [], rels = d.releases || [];
        const tabs = [['all', 'All'], ['releases', 'Releases'], ['tracks', 'Tracks']];
        const seg = `<div class="mapp-seg" role="tablist" aria-label="Artist sections">${tabs.map(([k, l]) =>
            `<button type="button" role="tab" id="mapp-atab-${k}" data-d="tab" data-tab="${k}" aria-selected="${artistTab === k}" tabindex="${artistTab === k ? 0 : -1}">${l}</button>`).join('')}</div>`;
        const trackList = (n) => (tracks.length ? rows(tracks.slice(0, n), { albumCol: true }) : empty('music', 'No tracks yet', 'Nothing came back for this artist.', '', 'inline'));
        const grid = (n) => (rels.length
            ? `<div class="mapp-grid mapp-grid--rel">${rels.slice(0, n).map((r) => relHtml(r, name)).join('')}</div>`
            : empty('disc', 'No releases found', 'The source did not list any albums or singles for this artist.', '', 'inline'));
        let body;
        if (artistTab === 'releases') body = `<section class="mapp-dsec"><h2>Albums &amp; singles</h2>${grid(1000)}</section>`;
        else if (artistTab === 'tracks') body = `<section class="mapp-dsec"><h2>Tracks</h2>${trackList(1000)}</section>`;
        else {
            body = `<section class="mapp-dsec"><div class="mapp-dsec-head"><h2>Tracks</h2>${tracks.length > 5 ? '<button type="button" class="mapp-link" data-d="tab" data-tab="tracks">Show all</button>' : ''}</div>${trackList(5)}</section>
                <section class="mapp-dsec"><div class="mapp-dsec-head"><h2>Albums &amp; singles</h2>${rels.length > 6 ? '<button type="button" class="mapp-link" data-d="tab" data-tab="releases">Show all</button>' : ''}</div>${grid(6)}</section>`;
        }
        return `${seg}<div class="mapp-dbody" role="tabpanel" aria-labelledby="mapp-atab-${artistTab}">${body}</div>`;
    }
    function renderArtist() {
        const box = boxes.artist, d = artistData;
        const rels = (d.releases || []).length, n = (d.top_tracks || []).length;
        const meta = [rels ? plural(rels, 'release') : '', n ? plural(n, 'top track') : ''].filter(Boolean).join(' · ');
        box.innerHTML = headerHtml({ kind: 'Artist', title: d.artist.name, artwork: d.artist.artwork, round: true, meta: esc(meta), acts: actsHtml({ noPlay: !n }) }) + `<div id="mapp-abody">${artistBody()}</div>`;
        tint($('.mapp-dh', box), d.artist.artwork);
        M.refresh();
    }
    async function loadArtist(name, url) {
        const mine = ++seq, box = boxes.artist;
        if (!box) return;
        cur = null;
        box.dataset.retry = `artist|${name}|${url || ''}`;
        box.innerHTML = headerSkeleton('Artist', name, true) + `<div class="mapp-dbody">${skeleton(5)}</div>`;
        const r = await cached(`${BASE}/artist?name=${encodeURIComponent(name)}${url ? '&url=' + encodeURIComponent(url) : ''}`);
        if (mine !== seq) return;
        if (!r.ok) { errorState(box, 'Artist', r, box.dataset.retry); return; }
        artistData = r.data;
        if (!artistData.artist.name) artistData.artist.name = name;
        artistTab = 'all';
        document.title = `${artistData.artist.name} | Percy Music`;
        cur = { kind: 'artist', title: artistData.artist.name, tracks: artistData.top_tracks || [], link: artistData.artist.url || '', share: artistData.artist.url || location.href };
        renderArtist();
    }

    // ── Album / single ───────────────────────────────────────────────────
    async function loadAlbum(url, hintTitle, hintArtist) {
        const mine = ++seq, box = boxes.album;
        if (!box) return;
        cur = null;
        if (!url) { box.innerHTML = empty('disc', 'No album selected', 'Open an album from a track or an artist page.', '<button type="button" class="mapp-btn" data-hist="-1">Go back</button>'); return; }
        box.dataset.retry = `album|${url}|${hintTitle || ''}|${hintArtist || ''}`;
        box.innerHTML = headerSkeleton('Album', hintTitle || 'Loading', false) + `<div class="mapp-dbody">${skeleton(8)}</div>`;
        const r = await cached(`${BASE}/album?url=${encodeURIComponent(url)}`);
        if (mine !== seq) return;
        if (!r.ok) { errorState(box, 'Album', r, box.dataset.retry); return; }
        const a = r.data.album || {}, tracks = r.data.tracks || [];
        const kind = a.kind === 'single' ? 'Single' : 'Album';
        const artists = (a.artists || []).map((n) => `<a class="mapp-ln" href="${esc(M.artistHref(n, ''))}">${esc(n)}</a>`).join(', ');
        const meta = [a.year, artists, tracks.length ? plural(tracks.length, 'track') : '', a.total_ms ? longDur(a.total_ms) : ''].filter(Boolean).join(' <span class="mapp-sep">·</span> ');
        document.title = `${a.title || kind} | Percy Music`;
        cur = { kind: 'album', title: a.title || kind, tracks, link: a.url || url, share: a.url || url };
        box.innerHTML = headerHtml({ kind, title: a.title || kind, artwork: a.artwork, meta, acts: actsHtml({ noPlay: !tracks.length }) })
            + `<div class="mapp-dbody">${tracks.length ? rows(tracks, {}) : empty('music', 'This album is empty', 'No playable tracks came back for it.', '', 'inline')}</div>`;
        tint($('.mapp-dh', box), a.artwork);
        M.refresh();
    }

    // ── Saved playlist and Liked Songs ───────────────────────────────────
    const editBtns = () => `
        <button type="button" class="mapp-ibtn mapp-ibtn--ring" data-d="pl-rename" aria-label="Rename playlist" title="Rename">${ico('pencil')}</button>
        <button type="button" class="mapp-ibtn mapp-ibtn--ring" data-d="pl-delete" aria-label="Delete playlist" title="Delete playlist">${ico('trash')}</button>`;
    let shownPlaylist = null; // the Percy payload behind the open playlist page
    function playlistPage(box, p) {
        shownPlaylist = p;
        const tracks = (p.tracks || []).map((t) => ({ uri: t.url, title: t.name || t.url, author: t.author || '', artwork: t.artwork || '' }));
        const n = tracks.length;
        cur = { kind: 'playlist', title: p.name || 'Playlist', tracks, link: '', share: location.href, id: String(p.id) };
        box.innerHTML = `<div class="mapp-dtop"><a class="mapp-link" href="#playlists">${ico('arrow-left')}Playlists</a></div>`
            + headerHtml({ kind: 'Playlist', title: cur.title, artwork: p.cover, icon: p.cover ? '' : 'playlist', coverEdit: true, meta: esc(plural(n, 'track')), acts: actsHtml({ noPlay: !n, extra: editBtns() }) })
            + `<div class="mapp-dbody">${n ? rows(tracks, {}) : empty('playlist', 'This playlist is empty', 'Open the ••• menu on any song and pick Add to playlist.', '', 'inline')}</div>`;
        tint($('.mapp-dh', box), p.cover);
        M.refresh();
    }
    async function loadPlaylist(id) {
        const mine = ++seq, box = boxes.playlist;
        if (!box) return;
        cur = null;
        box.dataset.retry = `playlist|${id}`;
        box.innerHTML = headerSkeleton('Playlist', 'Playlist', false) + `<div class="mapp-dbody">${skeleton(6)}</div>`;
        const r = await api(`/account/music/playlists/${encodeURIComponent(id || '')}`);
        if (mine !== seq) return;
        if (!r.ok) { errorState(box, 'Playlist', r, box.dataset.retry); return; }
        const p = r.data.playlist || {};
        document.title = `${p.name || 'Playlist'} | Percy Music`;
        playlistPage(box, p);
    }
    /* Liked Songs: server-rendered rows stay (JS off); only the header becomes the shared one. */
    function likedHeader() {
        const box = $('#mapp-liked-head');
        if (!box) return;
        const tracks = Array.from(M.liked, ([url, name]) => ({ uri: url, title: name || url }));
        if (cur && cur.kind === 'liked') cur.tracks = tracks;
        else cur = { kind: 'liked', title: 'Liked Songs', tracks, link: '', share: location.href };
        box.innerHTML = headerHtml({ kind: 'Playlist', title: 'Liked Songs', icon: 'heart-fill', meta: `<span id="mapp-liked-sum">${esc(plural(tracks.length, 'song'))}</span>`, acts: actsHtml({ noPlay: !tracks.length }) });
        $('.mapp-dh', box).classList.add('is-liked');
        const h = $('.mapp-dh-title', box); if (h) h.id = 'mapp-h-liked';
    }

    // ── Playlist editing (the owner's own playlists; Percy checks ownership) ──
    const saveBody = (t) => ({ name: t.title || t.uri, url: t.uri, author: t.author || null, artwork: t.artwork || null });
    function bumpCount(id, by) {
        const p = M.playlists.find((x) => String(x.id) === String(id));
        if (p) { p.track_count = Math.max(0, (p.track_count || 0) + by); M.renderPlaylists(); }
    }
    async function addToPlaylist(t, id) {
        const p = M.playlists.find((x) => String(x.id) === String(id));
        const name = p ? p.name : 'the playlist';
        const r = await api(`/account/music/playlists/${encodeURIComponent(id)}/tracks`, { method: 'POST', body: saveBody(t) });
        if (!r.ok) { toast('error', r.error); return; }
        toast('success', `Added ${q(t.title)} to ${q(name)}`);
        M.refreshLibrary(); // counts and the cover a first song gives an empty playlist
        if (cur && cur.kind === 'playlist' && cur.id === String(id)) loadPlaylist(id);
    }
    async function newPlaylist(thenAdd) {
        const name = await PercyConfirm.input({ title: 'New playlist', label: 'Name', placeholder: 'Late night drive', confirmLabel: 'Create', maxLength: 100 });
        if (!name) return;
        const r = await api('/account/music/playlists', { method: 'POST', body: { name } });
        if (!r.ok) { toast('error', r.error); return; }
        const p = r.data.playlist;
        M.playlists.push(p);
        M.renderPlaylists();
        if (thenAdd) await addToPlaylist(thenAdd, p.id);
        else { toast('success', `Created ${q(p.name)}`); M.go(`playlist/${p.id}`); }
    }
    async function removeFromPlaylist(t) {
        if (!cur || cur.kind !== 'playlist') return;
        const id = cur.id;
        const r = await api(`/account/music/playlists/${encodeURIComponent(id)}/tracks/remove`, { method: 'POST', body: { url: t.uri } });
        if (!r.ok) { toast('error', r.error); return; }
        toast('success', `Removed ${q(t.title)} from ${q(cur.title)}`);
        bumpCount(id, -1);
        loadPlaylist(id);
    }
    async function updatePlaylist(body, done) {
        const id = cur.id;
        const r = await api(`/account/music/playlists/${encodeURIComponent(id)}/update`, { method: 'POST', body });
        if (!r.ok) { toast('error', r.error); return; }
        applyPlaylist(r.data.playlist, done);
    }
    function applyPlaylist(p, done) {
        const id = String(p.id);
        const s = M.playlists.find((x) => String(x.id) === id);
        if (s) { s.name = p.name; s.thumbnail = p.thumbnail; s.cover = p.cover; M.renderPlaylists(); }
        document.title = `${p.name} | Percy Music`;
        playlistPage(boxes.playlist, p);
        toast('success', done);
    }
    async function renamePlaylist() {
        const name = await PercyConfirm.input({ title: 'Rename playlist', label: 'Name', value: cur.title, confirmLabel: 'Rename', maxLength: 100 });
        if (name && name !== cur.title) await updatePlaylist({ name }, `Renamed to ${q(name)}`);
    }
    function changeCover(btn) {
        const custom = !!(shownPlaylist && shownPlaylist.thumbnail);
        popMenu(btn, [
            ['upload', 'image', 'Upload an image'],
            ['link', 'link', 'Use an image link'],
            custom ? ['reset', 'x', 'Use the first song’s cover'] : null,
        ].filter(Boolean), (k) => {
            if (k === 'upload') pickCover();
            else if (k === 'link') coverLink();
            else if (k === 'reset') M.once(`pl-cover:${cur.id}`, null, () => updatePlaylist({ thumbnail: '' }, 'Cover reset to the first song’s'));
        });
    }
    const COVER_PX = 600;
    /* Square-crop the centre and re-encode as JPEG, so any photo becomes a ~100 KB cover. */
    async function coverBlob(file) {
        const bmp = await createImageBitmap(file);
        const side = Math.min(bmp.width, bmp.height), size = Math.min(COVER_PX, side);
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = size;
        canvas.getContext('2d').drawImage(bmp, (bmp.width - side) / 2, (bmp.height - side) / 2, side, side, 0, 0, size, size);
        bmp.close();
        return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.86));
    }
    async function uploadCover(file) {
        if (!cur || cur.kind !== 'playlist') return;
        if (!/^image\//.test(file.type)) { toast('error', 'Pick an image file (JPEG, PNG, WebP or GIF).'); return; }
        if (file.size > 25 * 1024 * 1024) { toast('error', 'That image is over 25 MB. Pick a smaller one.'); return; }
        const id = cur.id;
        let blob;
        try { blob = await coverBlob(file); } catch (_) { blob = null; }
        if (!blob) { toast('error', 'That image could not be read. Try another one.'); return; }
        toast('info', 'Uploading cover…');
        let r;
        try {
            const res = await fetch(`/account/music/playlists/${encodeURIComponent(id)}/cover`, {
                method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'image/jpeg', Accept: 'application/json' }, body: blob,
            });
            const d = await res.json().catch(() => ({}));
            r = res.ok && d.ok !== false ? { ok: true, data: d } : { ok: false, error: d.error || 'Could not upload the cover.' };
        } catch (_) {
            r = { ok: false, error: 'Could not reach the server. Check your connection.' };
        }
        if (!r.ok) { toast('error', r.error); return; }
        applyPlaylist(r.data.playlist, 'Cover updated');
    }
    function pickCover() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.addEventListener('change', () => { if (input.files && input.files[0]) M.once(`pl-cover:${cur.id}`, null, () => uploadCover(input.files[0])); });
        input.click();
    }
    async function coverLink() {
        const now = (shownPlaylist && shownPlaylist.thumbnail) || '';
        const shown = now.includes('/account/music/covers/') ? '' : now; // an upload has no link worth editing
        const url = await PercyConfirm.input({
            title: 'Playlist cover', label: 'Image link', value: shown, type: 'url', placeholder: 'https://…/cover.jpg',
            hint: 'Paste a link to an image. Leave it empty to use the first song’s cover.', confirmLabel: 'Save cover', required: false,
        });
        if (url === null || url === shown) return;
        if (url && !/^https?:\/\//i.test(url)) { toast('error', 'The cover must be an http(s) link to an image.'); return; }
        await updatePlaylist({ thumbnail: url }, url ? 'Cover updated' : 'Cover reset to the first song’s');
    }
    async function deletePlaylist() {
        const ok = await PercyConfirm.ask({ title: `Delete ${q(cur.title)}?`, consequence: `The playlist and its ${plural(cur.tracks.length, 'song')} are removed, here and in Discord.`, undo: null, confirmLabel: 'Delete playlist', danger: true });
        if (!ok) return;
        const id = cur.id;
        const r = await api(`/account/music/playlists/${encodeURIComponent(id)}/delete`, { method: 'POST', body: {} });
        if (!r.ok) { toast('error', r.error); return; }
        const i = M.playlists.findIndex((x) => String(x.id) === id);
        if (i >= 0) M.playlists.splice(i, 1);
        M.renderPlaylists();
        toast('success', `Deleted ${q(cur.title)}`);
        M.go('playlists');
    }

    // ── Drag a track onto Liked Songs or a playlist in the sidebar ───────
    const TRACK_TYPE = 'application/x-percy-track';
    const dropTarget = (el) => el && el.closest && el.closest('[data-drop="liked"], [data-drop-pl]');
    const carriesTrack = (e) => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes(TRACK_TYPE);
    document.addEventListener('dragstart', (e) => {
        const el = e.target.closest && e.target.closest('[draggable="true"][data-uri]');
        if (!el || !el.dataset.uri || !e.dataTransfer) return;
        const t = M.trackOf(el);
        e.dataTransfer.setData(TRACK_TYPE, JSON.stringify(t));
        e.dataTransfer.setData('text/uri-list', t.uri);
        if (e.dataTransfer.effectAllowed !== 'copyMove') e.dataTransfer.effectAllowed = 'copy';
        document.documentElement.classList.add('mapp-dragging-track');
    });
    document.addEventListener('dragend', () => {
        document.documentElement.classList.remove('mapp-dragging-track');
        $$('.is-drop-over').forEach((n) => n.classList.remove('is-drop-over'));
    });
    document.addEventListener('dragover', (e) => {
        const target = dropTarget(e.target);
        if (!target || !carriesTrack(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        target.classList.add('is-drop-over');
    });
    document.addEventListener('dragleave', (e) => {
        const target = dropTarget(e.target);
        if (target && !target.contains(e.relatedTarget)) target.classList.remove('is-drop-over');
    });
    document.addEventListener('drop', (e) => {
        const target = dropTarget(e.target);
        if (!target || !carriesTrack(e)) return;
        e.preventDefault();
        target.classList.remove('is-drop-over');
        document.documentElement.classList.remove('mapp-dragging-track');
        let t;
        try { t = JSON.parse(e.dataTransfer.getData(TRACK_TYPE)); } catch (_) { return; }
        if (!t || !t.uri) return;
        if (target.dataset.drop === 'liked') M.likeTrack(t);
        else addToPlaylist(t, target.dataset.dropPl);
    });

    document.addEventListener('dragover', (e) => {
        const cover = e.target.closest && e.target.closest('#mapp-playlist .mapp-dh-cover');
        if (!cover || !e.dataTransfer || !Array.from(e.dataTransfer.types).includes('Files')) return;
        e.preventDefault();
        cover.classList.add('is-drop');
    });
    document.addEventListener('dragleave', (e) => {
        const cover = e.target.closest && e.target.closest('.mapp-dh-cover');
        if (cover && !cover.contains(e.relatedTarget)) cover.classList.remove('is-drop');
    });
    document.addEventListener('drop', (e) => {
        const cover = e.target.closest && e.target.closest('#mapp-playlist .mapp-dh-cover');
        if (!cover || !e.dataTransfer || !e.dataTransfer.files.length) return;
        e.preventDefault();
        cover.classList.remove('is-drop');
        const file = e.dataTransfer.files[0];
        M.once(`pl-cover:${cur && cur.id}`, null, () => uploadCover(file));
    });

    // ── Routing ──────────────────────────────────────────────────────────
    function onRoute(d) {
        closeMenu();
        if (d.view === 'artist') loadArtist(d.arg || '', d.params.get('u') || '');
        else if (d.view === 'album') loadAlbum(d.params.get('u') || '', d.params.get('n') || '', d.params.get('a') || '');
        else if (d.view === 'playlist') loadPlaylist(d.arg);
        else if (d.view === 'liked') { seq++; cur = null; likedHeader(); }
        else if (d.view === 'home' || d.view === 'search' || d.view === 'history') seq++;
    }
    document.addEventListener('mapp:route', (e) => onRoute(e.detail));
    document.addEventListener('mapp:liked', () => { if ($('[data-view="liked"]:not([hidden])')) likedHeader(); });

    // ── The ••• row menu ─────────────────────────────────────────────────
    let menu = null, menuFor = null;
    function closeMenu() {
        if (menu) { menu.remove(); menu = null; }
        if (menuFor) { menuFor.setAttribute('aria-expanded', 'false'); menuFor = null; }
    }
    function placeMenu(btn) {
        document.body.appendChild(menu);
        const r = btn.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
        menu.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.right - w))}px`;
        menu.style.top = `${r.bottom + h + 12 > innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4}px`;
        menuFor = btn;
    }
    /* A small menu of [key, icon, label] items; onPick gets the key. Same look and keys as ••• . */
    function popMenu(btn, items, onPick) {
        const had = menuFor === btn;
        closeMenu();
        if (had) return;
        menu = document.createElement('div');
        menu.className = 'mapp-menu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = items.map(([k, i, l]) => `<button type="button" role="menuitem" data-m="${esc(k)}">${ico(i)}<span>${esc(l)}</span></button>`).join('');
        placeMenu(btn);
        btn.setAttribute('aria-expanded', 'true');
        const f = $('button', menu); if (f) f.focus({ preventScroll: true });
        menu.addEventListener('click', (e) => {
            e.stopPropagation();
            const b = e.target.closest('[data-m]');
            if (!b) return;
            closeMenu();
            onPick(b.dataset.m);
        });
    }
    function openMenu(btn, sub) {
        const had = menuFor === btn;
        closeMenu();
        if (had && !sub) return;
        const t = M.trackOf(btn);
        const first = (t.author || '').split(',')[0].trim().replace(/ - Topic$/, '');
        const inPlaylist = cur && cur.kind === 'playlist' && btn.closest('#mapp-playlist');
        const items = sub === 'playlists'
            ? [...M.playlists.map((p) => [`pl:${p.id}`, 'playlist', p.name]), ['pl-new', 'plus', 'New playlist']]
            : [
                ['playnow', 'play', 'Play now'], ['add', 'plus', 'Add to queue'],
                t.uri ? ['addpl', 'playlist', 'Add to playlist'] : null,
                inPlaylist && t.uri ? ['rmpl', 'x', 'Remove from this playlist'] : null,
                first ? ['artist', 'user', 'Go to artist'] : null,
                t.albumUrl ? ['album', 'disc', 'Go to album'] : null,
                t.uri ? ['copy', 'link', 'Copy link'] : null,
            ].filter(Boolean);
        menu = document.createElement('div');
        menu.className = 'mapp-menu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = items.map(([k, i, l]) => `<button type="button" role="menuitem" data-m="${esc(k)}">${ico(i)}<span>${esc(l)}</span></button>`).join('');
        placeMenu(btn);
        btn.setAttribute('aria-expanded', 'true');
        const f = $('button', menu); if (f) f.focus({ preventScroll: true });
        menu.addEventListener('click', (e) => {
            // The document click handler closes menus on outside clicks; this menu is about to be
            // removed (and maybe replaced by the playlist menu), so the click must stop here.
            e.stopPropagation();
            const b = e.target.closest('[data-m]');
            if (!b) return;
            const k = b.dataset.m;
            closeMenu();
            if (k === 'addpl') { openMenu(btn, 'playlists'); return; }
            if (k === 'pl-new') { newPlaylist(t); return; }
            if (k.startsWith('pl:')) { addToPlaylist(t, k.slice(3)); return; }
            if (k === 'rmpl') { removeFromPlaylist(t); return; }
            if (k === 'playnow') M.once(`play:${t.uri || t.title}`, null, () => M.playTrack(t, true));
            else if (k === 'add') M.once(`add:${t.uri || t.title}`, null, () => M.playTrack(t, false));
            else if (k === 'artist') M.go(M.artistHref(first, t.artistUrl).slice(1));
            else if (k === 'album') M.go(M.albumHref({ url: t.albumUrl, name: t.albumName }, t.author).slice(1));
            else if (k === 'copy') copy(t.uri, q(t.title));
        });
        menu.addEventListener('keydown', (e) => {
            const bs = $$('button', menu), i = bs.indexOf(document.activeElement);
            if (e.key === 'ArrowDown') { e.preventDefault(); bs[(i + 1) % bs.length].focus(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
            else if (e.key === 'Escape') { e.preventDefault(); const b = menuFor; closeMenu(); if (b) b.focus(); }
            else if (e.key === 'Tab') closeMenu();
        });
    }
    window.addEventListener('resize', closeMenu);
    $('#mapp-main').addEventListener('scroll', closeMenu, { passive: true });

    // ── Clicks ───────────────────────────────────────────────────────────
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-pl="new"]')) { newPlaylist(null); return; }
        const b = e.target.closest('[data-d]');
        if (!b) {
            if (menu && !menu.contains(e.target)) closeMenu();
            return;
        }
        const act = b.dataset.d;
        if (act === 'more') { e.stopPropagation(); openMenu(b); return; }
        // The player bar's playlist button goes straight to the playlist list (a second click closes it).
        if (act === 'addpl') { e.stopPropagation(); if (menuFor === b) closeMenu(); else openMenu(b, 'playlists'); return; }
        if (menu) closeMenu();
        if (b.disabled) return;
        if (act === 'tab') {
            artistTab = b.dataset.tab;
            const body = $('#mapp-abody');
            if (body && artistData) { body.innerHTML = artistBody(); M.refresh(); const t = $(`#mapp-atab-${artistTab}`); if (t && document.activeElement === b) t.focus(); }
            return;
        }
        if (act === 'retry') {
            const box = b.closest('[data-retry]') || b.closest('.mapp-dpage');
            const [kind, a1, a2, a3] = ((box && box.dataset.retry) || '').split('|');
            cache.clear();
            if (kind === 'artist') loadArtist(a1, a2); else if (kind === 'album') loadAlbum(a1, a2, a3); else if (kind === 'playlist') loadPlaylist(a1);
            return;
        }
        if (!cur) return;
        if (cur.kind === 'playlist' && act === 'pl-cover') { e.stopPropagation(); changeCover(b); return; }
        if (cur.kind === 'playlist' && act.startsWith('pl-')) {
            const run = { 'pl-rename': renamePlaylist, 'pl-delete': deletePlaylist }[act];
            if (run) M.once(`${act}:${cur.id}`, b, run);
            return;
        }
        if (act === 'play') M.once(`dplay:${cur.title}`, b, () => playCollection(cur));
        else if (act === 'queue') M.once(`dqueue:${cur.title}`, b, () => queueTracks(cur.tracks, cur.link, cur.title));
        else if (act === 'copy') copy(cur.share || location.href, q(cur.title));
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && menu) { closeMenu(); return; }
        const tab = e.target.closest && e.target.closest('[role="tab"][data-d="tab"]');
        if (!tab || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
        const ts = $$('[data-d="tab"][role="tab"]', tab.parentElement), i = ts.indexOf(tab);
        const nxt = ts[(i + (e.key === 'ArrowRight' ? 1 : -1) + ts.length) % ts.length];
        e.preventDefault(); nxt.click(); nxt.focus();
    });

    // app.js routed before this file loaded: replay the current route for the views it owns.
    M.route();
})();
