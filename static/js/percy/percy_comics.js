/* Percy Dashboard — Comic feeds (subscribe / edit / delete / push).
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const baseUrl = `/dashboard/guild/${guildId}/comics`;
    let editingBrand = null;
    let original = null; // snapshot of the feed being edited, for change detection

    // Select an <option> by value, case-insensitively (feed.format may come back
    // as "SUMMARY" while the options are "Summary"); falls back to the first option.
    function setSelectValue(sel, val) {
        val = (val == null ? '' : String(val)).toLowerCase();
        for (let i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value.toLowerCase() === val) { sel.selectedIndex = i; return; }
        }
        sel.selectedIndex = 0;
    }

    const addBtn = document.getElementById('add-feed-btn');
    const modal = document.getElementById('feed-modal');
    const modalTitle = document.getElementById('feed-modal-title');
    const cancelBtn = document.getElementById('feed-modal-cancel');
    const confirmBtn = document.getElementById('feed-modal-confirm');

    function openModal(editing) {
        editingBrand = editing || null;
        modalTitle.textContent = editing ? `Edit Feed — ${editing}` : 'Subscribe to Comic Feed';
        confirmBtn.textContent = editing ? 'Save' : 'Create';
        document.getElementById('feed_brand').disabled = !!editing;
        modal.hidden = false;
    }

    function closeModal() {
        modal.hidden = true;
        editingBrand = null;
        original = null;
        document.getElementById('feed_brand').disabled = false;
        document.getElementById('feed_brand').value = 'Marvel';
        document.getElementById('feed_channel').value = '';
        document.getElementById('feed_format').value = 'Full';
        document.getElementById('feed_day').value = '1';
        document.getElementById('feed_ping').value = '';
        document.getElementById('feed_pin').checked = false;
    }

    addBtn.addEventListener('click', () => openModal(null));
    cancelBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

    confirmBtn.addEventListener('click', async () => {
        const brand = document.getElementById('feed_brand').value;
        const channel_id = document.getElementById('feed_channel').value.trim();
        const format = document.getElementById('feed_format').value;
        const day = parseInt(document.getElementById('feed_day').value);
        const ping = document.getElementById('feed_ping').value.trim() || null;
        const pin = document.getElementById('feed_pin').checked;

        if (!channel_id) { showToast('error', 'Channel is required.'); return; }

        try {
            let resp;
            if (editingBrand) {
                // Only send the fields that actually changed from the current config.
                const patch = {};
                if (channel_id !== original.channel_id) patch.channel_id = channel_id;
                if (format.toLowerCase() !== (original.format || '').toLowerCase()) patch.format = format;
                if (day !== parseInt(original.day || '0')) patch.day = day;
                if ((ping || '') !== (original.ping || '')) patch.ping = ping;
                if (pin !== (original.pin === 'true')) patch.pin = pin;

                if (Object.keys(patch).length === 0) { showToast('info', 'No changes to save.'); return; }

                resp = await fetch(`${baseUrl}/${encodeURIComponent(editingBrand)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(patch),
                });
            } else {
                resp = await fetch(baseUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ brand, channel_id, format, day, ping, pin }),
                });
            }
            const data = await resp.json();
            if (data.ok) {
                showToast('success', editingBrand ? 'Feed updated.' : 'Feed created.');
                setTimeout(() => location.reload(), 400);
            } else {
                showToast('error', data.error || 'Failed.');
            }
        } catch { showToast('error', 'Network error.'); }
    });

    // Edit buttons
    document.querySelectorAll('.edit-feed-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            // Keep the raw current values so we can diff on save.
            original = {
                channel_id: btn.dataset.channel,
                format: btn.dataset.format,
                day: btn.dataset.day,
                ping: btn.dataset.ping,
                pin: btn.dataset.pin,
            };
            document.getElementById('feed_brand').value = btn.dataset.brand;
            setSelectValue(document.getElementById('feed_channel'), btn.dataset.channel);
            setSelectValue(document.getElementById('feed_format'), btn.dataset.format);
            setSelectValue(document.getElementById('feed_day'), btn.dataset.day);
            setSelectValue(document.getElementById('feed_ping'), btn.dataset.ping);
            document.getElementById('feed_pin').checked = btn.dataset.pin === 'true';
            openModal(btn.dataset.brand);
        });
    });

    // Delete buttons
    document.querySelectorAll('.delete-feed-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const brand = btn.dataset.brand;
            if (!confirm(`Remove "${brand}" comic feed?`)) return;
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(brand)}`, {
                    method: 'DELETE',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Feed removed.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to delete.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });

    // Push Now buttons
    document.querySelectorAll('.push-feed-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const brand = btn.dataset.brand;
            btn.disabled = true;
            btn.textContent = 'Pushing...';
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(brand)}/push`, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', `${brand} feed pushed.`); }
                else { showToast('error', data.error || 'Failed to push.'); }
            } catch { showToast('error', 'Network error.'); }
            finally { btn.disabled = false; btn.textContent = 'Push Now'; }
        });
    });
})();
