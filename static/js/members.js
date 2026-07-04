/* Percy Dashboard — Members management */

(function() {
    const tbody = document.getElementById('members-tbody');
    const searchInput = document.getElementById('member-search');
    const loadMoreBtn = document.getElementById('load-more');
    const modal = document.getElementById('action-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalDesc = document.getElementById('modal-description');
    const modalReason = document.getElementById('modal-reason');
    const modalCancel = document.getElementById('modal-cancel');
    const modalConfirm = document.getElementById('modal-confirm');

    let pendingAction = null;

    // Escape user-controlled text before interpolating into innerHTML.
    function esc(s) {
        return String(s).replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    }

    // Resolve role names from the ROLES global
    const roleMap = {};
    if (typeof ROLES !== 'undefined') {
        ROLES.forEach(r => { roleMap[r.id] = r; });
    }
    document.querySelectorAll('.role-chip').forEach(chip => {
        const role = roleMap[chip.dataset.roleId];
        if (role) {
            chip.textContent = '@' + role.name;
            if (role.color) {
                chip.style.borderColor = '#' + role.color.toString(16).padStart(6, '0');
            }
        }
    });

    // Search filtering
    let searchTimeout;
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            const query = searchInput.value.toLowerCase();
            tbody.querySelectorAll('tr').forEach(row => {
                row.hidden = query && !row.dataset.name.includes(query);
            });
        }, 200);
    });

    // Load more pagination
    loadMoreBtn.addEventListener('click', async () => {
        const rows = tbody.querySelectorAll('tr');
        const lastId = rows.length ? rows[rows.length - 1].dataset.userId : '0';
        loadMoreBtn.disabled = true;

        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/members.json?after=${lastId}`);
            const data = await resp.json();
            if (data.members && data.members.length > 0) {
                data.members.forEach(m => {
                    const row = document.createElement('tr');
                    row.dataset.userId = m.id;
                    row.dataset.name = m.display_name.toLowerCase();
                    row.dataset.bot = m.bot;

                    const rolesHtml = m.roles.map(rid => {
                        const role = roleMap[rid];
                        const name = role ? '@' + role.name : rid;
                        const style = role && role.color ? `border-color:#${role.color.toString(16).padStart(6,'0')}` : '';
                        return `<span class="role-chip" style="${style}">${esc(name)}</span>`;
                    }).join('');

                    const actionsHtml = m.bot ? '' :
                        `<button class="button small" data-action="kick" data-user-id="${m.id}" data-name="${esc(m.display_name)}">Kick</button>` +
                        `<button class="button small danger" data-action="ban" data-user-id="${m.id}" data-name="${esc(m.display_name)}">Ban</button>`;

                    // Mirror the server-rendered row structure so the responsive
                    // card layout, the selection checkbox and the profile link all
                    // work identically on loaded-more rows.
                    row.innerHTML = `
                        <td><input type="checkbox" class="bulk-check" data-user-id="${m.id}" ${m.bot ? 'disabled' : ''}></td>
                        <td>
                            <a class="member-cell" href="/dashboard/guild/${GUILD_ID}/members/${m.id}">
                                <img class="member-avatar" src="${m.avatar_url}?size=64" alt="" width="36" height="36">
                                <div class="member-info">
                                    <span class="member-name">${esc(m.display_name)}</span>
                                    <span class="member-username">${esc(m.name)}${m.bot ? ' <span class="badge">BOT</span>' : ''}</span>
                                </div>
                            </a>
                        </td>
                        <td class="member-joined"><time class="js-ts">${m.joined_at || '—'}</time></td>
                        <td><div class="member-roles">${rolesHtml}</div></td>
                        <td><div class="member-actions">${actionsHtml}</div></td>
                    `;
                    tbody.appendChild(row);
                });
                if (window.formatTimestamps) window.formatTimestamps(tbody);
                if (data.members.length < 100) loadMoreBtn.hidden = true;
            } else {
                loadMoreBtn.hidden = true;
            }
        } catch {
            showToast('error', 'Failed to load members.');
        } finally {
            loadMoreBtn.disabled = false;
        }
    });

    // Action buttons (delegated)
    tbody.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;

        const action = btn.dataset.action;
        const userId = btn.dataset.userId;
        const name = btn.dataset.name;

        pendingAction = { action, userId };
        modalTitle.textContent = action === 'ban' ? 'Ban Member' : 'Kick Member';
        modalDesc.textContent = `Are you sure you want to ${action} ${name}?`;
        modalReason.value = '';
        modal.hidden = false;
    });

    // Modal handlers
    modalCancel.addEventListener('click', () => {
        modal.hidden = true;
        pendingAction = null;
    });

    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.hidden = true;
            pendingAction = null;
        }
    });

    modalConfirm.addEventListener('click', async () => {
        if (!pendingAction) return;
        modalConfirm.disabled = true;

        const { action, userId } = pendingAction;
        const reason = modalReason.value.trim() || undefined;

        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/members/${userId}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, reason }),
            });
            const data = await resp.json();
            if (data.ok) {
                showToast('success', `Successfully ${action === 'ban' ? 'banned' : 'kicked'} the member.`);
                const row = tbody.querySelector(`tr[data-user-id="${userId}"]`);
                if (row) row.remove();
            } else {
                showToast('error', data.error || `Failed to ${action} member.`);
            }
        } catch {
            showToast('error', 'Network error.');
        } finally {
            modal.hidden = true;
            pendingAction = null;
            modalConfirm.disabled = false;
        }
    });

    // ─── Bulk Selection ──────────────────────────────────────────────
    const bulkToolbar = document.getElementById('bulk-toolbar');
    const bulkCount = document.getElementById('bulk-selected-count');
    const selectAll = document.getElementById('bulk-select-all');

    function getSelected() {
        return Array.from(tbody.querySelectorAll('.bulk-check:checked')).map(cb => cb.dataset.userId);
    }

    function updateBulkUI() {
        const selected = getSelected();
        if (bulkToolbar) {
            bulkToolbar.hidden = selected.length === 0;
            bulkCount.textContent = selected.length;
        }
    }

    if (selectAll) {
        selectAll.addEventListener('change', () => {
            const checked = selectAll.checked;
            tbody.querySelectorAll('.bulk-check:not(:disabled)').forEach(cb => {
                if (!cb.closest('tr').hidden) cb.checked = checked;
            });
            updateBulkUI();
        });
    }

    tbody.addEventListener('change', (e) => {
        if (e.target.classList.contains('bulk-check')) updateBulkUI();
    });

    const bulkDeselect = document.getElementById('bulk-deselect');
    if (bulkDeselect) {
        bulkDeselect.addEventListener('click', () => {
            tbody.querySelectorAll('.bulk-check:checked').forEach(cb => cb.checked = false);
            if (selectAll) selectAll.checked = false;
            updateBulkUI();
        });
    }

    async function doBulkAction(action) {
        const userIds = getSelected();
        if (!userIds.length) return;
        if (!confirm(`Are you sure you want to ${action} ${userIds.length} member(s)?`)) return;

        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/members/bulk-action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_ids: userIds, action }),
            });
            const data = await resp.json();
            if (data.ok) {
                showToast('success', `${data.successes} member(s) ${action === 'ban' ? 'banned' : 'kicked'}.`);
                if (data.failures && data.failures.length > 0) {
                    showToast('warning', `${data.failures.length} failed.`);
                }
                userIds.forEach(uid => {
                    const row = tbody.querySelector(`tr[data-user-id="${uid}"]`);
                    if (row && !data.failures.some(f => f.user_id === uid)) row.remove();
                });
                updateBulkUI();
            } else {
                showToast('error', data.error || 'Bulk action failed.');
            }
        } catch {
            showToast('error', 'Network error.');
        }
    }

    const bulkKick = document.getElementById('bulk-kick');
    const bulkBan = document.getElementById('bulk-ban');
    if (bulkKick) bulkKick.addEventListener('click', () => doBulkAction('kick'));
    if (bulkBan) bulkBan.addEventListener('click', () => doBulkAction('ban'));
})();
