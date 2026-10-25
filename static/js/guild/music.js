/* Percy Dashboard — Music panel (setup, DJ mode, panel toggle, live status; the equalizer and filters live in the Sound panel, music/sound.js).
   Expects GUILD_ID and
   window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const base = `/dashboard/guild/${guildId}/music`;

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
            if (!(await PercyConfirm.ask({ title: "Delete the music panel channel?", consequence: "The Discord channel is deleted. If the panel is on, it keeps working as a temporary panel.", undo: "Run setup again to create a new panel channel.", confirmLabel: "Delete channel", danger: true }))) return;
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

    // ─── Live status (driven by the shared player's poll) ──────────
    // The Apple-Music-style player (percy_player.js) owns the single status
    // poll; we piggy-back on its callback to keep the EQ/filter sections and the
    // canvas gains in sync rather than polling a second time.
    let playerInstance = null;

    function onPlayerStatus(d) {
        const toggle = document.getElementById('panel-toggle');
        if (toggle && d.setup) toggle.checked = d.setup.use_panel;
    }

    function refreshStatus() { if (playerInstance) playerInstance.refresh(); }

    // DJ mode — a setting rather than a live player control, so it goes through the
    // global unsaved-changes banner instead of saving on change.
    const djSelect = document.getElementById('dj-mode-select');
    if (djSelect && !djSelect.disabled) {
        PercyUnsaved.register({
            label: 'DJ Mode',
            element: djSelect,
            reload: true,
            read: () => ({ dj_mode: parseInt(djSelect.value, 10) }),
            write: (state) => { djSelect.value = String(state.dj_mode); },
            save: async (state) => {
                const resp = await fetch(`${base}/dj-mode`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(state),
                });
                return resp.json();
            },
        });
    }

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
