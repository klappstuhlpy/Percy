/* Percy Dashboard — Music panel (setup, equalizer canvas, filters, live status).
   Expects GUILD_ID, MUSIC_INITIAL_ACTIVE, MUSIC_EQUALIZER and
   window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const base = `/dashboard/guild/${guildId}/music`;
    const initialActive = MUSIC_INITIAL_ACTIVE;

    const BANDS = ['25', '40', '63', '100', '160', '250', '400', '630', '1K', '1.6K', '2.5K', '4K', '6.3K', '10K', '16K'];
    const MIN_GAIN = -0.25, MAX_GAIN = 1.0;
    let gains = MUSIC_EQUALIZER.slice();
    let currentPreset = 'Custom';

    function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

    // Capitalize preset button labels
    document.querySelectorAll('.preset-btn').forEach(function(btn) {
        btn.textContent = capitalize(btn.textContent.trim());
    });

    // ─── Setup / Reset ──────────────────────────────────────────────
    const setupBtn = document.getElementById('run-setup-btn');
    if (setupBtn) {
        setupBtn.addEventListener('click', async function() {
            setupBtn.disabled = true;
            setupBtn.textContent = 'Setting up…';
            const channelSelect = document.getElementById('setup-channel');
            const channelId = channelSelect ? channelSelect.value : '';
            const body = channelId ? { channel_id: channelId } : {};
            try {
                const r = await fetch(`${base}/setup`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(body) });
                const d = await r.json();
                if (d.ok) {
                    showToast('success', 'Dedicated panel channel set: #' + d.channel_name);
                    setTimeout(function() { location.reload(); }, 1000);
                } else {
                    showToast('error', d.error || 'Setup failed.');
                    setupBtn.disabled = false;
                    setupBtn.textContent = 'Set dedicated channel';
                }
            } catch { showToast('error', 'Network error.'); setupBtn.disabled = false; setupBtn.textContent = 'Setup Music Panel'; }
        });
    }

    const resetBtn2 = document.getElementById('reset-setup-btn');
    if (resetBtn2) {
        resetBtn2.addEventListener('click', async function() {
            if (!confirm('Remove the dedicated panel channel? The Discord channel is deleted; the panel stays enabled as a temporary panel if it\'s turned on.')) return;
            resetBtn2.disabled = true;
            try {
                const r = await fetch(`${base}/reset`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: '{}' });
                const d = await r.json();
                if (d.ok) {
                    showToast('success', 'Dedicated channel removed.');
                    setTimeout(function() { location.reload(); }, 1000);
                } else {
                    showToast('error', d.error || 'Reset failed.');
                    resetBtn2.disabled = false;
                }
            } catch { showToast('error', 'Network error.'); resetBtn2.disabled = false; }
        });
    }

    const panelToggle = document.getElementById('panel-toggle');
    if (panelToggle) {
        panelToggle.addEventListener('change', async function() {
            try {
                const r = await fetch(`/dashboard/guild/${guildId}/config`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' },
                    body: '_section=music&' + (panelToggle.checked ? 'use_music_panel=true' : ''),
                });
                const d = await r.json();
                if (d.ok) showToast('success', 'Panel ' + (panelToggle.checked ? 'enabled' : 'disabled') + '.');
                else showToast('error', d.error || 'Failed.');
            } catch { showToast('error', 'Network error.'); }
        });
    }

    // ─── EQ Status indicator ───────────────────────────────────────
    const eqStatus = document.getElementById('eq-status');
    const eqStatusText = eqStatus ? eqStatus.querySelector('.eq-status-text') : null;
    const eqPresetName = document.getElementById('eq-preset-name');
    let statusTimer = null;

    function setEqStatus(state, text) {
        if (!eqStatus) return;
        eqStatus.className = 'eq-status ' + state;
        eqStatusText.textContent = text;
        if (statusTimer) clearTimeout(statusTimer);
        if (state === 'applied') {
            statusTimer = setTimeout(function() { setEqStatus('', ''); }, 3000);
        }
    }

    function setPresetName(name) {
        if (eqPresetName) eqPresetName.textContent = capitalize(name);
        currentPreset = name;
    }

    // ─── Equalizer canvas ──────────────────────────────────────────
    const canvas = document.getElementById('eq-canvas');
    let dragging = -1;
    var draw = function() {};
    if (canvas) {
    const ctx = canvas.getContext('2d');
    let canvasW = 0, canvasH = 0;
    const PAD_TOP = 40, PAD_BOTTOM = 30, PAD_LEFT = 55, PAD_RIGHT = 20, BAND_INSET = 12;

    function resize() {
        const rect = canvas.getBoundingClientRect();
        canvasW = rect.width;
        canvasH = rect.height;
        canvas.width = canvasW * devicePixelRatio;
        canvas.height = canvasH * devicePixelRatio;
        ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
        draw();
    }

    function gainToY(gain) {
        return PAD_TOP + (1 - (gain - MIN_GAIN) / (MAX_GAIN - MIN_GAIN)) * (canvasH - PAD_TOP - PAD_BOTTOM);
    }

    function yToGain(y) {
        const raw = MAX_GAIN - (y - PAD_TOP) / (canvasH - PAD_TOP - PAD_BOTTOM) * (MAX_GAIN - MIN_GAIN);
        return Math.max(MIN_GAIN, Math.min(MAX_GAIN, Math.round(raw * 100) / 100));
    }

    function bandX(i) {
        return PAD_LEFT + BAND_INSET + i / (BANDS.length - 1) * (canvasW - PAD_LEFT - BAND_INSET - PAD_RIGHT);
    }

    draw = function() {
        ctx.clearRect(0, 0, canvasW, canvasH);

        const gridGains = [-0.25, 0, 0.25, 0.5, 0.75, 1.0];

        // Grid lines
        ctx.strokeStyle = '#2a2a2a'; ctx.lineWidth = 1;
        for (const g of gridGains) {
            const y = gainToY(g);
            ctx.beginPath(); ctx.moveTo(PAD_LEFT, y); ctx.lineTo(canvasW - PAD_RIGHT, y); ctx.stroke();
        }

        // Zero line
        ctx.strokeStyle = '#555'; ctx.lineWidth = 1;
        const zy = gainToY(0);
        ctx.beginPath(); ctx.moveTo(PAD_LEFT, zy); ctx.lineTo(canvasW - PAD_RIGHT, zy); ctx.stroke();

        // Y-axis labels (gain values)
        ctx.fillStyle = '#888'; ctx.font = '11px JetBrains Mono, monospace'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        for (const g of gridGains) {
            const label = g >= 0 ? '+' + g.toFixed(2) : g.toFixed(2);
            ctx.fillText(label, PAD_LEFT - 12, gainToY(g));
        }

        // X-axis labels (band frequencies)
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        for (let i = 0; i < BANDS.length; i++) {
            ctx.fillText(BANDS[i], bandX(i), canvasH - PAD_BOTTOM + 12);
        }

        // Axis labels
        ctx.save();
        ctx.fillStyle = '#666'; ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
        ctx.fillText('Gain', PAD_LEFT / 2, PAD_TOP - 10);
        ctx.fillText('Hz', canvasW / 2, canvasH - 2);
        ctx.restore();

        if (!gains.length) return;

        // Build points array
        var pts = [];
        for (let i = 0; i < gains.length; i++) {
            pts.push({ x: bandX(i), y: gainToY(gains[i]) });
        }

        // Monotone cubic spline that passes through all points
        function drawSpline(points) {
            ctx.moveTo(points[0].x, points[0].y);
            if (points.length === 2) {
                ctx.lineTo(points[1].x, points[1].y);
                return;
            }
            for (let i = 0; i < points.length - 1; i++) {
                const p0 = points[Math.max(0, i - 1)];
                const p1 = points[i];
                const p2 = points[i + 1];
                const p3 = points[Math.min(points.length - 1, i + 2)];
                const cp1x = p1.x + (p2.x - p0.x) / 6;
                const cp1y = p1.y + (p2.y - p0.y) / 6;
                const cp2x = p2.x - (p3.x - p1.x) / 6;
                const cp2y = p2.y - (p3.y - p1.y) / 6;
                ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
            }
        }

        // Fill under curve
        ctx.beginPath();
        drawSpline(pts);
        ctx.lineTo(pts[pts.length - 1].x, zy);
        ctx.lineTo(pts[0].x, zy);
        ctx.closePath();
        ctx.fillStyle = 'rgba(217, 119, 87, 0.15)';
        ctx.fill();

        // Stroke curve
        ctx.beginPath();
        drawSpline(pts);
        ctx.strokeStyle = '#d97757'; ctx.lineWidth = 2.5; ctx.stroke();

        // Dots (on exact data points — spline passes through these)
        for (let i = 0; i < pts.length; i++) {
            ctx.beginPath();
            ctx.arc(pts[i].x, pts[i].y, 7, 0, Math.PI * 2);
            ctx.fillStyle = '#e8956f'; ctx.fill();
            ctx.strokeStyle = '#111'; ctx.lineWidth = 2; ctx.stroke();
        }
    };

    function findBand(x, y) {
        for (let i = 0; i < gains.length; i++) {
            const bx = bandX(i), by = gainToY(gains[i]);
            if (Math.hypot(x - bx, y - by) < 16) return i;
        }
        return -1;
    }

    function getPos(e) {
        const rect = canvas.getBoundingClientRect();
        const touch = e.touches ? e.touches[0] : e;
        return [touch.clientX - rect.left, touch.clientY - rect.top];
    }

    canvas.addEventListener('mousedown', function(e) {
        const [x, y] = getPos(e);
        dragging = findBand(x, y);
    });
    canvas.addEventListener('mousemove', function(e) {
        if (dragging < 0) return;
        const [, y] = getPos(e);
        gains[dragging] = yToGain(y);
        setPresetName('Custom');
        draw();
    });
    canvas.addEventListener('mouseup', function() {
        if (dragging >= 0) { applyEQ(); dragging = -1; }
    });
    canvas.addEventListener('mouseleave', function() {
        if (dragging >= 0) { applyEQ(); dragging = -1; }
    });

    // Touch support
    canvas.addEventListener('touchstart', function(e) {
        e.preventDefault();
        const [x, y] = getPos(e);
        dragging = findBand(x, y);
    });
    canvas.addEventListener('touchmove', function(e) {
        if (dragging < 0) return;
        e.preventDefault();
        const [, y] = getPos(e);
        gains[dragging] = yToGain(y);
        setPresetName('Custom');
        draw();
    });
    canvas.addEventListener('touchend', function() {
        if (dragging >= 0) { applyEQ(); dragging = -1; }
    });

    window.addEventListener('resize', resize);
    resize();
    } // end if (canvas)

    // ─── EQ apply ──────────────────────────────────────────────────
    async function applyEQ(preset) {
        setEqStatus('applying', preset ? 'Applying ' + capitalize(preset) + '…' : 'Applying…');
        const body = preset ? { preset } : { bands: gains };
        try {
            const r = await fetch(`${base}/equalizer`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(body) });
            const d = await r.json();
            if (!d.ok) { setEqStatus('', ''); showToast('error', d.error || 'Failed.'); }
            else {
                if (preset) setPresetName(preset);
                setEqStatus('applied', 'Applied ✓');
                if (preset) { refreshStatus(); }
            }
        } catch { setEqStatus('', ''); showToast('error', 'Network error.'); }
    }

    // Presets
    document.querySelectorAll('.preset-btn').forEach(function(btn) {
        btn.addEventListener('click', function() { applyEQ(btn.dataset.preset); });
    });
    const resetBtn = document.getElementById('eq-reset-btn');
    if (resetBtn) resetBtn.addEventListener('click', function() { applyEQ('flat'); });

    // ─── Filters ───────────────────────────────────────────────────
    document.querySelectorAll('.filter-toggle').forEach(function(btn) {
        btn.addEventListener('click', async function() {
            const action = btn.dataset.action;
            const body = { action: action };
            if (action === 'lowpass') {
                const val = parseFloat(document.getElementById('lowpass-val').value);
                if (btn.classList.contains('active')) body.smoothing = null;
                else body.smoothing = val || 20.0;
            }
            try {
                const r = await fetch(`${base}/filters`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(body) });
                const d = await r.json();
                if (d.ok) { showToast('success', 'Filter updated.'); refreshStatus(); }
                else showToast('error', d.error || 'Failed.');
            } catch { showToast('error', 'Network error.'); }
        });
    });

    // ─── Live status (driven by the shared player's poll) ──────────
    // The Apple-Music-style player (percy_player.js) owns the single status
    // poll; we piggy-back on its callback to keep the EQ/filter sections and the
    // canvas gains in sync rather than polling a second time.
    let playerInstance = null;

    function applyActivity(active) {
        const eqSec = document.getElementById('eq-section');
        const filterSec = document.getElementById('filters-section');
        if (eqSec) eqSec.style.display = active ? '' : 'none';
        if (filterSec) filterSec.style.display = active ? '' : 'none';
    }

    function updateFilters(filters) {
        if (!filters) return;
        document.querySelectorAll('.filter-toggle').forEach(function(btn) {
            const action = btn.dataset.action;
            if (action === 'nightcore') btn.classList.toggle('active', filters.nightcore);
            else if (action === '8d') btn.classList.toggle('active', filters['8d'] || false);
            else if (action === 'lowpass') btn.classList.toggle('active', filters.lowpass != null);
        });
        const lpVal = document.getElementById('lowpass-val');
        if (lpVal && filters.lowpass != null) lpVal.value = filters.lowpass;
    }

    function onPlayerStatus(d) {
        applyActivity(!!d.active);
        updateFilters(d.filters);
        if (d.equalizer && d.equalizer.length === 15) {
            gains = d.equalizer;
            if (document.getElementById('eq-canvas') && dragging < 0) draw();
        }
        const toggle = document.getElementById('panel-toggle');
        if (toggle && d.setup) toggle.checked = d.setup.use_panel;
    }

    // Re-poll after a local EQ/filter mutation so the UI reflects the bot.
    function refreshStatus() { if (playerInstance) playerInstance.refresh(); }

    // Hide EQ/filters until we know the player is active.
    applyActivity(initialActive);

    // Mount the live player into the Player Status section.
    if (window.PercyPlayer && document.getElementById('percy-player')) {
        playerInstance = window.PercyPlayer.mount({
            root: document.getElementById('percy-player'),
            statusUrl: `${base}/status`,
            controlUrl: `${base}/control`,
            lyricsUrl: `${base}/lyrics`,
            onStatus: onPlayerStatus,
        });
    }
})();
