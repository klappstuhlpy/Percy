/* Percy Dashboard — Outbound webhooks (create / edit / toggle / delete / test /
   delivery log). Expects GUILD_ID and window.showToast (common.js). */

(function() {
    const guildId = GUILD_ID;
    const base = `/dashboard/guild/${guildId}/webhooks`;

    const modal = document.getElementById('wh-modal');
    const title = document.getElementById('wh-modal-title');
    const urlInput = document.getElementById('wh_url');
    const labelInput = document.getElementById('wh_label');
    const confirmBtn = document.getElementById('wh-modal-confirm');
    let editingId = null;

    const jsonHeaders = { 'Content-Type': 'application/json', 'Accept': 'application/json' };

    function eventChecks() { return Array.from(document.querySelectorAll('.wh-event')); }
    function selectedEvents() { return eventChecks().filter(c => c.checked).map(c => c.value); }
    function setEvents(list) {
        const set = new Set(list || []);
        eventChecks().forEach(c => { c.checked = set.has(c.value); });
    }

    function openModal(mode, row) {
        if (mode === 'edit' && row) {
            editingId = row.dataset.id;
            title.textContent = 'Edit Webhook';
            urlInput.value = row.dataset.url || '';
            labelInput.value = row.dataset.label || '';
            setEvents((row.dataset.events || '').split(',').filter(Boolean));
        } else {
            editingId = null;
            title.textContent = 'Add Webhook';
            urlInput.value = '';
            labelInput.value = '';
            setEvents([]);
        }
        modal.hidden = false;
    }
    function closeModal() { modal.hidden = true; }

    const addBtn = document.getElementById('add-webhook-btn');
    if (addBtn) addBtn.addEventListener('click', () => openModal('create'));
    document.getElementById('wh-modal-cancel').addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

    confirmBtn.addEventListener('click', async () => {
        const url = urlInput.value.trim();
        const label = labelInput.value.trim();
        const events = selectedEvents();
        if (!url) { showToast('error', 'Payload URL is required.'); return; }
        if (events.length === 0) { showToast('error', 'Select at least one event.'); return; }

        const body = { url, events, label: label || null };
        try {
            if (editingId) {
                const resp = await fetch(`${base}/${editingId}`, { method: 'PATCH', headers: jsonHeaders, body: JSON.stringify(body) });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Webhook updated.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to update.'); }
            } else {
                const resp = await fetch(`${base}/create`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) });
                const data = await resp.json();
                if (data.ok) {
                    closeModal();
                    const secret = data.subscription && data.subscription.secret;
                    if (secret) { revealSecret(secret); }
                    else { showToast('success', 'Webhook created.'); setTimeout(() => location.reload(), 500); }
                } else { showToast('error', data.error || 'Failed to create.'); }
            }
        } catch { showToast('error', 'Network error.'); }
    });

    // -- Secret reveal (once, after create) ------------------------------------
    const secretModal = document.getElementById('wh-secret-modal');
    const secretValue = document.getElementById('wh-secret-value');
    function revealSecret(secret) {
        secretValue.value = secret;
        secretModal.hidden = false;
    }
    document.getElementById('wh-secret-copy').addEventListener('click', () => {
        secretValue.select();
        navigator.clipboard?.writeText(secretValue.value).then(
            () => showToast('success', 'Secret copied.'),
            () => showToast('error', 'Copy failed — select and copy manually.'),
        );
    });
    document.getElementById('wh-secret-close').addEventListener('click', () => {
        secretModal.hidden = true;
        location.reload();
    });

    // -- Row actions -----------------------------------------------------------
    function rowFor(id) { return document.querySelector(`tr[data-id="${id}"]`); }

    document.querySelectorAll('.wh-edit').forEach(btn => {
        btn.addEventListener('click', () => openModal('edit', rowFor(btn.dataset.id)));
    });

    document.querySelectorAll('.wh-toggle').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            const enabled = btn.dataset.enabled === 'true';
            try {
                const resp = await fetch(`${base}/${id}`, { method: 'PATCH', headers: jsonHeaders, body: JSON.stringify({ enabled: !enabled }) });
                const data = await resp.json();
                if (data.ok) { location.reload(); }
                else { showToast('error', data.error || 'Failed to toggle.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });

    document.querySelectorAll('.wh-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this webhook and its delivery log?')) return;
            try {
                const resp = await fetch(`${base}/${btn.dataset.id}`, { method: 'DELETE', headers: { 'Accept': 'application/json' } });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Deleted.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to delete.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });

    document.querySelectorAll('.wh-test').forEach(btn => {
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            try {
                const resp = await fetch(`${base}/${btn.dataset.id}/test`, { method: 'POST', headers: { 'Accept': 'application/json' } });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Test delivered successfully.'); }
                else {
                    const detail = data.result && (data.result.status_code || data.result.error);
                    showToast('error', 'Test failed' + (detail ? `: ${detail}` : '.'));
                }
            } catch { showToast('error', 'Network error.'); }
            finally { btn.disabled = false; }
        });
    });

    // -- Delivery log ----------------------------------------------------------
    const logModal = document.getElementById('wh-log-modal');
    const logBody = document.getElementById('wh-log-body');
    document.getElementById('wh-log-close').addEventListener('click', () => { logModal.hidden = true; });
    logModal.addEventListener('click', (e) => { if (e.target === logModal) logModal.hidden = true; });

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    document.querySelectorAll('.wh-log').forEach(btn => {
        btn.addEventListener('click', async () => {
            logBody.innerHTML = '<p class="text-muted">Loading…</p>';
            logModal.hidden = false;
            try {
                const resp = await fetch(`${base}/${btn.dataset.id}/deliveries`, { headers: { 'Accept': 'application/json' } });
                const data = await resp.json();
                if (!data.ok) { logBody.innerHTML = `<p class="text-muted">${escapeHtml(data.error || 'Failed to load.')}</p>`; return; }
                if (!data.deliveries.length) { logBody.innerHTML = '<p class="text-muted">No deliveries yet.</p>'; return; }
                const rows = data.deliveries.map(d => `
                    <tr>
                        <td><code>${escapeHtml(d.event)}</code></td>
                        <td>${d.success ? '<span class="badge badge-success">ok</span>' : '<span class="badge badge-danger">fail</span>'}</td>
                        <td>${d.status_code != null ? d.status_code : '—'}</td>
                        <td>${d.attempts}</td>
                        <td class="text-truncate" title="${escapeHtml(d.error || '')}">${escapeHtml(d.error || '')}</td>
                    </tr>`).join('');
                logBody.innerHTML = `<table class="data-table"><thead><tr><th>Event</th><th>Result</th><th>Code</th><th>Tries</th><th>Error</th></tr></thead><tbody>${rows}</tbody></table>`;
            } catch { logBody.innerHTML = '<p class="text-muted">Network error.</p>'; }
        });
    });
})();
