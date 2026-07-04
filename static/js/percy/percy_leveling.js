/* Percy Dashboard — Leveling (config, rewards, multipliers, blacklists,
   special messages, per-user edit) plus the XP-over-time uPlot chart.
   editLevel is global for the leaderboard's inline onclick handler.
   Expects GUILD_ID, LEVEL_UP_CHANNEL and window.showToast (percy_common.js).
   uPlot must be loaded before this file for the chart to render. */

let editingUserId = null;

async function postJSON(path, body) {
    const resp = await fetch(`/dashboard/guild/${GUILD_ID}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(body),
    });
    return resp.json();
}

// ─── Level-up channel mode selector ──────────────────────────────────────────
const lvlChannelMode = document.getElementById('lvl-channel-mode');
const lvlChannelPickerField = document.getElementById('lvl-channel-picker-field');
const lvlChannelPicker = document.getElementById('lvl-channel-picker');

function syncChannelPickerVisibility() {
    lvlChannelPickerField.hidden = lvlChannelMode.value !== 'channel';
}
(function initChannelMode() {
    if (LEVEL_UP_CHANNEL === 0 || LEVEL_UP_CHANNEL === 1 || LEVEL_UP_CHANNEL === 2) {
        lvlChannelMode.value = String(LEVEL_UP_CHANNEL);
    } else {
        lvlChannelMode.value = 'channel';
        if (lvlChannelPicker) lvlChannelPicker.value = String(LEVEL_UP_CHANNEL);
    }
    syncChannelPickerVisibility();
})();
lvlChannelMode.addEventListener('change', syncChannelPickerVisibility);

function computeLevelUpChannel() {
    if (lvlChannelMode.value === 'channel') {
        const cid = lvlChannelPicker ? lvlChannelPicker.value : '';
        return cid ? parseInt(cid) : 1;
    }
    return parseInt(lvlChannelMode.value);
}

// ─── Leveling Config Form ────────────────────────────────────────────────────
const configForm = document.getElementById('leveling-config-form');
if (configForm) {
    // -- Unsaved changes banner --
    const initialState = {};
    for (const el of configForm.elements) {
        if (!el.name || el.type === 'hidden') continue;
        initialState[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    }
    let lvlBanner = null;
    function createLvlBanner() {
        if (lvlBanner) return lvlBanner;
        lvlBanner = document.createElement('div');
        lvlBanner.className = 'unsaved-banner';
        lvlBanner.innerHTML = `
            <span class="unsaved-banner-text">You have unsaved changes</span>
            <div class="unsaved-banner-actions">
                <button type="button" class="button small" id="lvl-banner-cancel">Cancel</button>
                <button type="button" class="button primary small" id="lvl-banner-save">Save Changes</button>
            </div>
        `;
        document.body.appendChild(lvlBanner);
        lvlBanner.querySelector('#lvl-banner-cancel').addEventListener('click', () => {
            for (const el of configForm.elements) {
                if (!el.name || el.type === 'hidden') continue;
                if (el.type === 'checkbox') el.checked = !!initialState[el.name];
                else el.value = initialState[el.name] !== undefined ? initialState[el.name] : '';
            }
            lvlChannelMode.value = initialState['__channelMode'] || String(LEVEL_UP_CHANNEL === 0 || LEVEL_UP_CHANNEL === 1 || LEVEL_UP_CHANNEL === 2 ? LEVEL_UP_CHANNEL : 'channel');
            syncChannelPickerVisibility();
            hideLvlBanner();
            showToast('success', 'Changes discarded.');
        });
        lvlBanner.querySelector('#lvl-banner-save').addEventListener('click', () => configForm.requestSubmit());
        return lvlBanner;
    }
    function showLvlBanner() { const b = createLvlBanner(); requestAnimationFrame(() => b.classList.add('visible')); }
    function hideLvlBanner() { if (lvlBanner) lvlBanner.classList.remove('visible'); }
    function checkLvlDirty() {
        let dirty = false;
        for (const el of configForm.elements) {
            if (!el.name || el.type === 'hidden') continue;
            if (el.type === 'checkbox') { if (el.checked !== !!initialState[el.name]) { dirty = true; break; } }
            else { if (el.value !== (initialState[el.name] || '')) { dirty = true; break; } }
        }
        if (dirty) showLvlBanner(); else hideLvlBanner();
    }
    configForm.addEventListener('input', checkLvlDirty);
    configForm.addEventListener('change', checkLvlDirty);

    configForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {
            enabled: configForm.querySelector('[name="enabled"]').checked,
            voice_enabled: configForm.querySelector('[name="voice_enabled"]').checked,
            role_stack: configForm.querySelector('[name="role_stack"]').checked,
            delete_after_leave: configForm.querySelector('[name="delete_after_leave"]').checked,
            factor: parseFloat(configForm.querySelector('[name="factor"]').value) || 1.0,
            base: parseInt(configForm.querySelector('[name="base"]').value) || 100,
            min_gain: parseInt(configForm.querySelector('[name="min_gain"]').value) || 0,
            max_gain: parseInt(configForm.querySelector('[name="max_gain"]').value) || 0,
            cooldown_per: parseInt(configForm.querySelector('[name="cooldown_per"]').value) || 0,
            level_up_channel: computeLevelUpChannel(),
            level_up_message: configForm.querySelector('[name="level_up_message"]').value.trim() || null,
        };
        try {
            const data = await postJSON('/leveling/config', body);
            if (data.ok) { showToast('success', 'Configuration saved.'); hideLvlBanner(); setTimeout(() => location.reload(), 400); }
            else { showToast('error', data.error || 'Failed to save.'); }
        } catch { showToast('error', 'Network error.'); }
    });
    document.getElementById('lvl-cancel-btn').addEventListener('click', () => {
        for (const el of configForm.elements) {
            if (!el.name || el.type === 'hidden') continue;
            if (el.type === 'checkbox') el.checked = !!initialState[el.name];
            else el.value = initialState[el.name] !== undefined ? initialState[el.name] : '';
        }
        hideLvlBanner();
        showToast('success', 'Changes discarded.');
    });
}

// ─── Level Rewards ───────────────────────────────────────────────────────────
document.getElementById('add-role-btn').addEventListener('click', async () => {
    const roleId = document.getElementById('add-role-select').value;
    const level = parseInt(document.getElementById('add-role-level').value);
    if (!roleId || !Number.isFinite(level)) { showToast('error', 'Pick a role and level.'); return; }
    try {
        const data = await postJSON('/leveling/roles', { role_id: roleId, level });
        if (data.ok) { showToast('success', 'Reward added.'); setTimeout(() => location.reload(), 400); }
        else { showToast('error', data.error || 'Failed.'); }
    } catch { showToast('error', 'Network error.'); }
});
document.querySelectorAll('.lvl-role-remove').forEach(btn => {
    btn.addEventListener('click', async () => {
        const level = parseInt(btn.dataset.level);
        try {
            const data = await postJSON('/leveling/roles', { level, role_id: null });
            if (data.ok) { showToast('success', 'Reward removed.'); setTimeout(() => location.reload(), 400); }
            else { showToast('error', data.error || 'Failed.'); }
        } catch { showToast('error', 'Network error.'); }
    });
});
const presetBtn = document.getElementById('preset-roles-btn');
if (presetBtn) {
    presetBtn.addEventListener('click', async () => {
        if (!confirm('Are you sure? This creates 12 milestone roles (levels 5–100) with preset names and colors in your server and assigns them as level rewards.')) return;
        presetBtn.disabled = true;
        try {
            const data = await postJSON('/leveling/roles/preset', {});
            if (data.ok) { showToast('success', 'Preset roles created.'); setTimeout(() => location.reload(), 600); }
            else { showToast('error', data.error || 'Failed.'); presetBtn.disabled = false; }
        } catch { showToast('error', 'Network error.'); presetBtn.disabled = false; }
    });
}

// ─── XP Multipliers ──────────────────────────────────────────────────────────
document.querySelectorAll('.add-mult-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        const type = btn.dataset.type;
        const id = document.getElementById(btn.dataset.select).value;
        const multiplier = parseFloat(document.getElementById(btn.dataset.value).value);
        if (!id || !Number.isFinite(multiplier) || multiplier <= 0) { showToast('error', 'Pick a target and a positive multiplier.'); return; }
        try {
            const data = await postJSON('/leveling/multipliers', { type, id, multiplier });
            if (data.ok) { showToast('success', 'Multiplier saved.'); setTimeout(() => location.reload(), 400); }
            else { showToast('error', data.error || 'Failed.'); }
        } catch { showToast('error', 'Network error.'); }
    });
});
document.querySelectorAll('.mult-remove').forEach(btn => {
    btn.addEventListener('click', async () => {
        try {
            const data = await postJSON('/leveling/multipliers', { type: btn.dataset.type, id: btn.dataset.id, multiplier: 0 });
            if (data.ok) { showToast('success', 'Multiplier removed.'); setTimeout(() => location.reload(), 400); }
            else { showToast('error', data.error || 'Failed.'); }
        } catch { showToast('error', 'Network error.'); }
    });
});

// ─── Blacklists ──────────────────────────────────────────────────────────────
document.querySelectorAll('.bl-add-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        const type = btn.dataset.type;
        const id = btn.dataset.input
            ? document.getElementById(btn.dataset.input).value.trim()
            : document.getElementById(btn.dataset.select).value;
        if (!id) { showToast('error', 'Pick or enter a target.'); return; }
        try {
            const data = await postJSON('/leveling/blacklist', { type, id, action: 'add' });
            if (data.ok) { showToast('success', 'Added to blacklist.'); setTimeout(() => location.reload(), 400); }
            else { showToast('error', data.error || 'Failed.'); }
        } catch { showToast('error', 'Network error.'); }
    });
});
document.querySelectorAll('.bl-remove').forEach(btn => {
    btn.addEventListener('click', async () => {
        try {
            const data = await postJSON('/leveling/blacklist', { type: btn.dataset.type, id: btn.dataset.id, action: 'remove' });
            if (data.ok) { showToast('success', 'Removed from blacklist.'); setTimeout(() => location.reload(), 400); }
            else { showToast('error', data.error || 'Failed.'); }
        } catch { showToast('error', 'Network error.'); }
    });
});

// ─── Special Level-Up Messages ───────────────────────────────────────────────
function collectSpecialMessages() {
    const map = {};
    document.querySelectorAll('#special-msg-table tbody tr[data-level]').forEach(tr => {
        map[tr.dataset.level] = tr.dataset.message;
    });
    return map;
}
async function saveSpecialMessages(map) {
    try {
        const data = await postJSON('/leveling/config', { special_level_up_messages: map });
        if (data.ok) { showToast('success', 'Messages updated.'); setTimeout(() => location.reload(), 400); }
        else { showToast('error', data.error || 'Failed.'); }
    } catch { showToast('error', 'Network error.'); }
}
document.getElementById('add-special-btn').addEventListener('click', () => {
    const level = parseInt(document.getElementById('add-special-level').value);
    const message = document.getElementById('add-special-message').value.trim();
    if (!Number.isFinite(level) || !message) { showToast('error', 'Enter a level and message.'); return; }
    const map = collectSpecialMessages();
    map[String(level)] = message;
    saveSpecialMessages(map);
});
document.querySelectorAll('.special-remove').forEach(btn => {
    btn.addEventListener('click', () => {
        const map = collectSpecialMessages();
        delete map[btn.dataset.level];
        saveSpecialMessages(map);
    });
});

// ─── Edit Level/XP Modal ─────────────────────────────────────────────────────
function editLevel(userId, username, level, xp) {
    editingUserId = userId;
    document.getElementById('level-modal-desc').textContent = 'Editing ' + username;
    document.getElementById('edit-level').value = level;
    document.getElementById('edit-xp').value = xp;
    document.getElementById('level-modal').hidden = false;
}

document.getElementById('level-save-btn').addEventListener('click', async () => {
    if (!editingUserId) return;
    const level = parseInt(document.getElementById('edit-level').value);
    const xp = parseInt(document.getElementById('edit-xp').value);

    try {
        const resp = await fetch(`/dashboard/guild/${GUILD_ID}/leveling/users/${editingUserId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ level, xp }),
        });
        const data = await resp.json();
        if (data.ok) {
            document.getElementById('level-modal').hidden = true;
            showToast('success', 'Level updated.');
            setTimeout(() => location.reload(), 400);
        } else {
            showToast('error', data.error || 'Failed to update');
        }
    } catch {
        showToast('error', 'Network error');
    }
});

