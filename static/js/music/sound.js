/* Music app: the Sound panel (equalizer + effects) and the active-effects chips under now playing.
 * Borrows api/toast/viewer from app.js through window.mapp. Percy decides who may change the sound
 * (the viewer's can_control); this script only mirrors that, applies changes optimistically and rolls back on error.
 *   GET  /dashboard/music/:gid/audio        state + preset names + EQ preset gains
 *   POST /dashboard/music/:gid/audio        {effect,enabled} | {speed,pitch,rotation,low_pass} | {reset}
 *   POST /dashboard/music/:gid/equalizer    {preset} | {bands}                                          */
(function () {
    'use strict';
    const M = window.mapp;
    const panel = document.getElementById('mapp-sound');
    if (!M || !panel || !M.hasGuild) return;

    const { api, toast, ico, esc } = M;
    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    const launcher = $('[data-act="sound"]');
    const fxBox = document.getElementById('mapp-fx');
    const paneEq = document.getElementById('mapp-pane-eq');
    const paneFx = document.getElementById('mapp-pane-fx');

    const BAND_HZ = ['25', '40', '63', '100', '160', '250', '400', '630', '1k', '1.6k', '2.5k', '4k', '6.3k', '10k', '16k'];
    const GAIN_MIN = -0.25, GAIN_MAX = 1.0;
    const EQ_LABEL = { flat: 'Flat', bassboost: 'Bass boost', treble: 'Treble', vocal: 'Vocal', pop: 'Pop', rock: 'Rock', electronic: 'Electronic', acoustic: 'Acoustic' };
    /* label, one-liner, sprite icon for each of Percy's named effects. */
    const FX = {
        nightcore: ['Nightcore', 'Faster and higher', 'zap'],
        vaporwave: ['Vaporwave', 'Slow and dreamy', 'waves'],
        slowed: ['Slowed', 'A touch slower, lower', 'clock'],
        '8d': ['8D', 'Sound circles your head', 'headphones'],
        karaoke: ['Karaoke', 'Fades out the vocals', 'mic'],
        tremolo: ['Tremolo', 'Pulsing volume', 'activity'],
        vibrato: ['Vibrato', 'Wavering pitch', 'activity'],
        lowpass: ['Low-pass', 'Muffled, bass only', 'volume'],
        distortion: ['Distortion', 'Gritty and crunchy', 'zap'],
        mono: ['Mono', 'Same signal both ears', 'users'],
    };
    const ICON_OK = new Set(['zap', 'waves', 'clock', 'headphones', 'mic', 'activity', 'volume', 'users']);

    const blank = () => ({
        active: false,
        equalizer: { preset: 'flat', bands: new Array(15).fill(0) },
        filters: {
            timescale: { speed: 1, pitch: 1, rate: 1 }, rotation: { rotation_hz: 0 }, low_pass: { smoothing: null },
        },
        effects: [],
        presets: { equalizer: Object.keys(EQ_LABEL), filters: Object.keys(FX), equalizer_bands: {} },
    });
    let state = blank();
    let loaded = false;
    let inflight = 0;                  // writes in flight or debounced: polls must not overwrite the UI
    const timers = {};
    let lastFetch = 0;
    let tab = 'eq';

    const cap = (s) => (EQ_LABEL[s] || (s ? s[0].toUpperCase() + s.slice(1) : 'Custom'));
    const clone = (o) => JSON.parse(JSON.stringify(o));
    const fx = () => state.filters || {};
    const ts = () => fx().timescale || { speed: 1, pitch: 1 };
    const rotHz = () => (fx().rotation || {}).rotation_hz || 0;
    const lowpass = () => (fx().low_pass || {}).smoothing || 0;

    // ── Who may change it ────────────────────────────────────────────────
    function lockReason() {
        const v = M.viewer(), s = M.status();
        if (!s) return 'Percy is offline right now';
        if (!s.active) return 'Start playing something to shape the sound';
        if (!v.can_control) return v.dj_mode === 1 && !v.is_manager ? 'Needs the DJ role' : M.lockHint();
        return '';
    }

    // ── Chips under now playing ──────────────────────────────────────────
    function chipsFor() {
        const out = [];
        const p = state.equalizer.preset;
        if (state.active && p !== 'flat') out.push(['EQ ' + (p ? cap(p) : 'custom'), 'equalizer']);
        (state.effects || []).forEach((k) => {
            if (k === 'timescale') out.push([`Speed ${ts().speed.toFixed(2).replace(/0$/, '')}×`, 'zap']);
            else out.push([FX[k] ? FX[k][0] : k, FX[k] ? FX[k][2] : 'zap']);
        });
        return out;
    }
    function renderChips() {
        if (!fxBox) return;
        const chips = chipsFor();
        fxBox.hidden = !chips.length;
        fxBox.innerHTML = chips.map(([t, i]) => `<button type="button" class="mapp-fx-chip" data-sound-open="${i === 'equalizer' ? 'eq' : 'fx'}" title="Open the sound panel">${ico(ICON_OK.has(i) || i === 'equalizer' ? i : 'zap')}${esc(t)}</button>`).join('');
    }

    // ── Equalizer pane ───────────────────────────────────────────────────
    function curvePath(bands, w, h) {
        const pad = 8;
        const pts = bands.map((g, i) => [((i + 0.5) / bands.length) * w, pad + (1 - (g - GAIN_MIN) / (GAIN_MAX - GAIN_MIN)) * (h - 2 * pad)]);
        let d = `M${pts[0][0]},${pts[0][1]}`;
        for (let i = 0; i < pts.length - 1; i++) { // Catmull-Rom -> cubic bezier
            const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
            d += ` C${p1[0] + (p2[0] - p0[0]) / 6},${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6},${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]},${p2[1]}`;
        }
        return d;
    }
    function drawCurve() {
        const svg = $('.mapp-eq-curve', paneEq);
        if (!svg) return;
        const w = 300, h = 160, d = curvePath(state.equalizer.bands, w, h);
        $('path.line', svg).setAttribute('d', d);
        $('path.fill', svg).setAttribute('d', `${d} L${w * (14.5 / 15)},${h} L${w * 0.5 / 15},${h} Z`);
    }
    function renderEq() {
        const names = (state.presets && state.presets.equalizer) || Object.keys(EQ_LABEL);
        const cur = state.equalizer.preset;
        paneEq.innerHTML = `
            <div class="mapp-chips" role="group" aria-label="Equalizer presets">
                ${names.map((n) => `<button type="button" class="mapp-pchip" data-eq-preset="${esc(n)}" aria-pressed="${n === cur}">${esc(cap(n))}</button>`).join('')}
            </div>
            <div class="mapp-eq">
                <div class="mapp-eq-scale" aria-hidden="true"><span style="top:0">+1</span><span style="top:123px">0</span><span style="top:152px">−¼</span></div>
                <div class="mapp-eq-stage">
                    <span class="mapp-eq-zero" aria-hidden="true"></span><svg class="mapp-eq-curve" viewBox="0 0 300 160" preserveAspectRatio="none" aria-hidden="true"><path class="fill"/><path class="line"/></svg>
                    <div class="mapp-eq-bands">
                        ${state.equalizer.bands.map((g, i) => `<label class="mapp-eq-band"><input type="range" class="mapp-eq-slider" data-band="${i}" min="${GAIN_MIN}" max="${GAIN_MAX}" step="0.01" value="${g}" aria-label="${BAND_HZ[i]} Hz" aria-valuetext="${(g * 100).toFixed(0)} percent gain"><span class="mapp-eq-hz" aria-hidden="true">${BAND_HZ[i]}</span></label>`).join('')}
                    </div>
                </div>
            </div>
            <div class="mapp-sound-foot">
                <p class="mapp-sound-note">Drag a band for a custom curve. It can take a moment to reach the voice channel.</p>
                <button type="button" class="mapp-btn" data-eq-preset="flat">Reset equalizer</button>
            </div>`;
        drawCurve();
        applyLock();
    }
    function syncEq() { // update in place so a drag is not interrupted by a re-render
        const cur = state.equalizer.preset;
        $$('[data-eq-preset].mapp-pchip', paneEq).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.eqPreset === cur)));
        $$('.mapp-eq-slider', paneEq).forEach((s) => {
            const g = state.equalizer.bands[+s.dataset.band];
            if (document.activeElement !== s || !s.matches(':active')) s.value = g;
            s.setAttribute('aria-valuetext', `${(g * 100).toFixed(0)} percent gain`);
        });
        drawCurve();
    }

    // ── Effects pane ─────────────────────────────────────────────────────
    function fmtSpeed(v) { return `${(+v).toFixed(2).replace(/0$/, '')}×`; }
    const SLIDERS = [
        { id: 'speed', label: 'Speed', min: 0.5, max: 2, step: 0.05, get: () => ts().speed, fmt: fmtSpeed },
        { id: 'pitch', label: 'Pitch', min: 0.5, max: 2, step: 0.05, get: () => ts().pitch, fmt: fmtSpeed },
        { id: 'rotation', label: 'Rotation', min: 0, max: 1, step: 0.01, get: rotHz, fmt: (v) => (+v ? `${(+v).toFixed(2)} Hz` : 'Off') },
        { id: 'low_pass', label: 'Low-pass', min: 0, max: 50, step: 0.5, get: lowpass, fmt: (v) => (+v >= 2.5 ? `${(+v).toFixed(1)}` : 'Off') },
    ];
    function renderFx() {
        const names = (state.presets && state.presets.filters) || Object.keys(FX);
        const on = new Set(state.effects || []);
        paneFx.innerHTML = `
            <div class="mapp-fx-grid" role="group" aria-label="Effect presets">
                ${names.map((n) => { const m = FX[n] || [cap(n), '', 'zap']; return `<button type="button" class="mapp-fxcard" data-fx="${esc(n)}" aria-pressed="${on.has(n)}"><span class="mapp-fxcard-ico">${ico(m[2])}</span><span class="mapp-fxcard-tx"><span class="mapp-fxcard-t">${esc(m[0])}</span><span class="mapp-fxcard-s">${esc(m[1])}</span></span></button>`; }).join('')}
            </div>
            <div class="mapp-sliders">
                ${SLIDERS.map((s) => `<div class="mapp-slider"><label for="mapp-fxs-${s.id}">${s.label}</label><input type="range" id="mapp-fxs-${s.id}" class="mapp-fxs" data-fxs="${s.id}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.get()}"><output id="mapp-fxo-${s.id}">${s.fmt(s.get())}</output></div>`).join('')}
            </div>
            <div class="mapp-sound-foot">
                <p class="mapp-sound-note">Effects stack. Reset clears every effect and the equalizer.</p>
                <button type="button" class="mapp-btn" data-fx-reset>Reset all</button>
            </div>`;
        applyLock();
    }
    function syncFx() {
        const on = new Set(state.effects || []);
        $$('.mapp-fxcard', paneFx).forEach((b) => b.setAttribute('aria-pressed', String(on.has(b.dataset.fx))));
        SLIDERS.forEach((s) => {
            const el = document.getElementById(`mapp-fxs-${s.id}`), out = document.getElementById(`mapp-fxo-${s.id}`);
            if (el && !(document.activeElement === el && el.matches(':active'))) el.value = s.get();
            if (out) out.textContent = s.fmt(s.get());
        });
    }

    // ── Disabled state ───────────────────────────────────────────────────
    function applyLock() {
        const why = lockReason();
        const lock = document.getElementById('mapp-sound-lock');
        lock.hidden = !why;
        lock.innerHTML = why ? `${ico('lock')}<span>${esc(why)}</span>` : '';
        panel.classList.toggle('is-locked', !!why);
        $$('button:not([data-sound-close]):not([data-sound-tab]), input', panel).forEach((el) => {
            el.disabled = !!why;
            if (why) el.title = why; else el.removeAttribute('title');
        });
    }

    // ── Talking to Percy ─────────────────────────────────────────────────
    function adopt(d) {
        const keepPresets = state.presets;
        state = Object.assign(blank(), d);
        if (!d.presets) state.presets = keepPresets;
        state.equalizer = d.equalizer || blank().equalizer;
        loaded = true;
    }
    function paint() { renderChips(); syncEq(); syncFx(); applyLock(); }
    async function fetchAudio(force) {
        if (!force && (inflight || Date.now() - lastFetch < 4000)) return;
        lastFetch = Date.now();
        const r = await api(`${M.BASE}/audio`);
        if (!r.ok || inflight) return;
        const first = !loaded;
        const shape = JSON.stringify([r.data.presets && r.data.presets.equalizer, r.data.presets && r.data.presets.filters]);
        const reshape = shape !== fetchAudio.shape;
        fetchAudio.shape = shape;
        adopt(r.data);
        if (first || reshape) { renderEq(); renderFx(); }
        paint();
    }
    /* Apply `mutate` to the local state now, send `req`, roll back and toast on failure. */
    async function commit(path, body, mutate, okMsg) {
        const snapshot = clone(state);
        mutate();
        paint();
        inflight++;
        const r = await api(`${M.BASE}/${path}`, { method: 'POST', body });
        inflight--;
        if (!r.ok) {
            if (!inflight) { state = snapshot; paint(); }
            toast('error', r.error);
            return false;
        }
        if (!inflight && path === 'audio') { adopt(r.data); paint(); }
        if (okMsg) toast('success', okMsg);
        lastFetch = Date.now();
        return true;
    }
    function debounced(key, fn) {
        if (!timers[key]) inflight++; // holds off polls while a drag is pending
        clearTimeout(timers[key]);
        timers[key] = setTimeout(() => { delete timers[key]; inflight--; fn(); }, 300);
    }
    const setBand = (i, g) => { state.equalizer.bands[i] = g; state.equalizer.preset = null; };

    // ── Events ───────────────────────────────────────────────────────────
    panel.addEventListener('input', (e) => {
        const t = e.target;
        if (t.classList.contains('mapp-eq-slider')) {
            setBand(+t.dataset.band, +t.value);
            drawCurve();
            $$('[data-eq-preset].mapp-pchip', paneEq).forEach((b) => b.setAttribute('aria-pressed', 'false'));
            t.setAttribute('aria-valuetext', `${(t.value * 100).toFixed(0)} percent gain`);
            debounced('eq', () => {
                const bands = state.equalizer.bands.slice();
                commit('equalizer', { bands }, () => {}, '');
            });
        } else if (t.classList.contains('mapp-fxs')) {
            const id = t.dataset.fxs, v = +t.value, def = SLIDERS.find((s) => s.id === id);
            document.getElementById(`mapp-fxo-${id}`).textContent = def.fmt(v);
            debounced('fxs-' + id, () => {
                let body;
                if (id === 'speed') body = { speed: v };
                else if (id === 'pitch') body = { pitch: v };
                else if (id === 'rotation') body = { rotation: v };
                else body = v >= 2.5 ? { low_pass: v } : { effect: 'lowpass', enabled: false };
                commit('audio', body, () => {
                    const f = fx();
                    if (id === 'speed' || id === 'pitch') f.timescale = Object.assign({}, f.timescale, { [id]: v });
                    if (id === 'rotation') f.rotation = { rotation_hz: v };
                    if (id === 'low_pass') f.low_pass = { smoothing: v >= 2.5 ? v : null };
                }, '');
            });
        }
    });
    panel.addEventListener('click', (e) => {
        const eqp = e.target.closest('[data-eq-preset]');
        if (eqp) {
            const n = eqp.dataset.eqPreset, gains = (state.presets.equalizer_bands || {})[n];
            commit('equalizer', { preset: n }, () => { state.equalizer.preset = n; if (gains) state.equalizer.bands = gains.slice(); }, `Equalizer: ${cap(n)}`);
            return;
        }
        const card = e.target.closest('[data-fx]');
        if (card) {
            const n = card.dataset.fx, was = (state.effects || []).includes(n);
            commit('audio', { effect: n, enabled: !was }, () => {
                state.effects = was ? state.effects.filter((x) => x !== n) : [...(state.effects || []), n];
            }, was ? `${FX[n][0]} off` : `${FX[n][0]} on`);
            return;
        }
        if (e.target.closest('[data-fx-reset]')) {
            commit('audio', { reset: true }, () => {
                state.effects = []; state.equalizer = { preset: 'flat', bands: new Array(15).fill(0) };
                state.filters = blank().filters;
            }, 'Sound reset');
            return;
        }
        const tabBtn = e.target.closest('[data-sound-tab]');
        if (tabBtn) setTab(tabBtn.dataset.soundTab, true);
        if (e.target.closest('[data-sound-close]')) close(true);
    });
    panel.addEventListener('keydown', (e) => {
        const t = e.target.closest('[data-sound-tab]');
        if (t && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); setTab(tab === 'eq' ? 'fx' : 'eq', true); }
    });

    function setTab(next, focus) {
        tab = next;
        $$('[data-sound-tab]', panel).forEach((b) => { const on = b.dataset.soundTab === next; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; if (on && focus) b.focus(); });
        paneEq.hidden = next !== 'eq';
        paneFx.hidden = next !== 'fx';
    }
    function open(which) {
        panel.hidden = false;
        launcher.setAttribute('aria-expanded', 'true');
        launcher.setAttribute('aria-pressed', 'true');
        if (which) setTab(which);
        fetchAudio(true);
        applyLock();
        const first = $('[data-sound-tab][aria-selected="true"]', panel);
        if (first) first.focus({ preventScroll: true });
    }
    function close(refocus) {
        panel.hidden = true;
        launcher.setAttribute('aria-expanded', 'false');
        launcher.removeAttribute('aria-pressed');
        if (refocus) launcher.focus();
    }
    launcher.addEventListener('click', () => (panel.hidden ? open() : close(false)));
    document.addEventListener('click', (e) => {
        const o = e.target.closest('[data-sound-open]');
        if (o) { open(o.dataset.soundOpen); return; }
        if (!panel.hidden && !panel.contains(e.target) && !launcher.contains(e.target) && !e.target.closest('.toast-card')) close(false);
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) close(true); });
    document.addEventListener('mapp:status', () => {
        const s = M.status();
        if (!s || !s.active) { if (state.active) { state = blank(); loaded = false; renderEq(); renderFx(); } paint(); return; }
        fetchAudio(!panel.hidden ? false : false);
        applyLock();
    });

    // ── Boot ─────────────────────────────────────────────────────────────
    renderEq(); renderFx(); setTab('eq');
    paint();
    window.mappSound = { open, close, adopt: (d) => { adopt(d); renderEq(); renderFx(); paint(); } };
    if (M.status() && M.status().active) fetchAudio(true);
})();
