/* Percy Dashboard — Developers tab: create (token shown once), list, revoke.
   Expects GUILD_ID, window.showToast (common.js) and PercyConfirm (confirm.js). */

(function () {
    const base = `/dashboard/guild/${GUILD_ID}/developers/tokens`;
    const jsonHeaders = { 'Content-Type': 'application/json', 'Accept': 'application/json' };

    document.querySelectorAll('.dev-ts').forEach(el => {
        const d = new Date(el.dataset.ts);
        if (!isNaN(d)) el.textContent = d.toLocaleString();
    });

    const modal = document.getElementById('dev-modal');
    const nameInput = document.getElementById('dev_name');
    const scopeBoxes = () => Array.from(document.querySelectorAll('.dev-scope'));

    document.getElementById('add-token-btn').addEventListener('click', () => {
        nameInput.value = '';
        scopeBoxes().forEach(c => { c.checked = false; });
        modal.hidden = false;
        nameInput.focus();
    });
    document.getElementById('dev-modal-cancel').addEventListener('click', () => { modal.hidden = true; });
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });

    const secretModal = document.getElementById('dev-secret-modal');
    const secretValue = document.getElementById('dev-secret-value');

    document.getElementById('dev-modal-confirm').addEventListener('click', async () => {
        const name = nameInput.value.trim();
        const scopes = scopeBoxes().filter(c => c.checked).map(c => c.value);
        if (!name) { showToast('error', 'Give the token a name.'); return; }
        if (scopes.length === 0) { showToast('error', 'Pick at least one scope.'); return; }
        try {
            const resp = await fetch(base, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ name, scopes }) });
            const data = await resp.json();
            if (data.ok) {
                modal.hidden = true;
                secretValue.value = data.token;
                secretModal.hidden = false;
                secretValue.select();
            } else { showToast('error', data.error || 'Failed to create the token.'); }
        } catch { showToast('error', 'Network error.'); }
    });

    document.getElementById('dev-secret-copy').addEventListener('click', () => {
        secretValue.select();
        navigator.clipboard?.writeText(secretValue.value).then(
            () => showToast('success', 'Token copied.'),
            () => showToast('error', 'Copy failed. Select and copy manually.'),
        );
    });
    document.getElementById('dev-secret-close').addEventListener('click', () => {
        secretValue.value = '';
        secretModal.hidden = true;
        location.reload();
    });

    document.querySelectorAll('.dev-revoke').forEach(btn => {
        btn.addEventListener('click', async () => {
            const row = btn.closest('tr');
            const ok = await PercyConfirm.ask({
                title: `Revoke "${row.dataset.name}"?`,
                consequence: 'Anything using this token stops working immediately.',
                undo: 'Create a new token to restore access; this one cannot be restored.',
                confirmLabel: 'Revoke token',
                danger: true,
            });
            if (!ok) return;
            try {
                const resp = await fetch(`${base}/${btn.dataset.id}`, { method: 'DELETE', headers: { 'Accept': 'application/json' } });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Token revoked.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to revoke.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });
})();
