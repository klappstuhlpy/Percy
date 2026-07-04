/* Percy Dashboard — User lookup page (avatar history, open-case form,
   case timeline edit/delete, command activity heatmap). Each feature
   no-ops when its target element is absent (e.g. the avatar grid only
   exists when the member has stored avatars). Expects GUILD_ID, MEMBER_ID
   and window.showToast (percy_common.js). */

// ─── Avatar history ──────────────────────────────────────────────────
(function() {
    const grid = document.getElementById('avatar-grid');
    if (!grid) return;
    fetch(`/dashboard/guild/${GUILD_ID}/members/${MEMBER_ID}/avatars`)
        .then(r => r.json())
        .then(data => {
            if (!data.avatars || !data.avatars.length) {
                grid.innerHTML = '<div class="empty-state"><p>No avatars stored.</p></div>';
                return;
            }
            grid.innerHTML = data.avatars.map(a => {
                const date = a.changed_at ? new Date(a.changed_at).toLocaleDateString() : '';
                return `<div class="avatar-history-item">
                    <img src="data:image/png;base64,${a.image}" alt="Avatar" loading="lazy">
                    <span class="avatar-date">${date}</span>
                </div>`;
            }).join('');
        })
        .catch(() => {
            grid.innerHTML = '<div class="empty-state"><p>Failed to load avatars.</p></div>';
        });
})();

// ─── Open case form ──────────────────────────────────────────────────
(function() {
    const form = document.getElementById('open-case-form');
    if (!form) return;
    const status = document.getElementById('open-case-status');

    function setStatus(kind, message) {
        status.textContent = message;
        status.className = 'open-case-status ' + kind;
        status.hidden = false;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const action = document.getElementById('case-action').value;
        const reason = document.getElementById('case-reason').value.trim();
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/cases`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, target_id: MEMBER_ID, reason: reason || null }),
            });
            const data = await resp.json();
            if (data.error) {
                setStatus('error', data.error);
                return;
            }
            setStatus('success', `Case #${data.case.case_index} opened — reloading…`);
            setTimeout(() => location.reload(), 600);
        } catch {
            setStatus('error', 'Failed to open case.');
        } finally {
            button.disabled = false;
        }
    });
})();

// ─── Case timeline (edit / delete) ───────────────────────────────────
(function() {
    const timeline = document.querySelector('.case-timeline');
    if (!timeline) return;

    const caseUrl = (idx) => `/dashboard/guild/${GUILD_ID}/cases/${idx}`;

    timeline.addEventListener('click', async (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;
        const entry = btn.closest('.case-entry');
        if (!entry) return;
        const idx = entry.dataset.caseIndex;

        if (btn.classList.contains('case-delete')) {
            if (!confirm(`Delete case #${idx}? This cannot be undone.`)) return;
            btn.disabled = true;
            try {
                const resp = await fetch(caseUrl(idx), { method: 'DELETE' });
                const data = await resp.json();
                if (data.error) { alert(data.error); btn.disabled = false; return; }
                entry.remove();
                if (!timeline.querySelector('.case-entry')) location.reload();
            } catch {
                alert('Failed to delete case.');
                btn.disabled = false;
            }
        } else if (btn.classList.contains('case-edit')) {
            beginEdit(entry);
        }
    });

    function beginEdit(entry) {
        if (entry.querySelector('.case-edit-form')) return; // already editing
        const reasonEl = entry.querySelector('.case-reason');
        const current = reasonEl.classList.contains('is-empty') ? '' : reasonEl.textContent.trim();

        const form = document.createElement('form');
        form.className = 'case-edit-form';
        form.innerHTML =
            '<input type="text" class="case-edit-input" maxlength="500" placeholder="New reason" required>'
            + '<button type="submit" class="button small primary">Save</button>'
            + '<button type="button" class="button small case-edit-cancel">Cancel</button>';
        const input = form.querySelector('.case-edit-input');
        input.value = current;

        reasonEl.hidden = true;
        reasonEl.insertAdjacentElement('afterend', form);
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);

        const close = () => { form.remove(); reasonEl.hidden = false; };
        form.querySelector('.case-edit-cancel').addEventListener('click', close);
        input.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') close(); });

        form.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const reason = input.value.trim();
            if (!reason) { input.focus(); return; } // Percy requires a non-empty reason
            const saveBtn = form.querySelector('button[type="submit"]');
            saveBtn.disabled = true;
            try {
                const resp = await fetch(caseUrl(entry.dataset.caseIndex), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason }),
                });
                const data = await resp.json();
                if (data.error) { alert(data.error); saveBtn.disabled = false; return; }
                reasonEl.textContent = reason;
                reasonEl.classList.remove('is-empty');
                close();
            } catch {
                alert('Failed to update case.');
                saveBtn.disabled = false;
            }
        });
    }
})();

