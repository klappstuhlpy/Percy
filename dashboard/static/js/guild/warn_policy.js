/* Percy Dashboard — warn policy card (audit log page).
   Expiry is an edit (PercyUnsaved); adding/removing a rule is an immediate action. */
(function () {
    const input = document.getElementById('warn-expiry');
    if (!input) return;
    const base = '/dashboard/guild/' + GUILD_ID + '/warn-policy';
    const json = { 'Content-Type': 'application/json', 'Accept': 'application/json' };

    PercyUnsaved.register({
        label: 'Warn expiry',
        element: input,
        read: () => ({ v: input.value.trim() }),
        write: (s) => { input.value = s.v; },
        save: async (s) => {
            const n = s.v === '' ? null : parseInt(s.v, 10);
            if (n !== null && !(n >= 1)) return { ok: false, error: 'Enter a number of days, or leave empty.' };
            const resp = await fetch(base, { method: 'PATCH', headers: json, body: JSON.stringify({ expiry_days: n }) });
            return resp.json();
        },
    });

    document.getElementById('warn-rule-add').addEventListener('click', async () => {
        const count = parseInt(document.getElementById('warn-rule-count').value, 10);
        const action = document.getElementById('warn-rule-action').value;
        const mins = parseInt(document.getElementById('warn-rule-minutes').value, 10);
        if (!(count >= 1)) { showToast('error', 'Enter the warn points.'); return; }
        if (!action) { showToast('error', 'Pick an action.'); return; }
        const body = { action, duration_seconds: mins >= 1 ? mins * 60 : null };
        try {
            const resp = await fetch(base + '/thresholds/' + count, { method: 'PUT', headers: json, body: JSON.stringify(body) });
            const data = await resp.json();
            if (!data.ok) { showToast('error', data.error || 'Failed to save rule.'); return; }
            location.reload();
        } catch { showToast('error', 'Network error.'); }
    });

    document.querySelectorAll('.warn-rule-remove').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const count = btn.dataset.count;
            if (!(await PercyConfirm.ask({ title: `Remove the rule at ${count} points?`, consequence: 'Members reaching that total no longer get an automatic action.', undo: 'You can add the rule again.', confirmLabel: 'Remove rule', danger: true }))) return;
            try {
                const resp = await fetch(base + '/thresholds/' + count, { method: 'DELETE', headers: json });
                const data = await resp.json();
                if (!data.ok) { showToast('error', data.error || 'Failed to remove rule.'); return; }
                location.reload();
            } catch { showToast('error', 'Network error.'); }
        });
    });

    document.getElementById('warn-reason-add').addEventListener('click', async () => {
        const name = document.getElementById('warn-reason-name').value.trim().toLowerCase();
        const points = parseInt(document.getElementById('warn-reason-points').value, 10);
        const description = document.getElementById('warn-reason-text').value.trim();
        if (!name || /\s/.test(name)) { showToast('error', 'Enter a one-word name.'); return; }
        if (!(points >= 1 && points <= 100)) { showToast('error', 'Points must be 1 to 100.'); return; }
        try {
            const resp = await fetch(base + '/reasons/' + encodeURIComponent(name), { method: 'PUT', headers: json, body: JSON.stringify({ points, description }) });
            const data = await resp.json();
            if (!data.ok) { showToast('error', data.error || 'Failed to save preset.'); return; }
            location.reload();
        } catch { showToast('error', 'Network error.'); }
    });

    document.querySelectorAll('.warn-reason-remove').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const name = btn.dataset.name;
            if (!(await PercyConfirm.ask({ title: `Remove the "${name}" preset?`, consequence: 'Warns can no longer use this name as a shortcut. Existing warns keep their points.', undo: 'You can add the preset again.', confirmLabel: 'Remove preset', danger: true }))) return;
            try {
                const resp = await fetch(base + '/reasons/' + encodeURIComponent(name), { method: 'DELETE', headers: json });
                const data = await resp.json();
                if (!data.ok) { showToast('error', data.error || 'Failed to remove preset.'); return; }
                location.reload();
            } catch { showToast('error', 'Network error.'); }
        });
    });
})();
