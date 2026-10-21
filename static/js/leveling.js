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
// Bound on the form so it also fires for the change event the unsaved tracker dispatches
// when it restores a discarded snapshot.
lvlChannelMode.closest('form').addEventListener('change', syncChannelPickerVisibility);

function computeLevelUpChannel() {
    if (lvlChannelMode.value === 'channel') {
        const cid = lvlChannelPicker ? lvlChannelPicker.value : '';
        return cid ? parseInt(cid) : 1;
    }
    return parseInt(lvlChannelMode.value);
}

// ─── Leveling Config Form ────────────────────────────────────────────────────
// No Save button: the global tracker (unsaved.js) raises the banner on any edit and
// POSTs the form's fields as the JSON body the leveling endpoint expects.
const configForm = document.getElementById('leveling-config-form');
if (configForm) {
    PercyUnsaved.watchForm(configForm, {
        label: 'Leveling Configuration',
        reload: true,
        save: (form) => postJSON('/leveling/config', {
            enabled: form.querySelector('[name="enabled"]').checked,
            voice_enabled: form.querySelector('[name="voice_enabled"]').checked,
            role_stack: form.querySelector('[name="role_stack"]').checked,
            delete_after_leave: form.querySelector('[name="delete_after_leave"]').checked,
            factor: parseFloat(form.querySelector('[name="factor"]').value) || 1.0,
            base: parseInt(form.querySelector('[name="base"]').value) || 100,
            min_gain: parseInt(form.querySelector('[name="min_gain"]').value) || 0,
            max_gain: parseInt(form.querySelector('[name="max_gain"]').value) || 0,
            cooldown_per: parseInt(form.querySelector('[name="cooldown_per"]').value) || 0,
            level_up_channel: computeLevelUpChannel(),
            level_up_message: form.querySelector('[name="level_up_message"]').value.trim() || null,
        }),
    });
}

// ─── Level-up message: placeholders, live preview, test send ─────────────────
// The preview mirrors Percy's substitution (app/services/level_message.py): only known
// {placeholders} are replaced, everything else stays exactly as typed.
(function () {
    const input = document.getElementById('level_up_message');
    const preview = document.getElementById('level-up-preview');
    if (!input || !preview) return;
    const sample = {
        'user': 'percyfan', 'user.mention': '@percyfan', 'user.display_name': 'Percy Fan',
        'level': '5', 'server': preview.dataset.guildName || 'this server',
    };
    const render = () => {
        const text = input.value || input.placeholder;
        preview.textContent = text.replace(/\{\{|\}\}|\{([\w.]+)\}/g, (m, name) =>
            m === '{{' ? '{' : m === '}}' ? '}' : (name in sample ? sample[name] : m));
    };
    input.addEventListener('input', render);
    render();

    document.querySelectorAll('.template-var').forEach((btn) => btn.addEventListener('click', () => {
        const at = input.selectionStart ?? input.value.length;
        input.setRangeText(btn.dataset.insert, at, input.selectionEnd ?? at, 'end');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
    }));

    const testBtn = document.getElementById('level-up-test-btn');
    if (testBtn) testBtn.addEventListener('click', async () => {
        testBtn.disabled = true;
        try {
            const data = await postJSON('/leveling/test-message', {});
            showToast(data.ok ? 'success' : 'error', data.ok ? data.message : (data.error || 'Could not send the test.'));
        } catch { showToast('error', 'Network error.'); }
        finally { testBtn.disabled = false; }
    });
})();

// ─── Rank card style ─────────────────────────────────────────────────────────
const cardStyleForm = document.getElementById('card-style-form');
if (cardStyleForm) {
    PercyUnsaved.watchForm(cardStyleForm, {
        label: 'Rank Card Style',
        save: (form) => postJSON('/leveling/config', {
            card_style: Object.fromEntries([...form.querySelectorAll('select')].map((el) => [el.name, el.value || null])),
            card_locked: form.querySelector('[name="card_locked"]').checked,
        }),
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
        if (!(await PercyConfirm.ask({ title: "Create 12 milestone roles?", consequence: "Percy creates roles for levels 5–100 with preset names and colours and makes them level rewards.", undo: "Delete the roles in Discord and remove the rewards here to undo.", confirmLabel: "Create roles" }))) return;
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

// ─── Quick setup (an action, not a tracked edit: it saves on its own) ─────────
(function quickSetupLeveling() {
    const qs = document.getElementById('quick-setup-leveling');
    if (!qs) return;
    const select = document.getElementById('qsl-channel');
    const checked = () => qs.querySelector('[name="qsl_where"]:checked').value;
    qs.querySelectorAll('[name="qsl_where"]').forEach(r => r.addEventListener('change', () => {
        select.disabled = checked() !== 'channel';
    }));
    document.getElementById('qsl-btn').addEventListener('click', async () => {
        const where = checked();
        if (where === 'channel' && !select.value) { showToast('error', 'Pick a channel for level-ups.'); return; }
        const roles = document.getElementById('qsl-roles').checked;
        const announce = {
            '0': 'Level-ups will not be announced',
            '1': 'Level-ups will be announced where the member talked',
            '2': 'Level-ups will be sent to the member by DM',
            'channel': `Level-ups will be announced in ${select.selectedOptions[0].textContent.trim()}`,
        }[where];
        if (!(await PercyConfirm.ask({
            title: 'Turn on leveling?',
            consequence: `${announce}. ${roles ? 'Percy also creates 12 milestone roles for levels 5–100 and makes them level rewards.' : 'No reward roles are created.'}`,
            undo: 'Turn leveling off or change any of this below at any time.',
            confirmLabel: 'Turn on leveling',
        }))) return;
        const res = await postJSON('/setup/leveling', {
            level_up_channel: where === 'channel' ? select.value : where,
            preset_roles: roles,
        }).catch(() => ({ ok: false, error: 'Network error.' }));
        showToast(res.ok ? 'success' : 'error', res.ok ? res.message : (res.error || 'Setup failed.'));
        if (res.ok) setTimeout(() => location.reload(), 900);
    });
})();