// ─── XP-over-time chart ─────────────────────────────────────────────
(function renderXpChart() {
    const el = document.getElementById('xp-chart');
    if (!el) return;

    let points;
    try {
        points = JSON.parse(el.dataset.points || '[]');
    } catch {
        points = [];
    }

    const message = (msg) => { el.innerHTML = `<div class="chart-message">${msg}</div>`; };

    if (!window.uPlot) { message('Chart library failed to load (CDN blocked?).'); return; }
    if (points.length === 0) { message('No snapshots yet — the first lands within a day of enabling leveling.'); return; }
    if (points.length < 2) { message('Only one day recorded so far — the trend appears after the next daily snapshot.'); return; }

    // Daily XP gained = difference between consecutive cumulative-total snapshots.
    const xs = points.map(p => Math.floor(Date.parse(p.day + 'T00:00:00Z') / 1000));
    const gains = points.map((p, i) => i === 0 ? null : Math.max(0, p.total_xp - points[i - 1].total_xp));

    const accent = getComputedStyle(document.documentElement).getPropertyValue('--branding').trim() || '#d97757';
    const size = () => ({ width: Math.max(220, el.clientWidth - 4), height: 240 });

    el.innerHTML = '';
    const chart = new uPlot({
        ...size(),
        cursor: { drag: { setScale: false } },
        legend: { live: true },
        scales: { x: { time: true }, y: { auto: true } },
        series: [
            {},
            {
                label: 'XP gained',
                stroke: accent,
                width: 2,
                fill: accent + '22',
                points: { show: true, size: 4 },
            },
        ],
        axes: [
            { stroke: '#71717a' },
            { stroke: '#71717a', grid: { stroke: 'rgba(127,127,127,0.15)' } },
        ],
    }, [xs, gains], el);

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => chart.setSize(size()), 150);
    });
})();
