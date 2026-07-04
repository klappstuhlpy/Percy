/* Percy Dashboard — Temp Channels hub management (create / edit / delete).
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const baseUrl = `/dashboard/guild/${guildId}/temp-channels`;
    let editingChannelId = null;

    const addBtn = document.getElementById('add-hub-btn');
    const modal = document.getElementById('hub-modal');
    const modalTitle = document.getElementById('hub-modal-title');
    const cancelBtn = document.getElementById('hub-modal-cancel');
    const confirmBtn = document.getElementById('hub-modal-confirm');

    function openModal(editing) {
        editingChannelId = editing || null;
        modalTitle.textContent = editing ? 'Edit Hub Channel' : 'Add Hub Channel';
        confirmBtn.textContent = editing ? 'Save' : 'Create';
        document.getElementById('hub_channel').disabled = !!editing;
        modal.hidden = false;
    }

    function closeModal() {
        modal.hidden = true;
        editingChannelId = null;
        document.getElementById('hub_channel').disabled = false;
        document.getElementById('hub_format').value = '';
    }

    addBtn.addEventListener('click', () => openModal(null));
    cancelBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

    confirmBtn.addEventListener('click', async () => {
        const channel_id = editingChannelId || document.getElementById('hub_channel').value;
        const format = document.getElementById('hub_format').value.trim();

        if (!channel_id) { showToast('error', 'Please select a channel.'); return; }
        if (!format) { showToast('error', 'Format string is required.'); return; }

        const body = { channel_id, format };

        try {
            let resp;
            if (editingChannelId) {
                resp = await fetch(`${baseUrl}/${encodeURIComponent(editingChannelId)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(body),
                });
            } else {
                resp = await fetch(baseUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(body),
                });
            }
            const data = await resp.json();
            if (data.ok) {
                showToast('success', editingChannelId ? 'Hub updated.' : 'Hub created.');
                setTimeout(() => location.reload(), 400);
            } else {
                showToast('error', data.error || 'Failed.');
            }
        } catch { showToast('error', 'Network error.'); }
    });

    // Edit buttons
    document.querySelectorAll('.edit-hub-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.getElementById('hub_channel').value = btn.dataset.channelId;
            document.getElementById('hub_format').value = btn.dataset.format;
            openModal(btn.dataset.channelId);
        });
    });

    // Delete buttons
    document.querySelectorAll('.delete-hub-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const channelId = btn.dataset.channelId;
            if (!confirm('Remove this temp channel hub?')) return;
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(channelId)}`, {
                    method: 'DELETE',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Hub removed.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to delete.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });
})();
