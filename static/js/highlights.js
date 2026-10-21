/* Percy Dashboard — Highlights (delete a member's highlights).
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const baseUrl = `/dashboard/guild/${guildId}/highlights`;

    document.querySelectorAll('.delete-highlight-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const userId = btn.dataset.userId;
            const username = btn.dataset.username;
            if (!(await PercyConfirm.ask({ title: `Delete all highlights for ${username}?`, consequence: "They stop getting notified for every word they set up here.", undo: null, confirmLabel: "Delete highlights", danger: true }))) return;
            try {
                const resp = await fetch(`${baseUrl}/${encodeURIComponent(userId)}`, {
                    method: 'DELETE',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Highlights removed.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to delete.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });
})();
