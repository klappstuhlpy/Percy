/* Percy Dashboard — Moderation audit log (live case polling + edit/close).
   editCase / closeCase are exposed on window for inline onclick handlers
   (including rows injected by prependCase). Expects GUILD_ID and
   window.showToast (percy_common.js). */

(function() {
    let lastPollTime = new Date().toISOString();

    function actionPillHtml(action) {
        return `<span class="action-pill ${action}">${action}</span>`;
    }

    function prependCase(c) {
        const tbody = document.getElementById('cases-tbody');
        if (!tbody) return;
        const tr = document.createElement('tr');
        tr.dataset.caseIndex = c.case_index;
        tr.style.animation = 'fadeIn 0.3s ease';
        tr.innerHTML = `
            <td>${c.case_index}</td>
            <td>${actionPillHtml(c.action)}</td>
            <td><a href="/dashboard/guild/${GUILD_ID}/members/${c.target_id}">${c.target_name}</a></td>
            <td>${c.moderator_name || 'System'}</td>
            <td class="text-muted case-reason-cell truncate-cell" title="${c.reason || ''}">${c.reason || '—'}</td>
            <td class="member-joined"><time class="js-ts">${c.created_at || '—'}</time></td>
            <td class="case-actions">
                <button type="button" class="button small" onclick="editCase(${c.case_index})">Edit</button>
                <button type="button" class="button small danger outline" onclick="closeCase(${c.case_index})">Close</button>
            </td>
        `;
        tbody.insertBefore(tr, tbody.firstChild);
        const counter = document.getElementById('total-cases');
        if (counter) counter.textContent = String(parseInt(counter.textContent) + 1);
    }

    function caseRow(idx) {
        return document.querySelector(`#cases-tbody tr[data-case-index="${idx}"]`);
    }

    window.editCase = async function(idx) {
        const row = caseRow(idx);
        const current = row?.querySelector('.case-reason-cell')?.title || '';
        const reason = prompt(`New reason for case #${idx}:`, current);
        if (reason === null || !reason.trim()) return;
        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/cases/${idx}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason: reason.trim() }),
            });
            const data = await resp.json();
            if (data.error) { showToast('error', data.error); return; }
            const cell = row?.querySelector('.case-reason-cell');
            if (cell) { cell.textContent = reason.trim(); cell.title = reason.trim(); }
            showToast('success', `Case #${idx} updated`);
        } catch {
            showToast('error', 'Failed to update case');
        }
    };

    window.closeCase = async function(idx) {
        if (!confirm(`Close (delete) case #${idx}? This removes the case and its modlog post permanently.`)) return;
        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/cases/${idx}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.error) { showToast('error', data.error); return; }
            caseRow(idx)?.remove();
            const counter = document.getElementById('total-cases');
            if (counter) counter.textContent = String(Math.max(0, parseInt(counter.textContent) - 1));
            showToast('success', `Case #${idx} closed`);
        } catch {
            showToast('error', 'Failed to close case');
        }
    };

    async function pollNewCases() {
        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/audit-log/recent?since=${encodeURIComponent(lastPollTime)}`);
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.cases && data.cases.length > 0) {
                for (const c of data.cases) {
                    prependCase(c);
                    if (c.created_at) lastPollTime = c.created_at;
                }
                showToast('info', `${data.cases.length} new case(s)`);
            }
        } catch { /* silent */ }
    }

    // Poll every 15 seconds for new cases
    setInterval(pollNewCases, 15000);

    // Convert ISO dates in filter inputs to local format
    document.querySelectorAll('input[type="date"]').forEach(input => {
        if (input.value && input.value.includes('T')) {
            input.value = input.value.split('T')[0];
        }
    });
})();