// ─── Command activity heatmap ────────────────────────────────────────
(function() {
    const container = document.getElementById('activity-heatmap');
    if (!container) return;

    fetch(`/dashboard/guild/${GUILD_ID}/members/${MEMBER_ID}/activity`)
        .then(r => r.json())
        .then(data => {
            if (!data.activity || data.error) {
                container.innerHTML = '<div class="empty-state"><p>No activity data available.</p></div>';
                return;
            }

            const activityMap = {};
            let maxCount = 0;
            data.activity.forEach(d => {
                activityMap[d.day] = d.count;
                if (d.count > maxCount) maxCount = d.count;
            });

            const today = new Date();
            const startDate = new Date(today);
            startDate.setDate(startDate.getDate() - 364);
            // Align to Sunday
            startDate.setDate(startDate.getDate() - startDate.getDay());

            function getLevel(count) {
                if (count === 0) return '';
                const q = maxCount / 4;
                if (count <= q) return 'l1';
                if (count <= q * 2) return 'l2';
                if (count <= q * 3) return 'l3';
                return 'l4';
            }

            function fmt(d) {
                return d.toISOString().split('T')[0];
            }

            const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

            // Build weeks (columns of 7 days), recording the month each column
            // starts in so we can render aligned month labels above the grid.
            const colMonths = [];
            let gridHtml = '<div class="heatmap-grid">';
            const d = new Date(startDate);
            while (d <= today) {
                colMonths.push(d.getMonth());
                gridHtml += '<div class="heatmap-col">';
                for (let row = 0; row < 7; row++) {
                    const key = fmt(d);
                    const count = activityMap[key] || 0;
                    const level = getLevel(count);
                    const title = `${key}: ${count} command${count !== 1 ? 's' : ''}`;
                    if (d <= today) {
                        gridHtml += `<div class="heatmap-cell ${level}" title="${title}"></div>`;
                    } else {
                        gridHtml += `<div class="heatmap-cell is-empty"></div>`;
                    }
                    d.setDate(d.getDate() + 1);
                }
                gridHtml += '</div>';
            }
            gridHtml += '</div>';

            // Shortened month labels — one cell per week column, labelled when
            // the month changes (skipping a label that would land in the last
            // couple of columns, where it'd be clipped at the edge).
            let monthsHtml = '<div class="heatmap-months">';
            let prevMonth = -1;
            colMonths.forEach((month, i) => {
                let label = '';
                if (month !== prevMonth) {
                    if (i === 0 || i < colMonths.length - 2) label = MONTHS[month];
                    prevMonth = month;
                }
                monthsHtml += `<span class="heatmap-month">${label}</span>`;
            });
            monthsHtml += '</div>';

            // Legend
            const legendHtml = `<div class="heatmap-legend">
                <span>Less</span>
                <div class="heatmap-cell"></div>
                <div class="heatmap-cell l1"></div>
                <div class="heatmap-cell l2"></div>
                <div class="heatmap-cell l3"></div>
                <div class="heatmap-cell l4"></div>
                <span>More</span>
            </div>`;

            container.innerHTML = monthsHtml + gridHtml + legendHtml;
        })
        .catch(() => {
            container.innerHTML = '<div class="empty-state"><p>Failed to load activity data.</p></div>';
        });
})();
