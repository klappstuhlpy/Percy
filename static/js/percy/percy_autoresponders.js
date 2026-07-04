/* Percy Dashboard — Autoresponders (create / toggle / delete).
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const baseUrl = `/dashboard/guild/${guildId}/autoresponders`;

    // Add modal
    const addBtn = document.getElementById('add-responder-btn');
    const addModal = document.getElementById('add-modal');
    const addCancel = document.getElementById('add-modal-cancel');
    const addConfirm = document.getElementById('add-modal-confirm');

    addBtn.addEventListener('click', () => { addModal.hidden = false; });
    addCancel.addEventListener('click', () => { addModal.hidden = true; });
    addModal.addEventListener('click', (e) => { if (e.target === addModal) addModal.hidden = true; });

    addConfirm.addEventListener('click', async () => {
        const trigger = document.getElementById('ar_trigger').value.trim();
        const response = document.getElementById('ar_response').value.trim();
        const match_type = document.getElementById('ar_match_type').value;
        const ignore_case = document.getElementById('ar_ignore_case').checked;

        if (!trigger || !response) { showToast('error', 'Trigger and response are required.'); return; }

        try {
            const resp = await fetch(baseUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ trigger, response, match_type, ignore_case }),
            });
            const data = await resp.json();
            if (data.ok) { showToast('success', 'Autoresponder created.'); setTimeout(() => location.reload(), 400); }
            else { showToast('error', data.error || 'Failed to create.'); }
        } catch { showToast('error', 'Network error.'); }
    });

    // Toggle buttons
    document.querySelectorAll('.toggle-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const trigger = btn.dataset.trigger;
            const enabled = btn.dataset.enabled === 'true';
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(trigger)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ enabled: !enabled }),
                });
                const data = await resp.json();
                if (data.ok) { location.reload(); }
                else { showToast('error', data.error || 'Failed to toggle.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });

    // Delete buttons
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const trigger = btn.dataset.trigger;
            if (!confirm(`Delete autoresponder "${trigger}"?`)) return;
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(trigger)}`, {
                    method: 'DELETE',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Deleted.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to delete.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });
})();
