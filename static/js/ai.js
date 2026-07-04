/* Percy Dashboard — AI tab: per-channel override editor.
   The server-wide AI feature toggles are a plain POST form (no JS needed); this drives
   the per-channel override editor and the existing-overrides table.
   Expects GUILD_ID, window.AI_OVERRIDES and window.showToast (percy_common.js). */

(function() {
    function toast(level, msg) { (window.showToast || function(){})(level, msg); }

    var grid = document.getElementById('ai-override-grid');
    if (!grid) return; // AI tab not on this page

    // Must match Percy's GuildConfig.AIFlags / the internal API flag names.
    var FEATURES = [
        { key: 'assistant', label: 'Assistant' },
        { key: 'router', label: 'Command Router' },
        { key: 'moderation', label: 'Moderation' },
        { key: 'sentinel', label: 'Sentinel Screening' },
        { key: 'music', label: 'Music' },
        { key: 'polls', label: 'Polls' },
        { key: 'giveaways', label: 'Giveaways' },
        { key: 'tags', label: 'Tags' },
        { key: 'reminders', label: 'Reminders' },
    ];

    var overrides = Array.isArray(window.AI_OVERRIDES) ? window.AI_OVERRIDES : [];
    function findOverride(channelId) {
        for (var i = 0; i < overrides.length; i++) {
            if (String(overrides[i].channel_id) === String(channelId)) return overrides[i];
        }
        return null;
    }

    async function postJSON(url, body) {
        var resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        });
        return resp.json();
    }

    // -- Build the per-feature Inherit/On/Off selects --
    FEATURES.forEach(function(feat) {
        var field = document.createElement('label');
        field.className = 'config-toggle';
        field.innerHTML =
            '<div><span class="config-label">' + feat.label + '</span></div>' +
            '<select class="config-select ai-ov-select" data-feature="' + feat.key + '">' +
            '<option value="inherit">Inherit</option>' +
            '<option value="on">On</option>' +
            '<option value="off">Off</option>' +
            '</select>';
        grid.appendChild(field);
    });

    var channelSel = document.getElementById('ai-override-channel');
    var saveBtn = document.getElementById('ai-override-save');
    var selects = grid.querySelectorAll('.ai-ov-select');

    function setSelectsFromOverride(ov) {
        selects.forEach(function(sel) {
            var key = sel.dataset.feature;
            var controlled = ov && ov.controlled && ov.controlled[key];
            if (!controlled) { sel.value = 'inherit'; return; }
            sel.value = (ov.enabled && ov.enabled[key]) ? 'on' : 'off';
        });
    }

    if (channelSel) {
        channelSel.addEventListener('change', function() {
            var cid = channelSel.value;
            saveBtn.disabled = !cid;
            setSelectsFromOverride(cid ? findOverride(cid) : null);
        });
    }

    // -- Save the override --
    if (saveBtn) {
        saveBtn.addEventListener('click', async function() {
            var cid = channelSel.value;
            if (!cid) { toast('error', 'Select a channel first.'); return; }
            var controlled = {};
            var enabled = {};
            var anyControlled = false;
            selects.forEach(function(sel) {
                var key = sel.dataset.feature;
                if (sel.value === 'inherit') { controlled[key] = false; enabled[key] = false; return; }
                controlled[key] = true;
                enabled[key] = (sel.value === 'on');
                anyControlled = true;
            });

            saveBtn.disabled = true;
            try {
                var data = await postJSON('/dashboard/guild/' + GUILD_ID + '/ai/override',
                    { channel_id: cid, controlled: controlled, enabled: enabled });
                if (data.ok) {
                    toast('success', anyControlled ? 'Override saved.' : 'Override cleared.');
                    setTimeout(function() { location.reload(); }, 400);
                } else {
                    toast('error', data.error || 'Failed to save override.');
                    saveBtn.disabled = false;
                }
            } catch (e) { toast('error', 'Network error'); saveBtn.disabled = false; }
        });
    }

    // -- Remove an existing override --
    document.querySelectorAll('.ai-override-remove').forEach(function(btn) {
        btn.addEventListener('click', async function() {
            var cid = btn.getAttribute('data-channel-id');
            btn.disabled = true;
            try {
                var data = await postJSON('/dashboard/guild/' + GUILD_ID + '/ai/override/' + cid + '/delete', null);
                if (data.ok) {
                    var row = document.querySelector('#ai-override-tbody tr[data-channel-id="' + cid + '"]');
                    if (row) row.remove();
                    var tbody = document.getElementById('ai-override-tbody');
                    if (tbody && tbody.children.length === 0) {
                        var wrap = document.getElementById('ai-override-table-wrap'); if (wrap) wrap.hidden = true;
                        var empty = document.getElementById('ai-override-empty'); if (empty) empty.hidden = false;
                    }
                    toast('success', 'Override removed.');
                } else { toast('error', data.error || 'Failed.'); btn.disabled = false; }
            } catch (e) { toast('error', 'Network error'); btn.disabled = false; }
        });
    });
})();
