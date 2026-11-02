/* Percy Dashboard — Members management.

   Each row opens a "Manage" menu of moderation actions; the shared confirmation modal
   grows the fields that action needs (a duration for timeout/mute, a message-history
   window for ban/softban, a message count for purge). Percy owns the action whitelist
   and every action lands in the modlog. */

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
    const durationField = document.getElementById('modal-duration-field');
    const durationSelect = document.getElementById('modal-duration');
    const durationForever = document.getElementById('modal-duration-forever');
    const purgeField = document.getElementById('modal-purge-field');
    const purgeDays = document.getElementById('modal-purge-days');
    const limitField = document.getElementById('modal-limit-field');
    const limitInput = document.getElementById('modal-limit');
    const nicknameField = document.getElementById('modal-nickname-field');
    const nicknameInput = document.getElementById('modal-nickname');
    const channelField = document.getElementById('modal-channel-field');
    const channelInput = document.getElementById('modal-channel');

    // What each action is called, what it needs, and how destructive it looks.
    // `removes` marks the actions after which the member is no longer in the guild,
    // so their row is dropped from the table.
    const ACTIONS = {
        warn:     { label: 'Warn',     verb: 'warn' },
        timeout:  { label: 'Timeout',  verb: 'time out', duration: true },
        mute:     { label: 'Mute',     verb: 'mute', duration: true, optionalDuration: true, needsMuteRole: true },
        unmute:   { label: 'Unmute',   verb: 'unmute', needsMuteRole: true },
        untimeout:{ label: 'Remove Timeout', verb: 'remove the timeout from' },
        purge:    { label: 'Purge Messages', verb: 'purge the recent messages of', limit: true },
        kick:     { label: 'Kick',     verb: 'kick', danger: true, removes: true },
        softban:  { label: 'Softban',  verb: 'softban', danger: true, purgeDays: true, removes: true },
        ban:      { label: 'Ban',      verb: 'ban', danger: true, purgeDays: true, removes: true },
        voicekick: { label: 'Voice Kick', verb: 'disconnect from voice' },
        voiceban:  { label: 'Voice Ban', verb: 'voice ban', duration: true, optionalDuration: true },
        voiceunban:{ label: 'Voice Unban', verb: 'lift the voice ban on' },
        nick:      { label: 'Set Nickname', verb: 'change the nickname of', nickname: true },
        nickfreeze:{ label: 'Freeze Nickname', verb: 'freeze the nickname of', nickname: true },
        nickunfreeze:{ label: 'Unfreeze Nickname', verb: 'unfreeze the nickname of' },
        channelmute:{ label: 'Channel Mute', verb: 'mute in one channel', duration: true, optionalDuration: true, channel: true },
        channelunmute:{ label: 'Channel Unmute', verb: 'unmute in one channel', channel: true },
    };

    // How each bulk action is walked back, for the confirm dialog (none = cannot be undone).
    const BULK_UNDO = {
        warn: "Delete the cases to remove the warnings.",
        timeout: "Remove the timeout at any time.",
        mute: "Unmute them at any time.",
        unmute: "Mute them again at any time.",
        untimeout: "Time them out again at any time.",
        ban: "Unban them at any time; they have to rejoin themselves.",
    };

    // The order the row menu offers them in: reversible first, terminal last.
    const MENU = ['warn', 'timeout', 'untimeout', 'mute', 'unmute', 'voicekick', 'voiceban', 'voiceunban', 'nick', 'nickfreeze', 'nickunfreeze', 'channelmute', 'channelunmute', 'purge', 'kick', 'softban', 'ban'];

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
                        `<button class="button small member-menu-btn" data-user-id="${m.id}" data-name="${esc(m.display_name)}" aria-haspopup="true" aria-expanded="false">Manage &#9662;</button>`;

                    // Mirror the server-rendered row structure so the responsive
                    // card layout, the selection checkbox and the profile link all
                    // work identically on loaded-more rows.
                    row.innerHTML = `
                        <td><input type="checkbox" class="bulk-check" data-user-id="${m.id}" ${m.bot ? 'disabled' : ''}></td>
                        <td>
                            <a class="member-cell" href="/dashboard/guild/${GUILD_ID}/members/${m.id}">
                                <img class="member-avatar" src="${m.avatar_url.split('?')[0]}?size=64" alt="" width="36" height="36">
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

    // ─── Row action menu ──────────────────────────────────────────────
    let openMenu = null;

    function closeMenu() {
        if (!openMenu) return;
        const btn = openMenu.previousElementSibling;
        if (btn) btn.setAttribute('aria-expanded', 'false');
        openMenu.remove();
        openMenu = null;
    }

    function toggleMenu(btn) {
        const wasOpen = openMenu && openMenu.dataset.userId === btn.dataset.userId;
        closeMenu();
        if (wasOpen) return;

        const menu = document.createElement('div');
        menu.className = 'member-menu';
        menu.dataset.userId = btn.dataset.userId;
        menu.innerHTML = MENU
            // A mute needs the guild's mute role; without one the API would only 400.
            .filter(name => !ACTIONS[name].needsMuteRole || MUTE_ROLE_CONFIGURED)
            .map(name => {
                const action = ACTIONS[name];
                return `<button type="button" class="member-menu-item${action.danger ? ' danger' : ''}" data-action="${name}">${action.label}</button>`;
            })
            .join('');

        btn.after(menu);
        // The table wrapper scrolls (and so clips) on desktop; flip up when the menu would be cut off.
        const clip = (menu.closest('.members-table-wrap') || document.documentElement).getBoundingClientRect();
        const m = menu.getBoundingClientRect(), b = btn.getBoundingClientRect();
        const above = b.top - clip.top;
        if (m.bottom > clip.bottom && above >= m.height + 4 && above > clip.bottom - b.bottom) menu.classList.add('member-menu--up');
        btn.setAttribute('aria-expanded', 'true');
        openMenu = menu;
    }

    document.addEventListener('click', (e) => {
        const trigger = e.target.closest('.member-menu-btn');
        if (trigger) {
            toggleMenu(trigger);
            return;
        }
        const item = e.target.closest('.member-menu-item');
        if (item) {
            const btn = item.closest('.member-actions').querySelector('.member-menu-btn');
            openActionModal(item.dataset.action, btn.dataset.userId, btn.dataset.name);
            closeMenu();
            return;
        }
        closeMenu();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { closeMenu(); closeActionModal(); }
    });

    // ─── Action modal ─────────────────────────────────────────────────
    function openActionModal(name, userId, displayName) {
        const action = ACTIONS[name];
        if (!action) return;

        pendingAction = { name, userId };
        modalTitle.textContent = `${action.label} Member`;
        modalDesc.textContent = `Are you sure you want to ${action.verb} ${displayName}?`;
        modalReason.value = '';

        durationField.hidden = !action.duration;
        // An indefinite mute is valid; an indefinite timeout is not (Discord caps it).
        durationForever.hidden = !action.optionalDuration;
        durationSelect.value = '3600';
        purgeField.hidden = !action.purgeDays;
        purgeDays.value = '0';
        limitField.hidden = !action.limit;
        limitInput.value = '100';
        nicknameField.hidden = !action.nickname;
        nicknameInput.value = '';
        channelField.hidden = !action.channel;
        channelInput.value = '';

        modalConfirm.className = action.danger ? 'button small danger' : 'button small primary';
        modalConfirm.textContent = action.label;
        modal.hidden = false;
    }

    function closeActionModal() {
        modal.hidden = true;
        pendingAction = null;
    }

    modalCancel.addEventListener('click', closeActionModal);
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeActionModal();
    });

    // Build the request body from whichever fields this action revealed.
    function actionBody(name) {
        const action = ACTIONS[name];
        const body = { action: name, reason: modalReason.value.trim() || undefined };
        if (action.duration && durationSelect.value) body.duration = parseInt(durationSelect.value, 10);
        if (action.purgeDays) body.delete_message_days = parseInt(purgeDays.value, 10);
        if (action.limit) body.limit = parseInt(limitInput.value, 10);
        if (action.nickname) body.nickname = nicknameInput.value.trim();
        if (action.channel) body.channel_id = channelInput.value.trim();
        return body;
    }

    modalConfirm.addEventListener('click', async () => {
        if (!pendingAction) return;
        const { name, userId } = pendingAction;
        modalConfirm.disabled = true;

        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/members/${userId}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify(actionBody(name)),
            });
            const data = await resp.json();
            if (data.ok) {
                const deleted = data.deleted !== undefined ? ` (${data.deleted} messages deleted)` : '';
                showToast('success', `${ACTIONS[name].label} applied.${deleted}`);
                if (ACTIONS[name].removes) {
                    const row = tbody.querySelector(`tr[data-user-id="${userId}"]`);
                    if (row) row.remove();
                }
            } else {
                showToast('error', data.error || `Failed to ${ACTIONS[name].verb} the member.`);
            }
        } catch {
            showToast('error', 'Network error.');
        } finally {
            closeActionModal();
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

    async function doBulkAction(name) {
        const userIds = getSelected();
        if (!userIds.length) return;

        const action = ACTIONS[name];
        const n = userIds.length;
        const who = `${n} member${n === 1 ? "" : "s"}`;
        if (!(await PercyConfirm.ask({
            title: `${action.label} ${who}?`,
            consequence: `Percy will ${action.verb} ${who}${action.removes ? " and they leave the server" : ""}. Each gets a case in the modlog.`,
            undo: BULK_UNDO[name] || null,
            confirmLabel: `${action.label} ${who}`,
            danger: !!action.danger,
            // Removing many people at once is the one mis-click that cannot be walked back.
            typeToConfirm: action.removes && n >= 5 ? String(n) : null,
        }))) return;

        const body = { user_ids: userIds, action: name };
        if (action.duration) {
            // Reuse the modal's duration vocabulary rather than inventing a second one.
            const hours = prompt('Timeout duration in hours (max 672):', '1');
            if (hours === null) return;
            const seconds = Math.round(parseFloat(hours) * 3600);
            if (!Number.isFinite(seconds) || seconds < 60) {
                showToast('error', 'Enter a duration of at least one minute.');
                return;
            }
            body.duration = seconds;
        }

        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/members/bulk-action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.ok) {
                showToast('success', `${data.successes} member(s): ${action.label.toLowerCase()} applied.`);
                if (data.failures && data.failures.length > 0) {
                    showToast('warning', `${data.failures.length} failed.`);
                }
                if (action.removes) {
                    userIds.forEach(uid => {
                        const row = tbody.querySelector(`tr[data-user-id="${uid}"]`);
                        if (row && !data.failures.some(f => String(f.user_id) === uid)) row.remove();
                    });
                }
                updateBulkUI();
            } else {
                showToast('error', data.error || 'Bulk action failed.');
            }
        } catch {
            showToast('error', 'Network error.');
        }
    }

    document.querySelectorAll('[data-bulk]').forEach(btn => {
        btn.addEventListener('click', () => doBulkAction(btn.dataset.bulk));
    });
})();
