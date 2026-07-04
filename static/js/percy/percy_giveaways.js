/* Percy Dashboard — Giveaway management (create / end / cancel).
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const baseUrl = `/dashboard/guild/${guildId}/giveaways`;

    const modal = document.getElementById('giveaway-modal');
    const openBtn = document.getElementById('create-giveaway-btn');
    const cancelBtn = document.getElementById('giveaway-modal-cancel');
    const confirmBtn = document.getElementById('giveaway-modal-confirm');

    function openModal() { modal.hidden = false; }
    function closeModal() { modal.hidden = true; }

    if (openBtn) openBtn.addEventListener('click', openModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
    if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

    if (confirmBtn) confirmBtn.addEventListener('click', async () => {
        const prize = document.getElementById('gw_prize').value.trim();
        const channel_id = document.getElementById('gw_channel').value;
        const amount = parseInt(document.getElementById('gw_duration_amount').value, 10);
        const unit = parseInt(document.getElementById('gw_duration_unit').value, 10);
        const winners = parseInt(document.getElementById('gw_winners').value, 10) || 1;
        const description = document.getElementById('gw_description').value.trim();

        if (!prize) { showToast('error', 'A prize is required.'); return; }
        if (!channel_id) { showToast('error', 'Please select a channel.'); return; }
        if (!amount || amount < 1) { showToast('error', 'Duration must be at least 1.'); return; }

        const body = {
            prize,
            channel_id,
            duration_seconds: amount * unit,
            winners,
            description: description || null,
        };

        confirmBtn.disabled = true;
        try {
            const resp = await fetch(baseUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.ok) {
                showToast('success', 'Giveaway created.');
                setTimeout(() => location.reload(), 500);
            } else {
                showToast('error', data.error || 'Failed to create giveaway.');
            }
        } catch { showToast('error', 'Network error.'); }
        finally { confirmBtn.disabled = false; }
    });

    // End buttons (draw winners now)
    document.querySelectorAll('.giveaway-end-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            if (!confirm(`End "${btn.dataset.title}" now and draw the winners?`)) return;
            btn.disabled = true;
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(id)}/end`, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Giveaway ended.'); setTimeout(() => location.reload(), 500); }
                else { showToast('error', data.error || 'Failed to end giveaway.'); btn.disabled = false; }
            } catch { showToast('error', 'Network error.'); btn.disabled = false; }
        });
    });

    // Cancel/delete buttons (no draw)
    document.querySelectorAll('.giveaway-delete-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            if (!confirm(`Cancel "${btn.dataset.title}"? No winners will be drawn.`)) return;
            btn.disabled = true;
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(id)}`, {
                    method: 'DELETE',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Giveaway cancelled.'); setTimeout(() => location.reload(), 500); }
                else { showToast('error', data.error || 'Failed to cancel giveaway.'); btn.disabled = false; }
            } catch { showToast('error', 'Network error.'); btn.disabled = false; }
        });
    });
})();
