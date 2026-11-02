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
            if (!(await PercyConfirm.ask({ title: `Delete case #${idx}?`, consequence: "The case and its post in the modlog channel are removed. The case number is never reused.", undo: null, confirmLabel: "Delete case", danger: true }))) return;
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

// ─── Moderation actions ──────────────────────────────────────────────
// The same action vocabulary the members table uses, driving one shared modal: each
// action reveals only the fields it needs (a duration, a message-history window, a count).
(function() {
    const modal = document.getElementById('action-modal');
    if (!modal) return;

    const ACTIONS = {
        warn:      { label: 'Warn', verb: 'warn' },
        timeout:   { label: 'Timeout', verb: 'time out', duration: true },
        untimeout: { label: 'Remove Timeout', verb: 'remove the timeout from' },
        mute:      { label: 'Mute', verb: 'mute', duration: true, optionalDuration: true },
        unmute:    { label: 'Unmute', verb: 'unmute' },
        purge:     { label: 'Purge Messages', verb: 'purge the recent messages of', limit: true },
        kick:      { label: 'Kick', verb: 'kick', danger: true },
        softban:   { label: 'Softban', verb: 'softban', danger: true, purgeDays: true },
        ban:       { label: 'Ban', verb: 'ban', danger: true, purgeDays: true },
        voicekick: { label: 'Voice Kick', verb: 'disconnect from voice' },
        voiceban:  { label: 'Voice Ban', verb: 'voice ban', duration: true, optionalDuration: true },
        voiceunban:{ label: 'Voice Unban', verb: 'lift the voice ban on' },
        nick:      { label: 'Set Nickname', verb: 'change the nickname of', nickname: true },
        nickfreeze:{ label: 'Freeze Nickname', verb: 'freeze the nickname of', nickname: true },
        nickunfreeze:{ label: 'Unfreeze Nickname', verb: 'unfreeze the nickname of' },
        channelmute:{ label: 'Channel Mute', verb: 'mute in one channel', duration: true, optionalDuration: true, channel: true },
        channelunmute:{ label: 'Channel Unmute', verb: 'unmute in one channel', channel: true },
    };

    const title = document.getElementById('modal-title');
    const desc = document.getElementById('modal-description');
    const reason = document.getElementById('modal-reason');
    const confirmBtn = document.getElementById('modal-confirm');
    const cancelBtn = document.getElementById('modal-cancel');
    const durationField = document.getElementById('modal-duration-field');
    const duration = document.getElementById('modal-duration');
    const durationForever = document.getElementById('modal-duration-forever');
    const purgeField = document.getElementById('modal-purge-field');
    const purgeDays = document.getElementById('modal-purge-days');
    const limitField = document.getElementById('modal-limit-field');
    const limitInput = document.getElementById('modal-limit');
    const nicknameField = document.getElementById('modal-nickname-field');
    const nicknameInput = document.getElementById('modal-nickname');
    const channelField = document.getElementById('modal-channel-field');
    const channelInput = document.getElementById('modal-channel');

    let pending = null;

    function close() {
        modal.hidden = true;
        pending = null;
    }

    document.querySelectorAll('[data-member-action]').forEach(btn => {
        btn.addEventListener('click', () => {
            const name = btn.dataset.memberAction;
            const action = ACTIONS[name];
            if (!action) return;

            pending = name;
            title.textContent = action.label + ' Member';
            desc.textContent = 'Are you sure you want to ' + action.verb + ' ' + MEMBER_NAME + '?';
            reason.value = '';

            durationField.hidden = !action.duration;
            // An indefinite mute is valid; an indefinite timeout is not (Discord caps it).
            durationForever.hidden = !action.optionalDuration;
            duration.value = '3600';
            purgeField.hidden = !action.purgeDays;
            purgeDays.value = '';
            limitField.hidden = !action.limit;
            limitInput.value = '100';
            nicknameField.hidden = !action.nickname;
            nicknameInput.value = '';
            channelField.hidden = !action.channel;
            channelInput.value = '';

            confirmBtn.className = action.danger ? 'button small danger' : 'button small primary';
            confirmBtn.textContent = action.label;
            modal.hidden = false;
        });
    });

    cancelBtn.addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

    confirmBtn.addEventListener('click', async () => {
        if (!pending) return;
        const action = ACTIONS[pending];
        const body = { action: pending, reason: reason.value.trim() || undefined };
        if (action.duration && duration.value) body.duration = parseInt(duration.value, 10);
        // Blank = server default: sending a value would override mod_default_ban_delete_days.
        if (action.purgeDays && purgeDays.value !== '') body.delete_message_days = parseInt(purgeDays.value, 10);
        if (action.limit) body.limit = parseInt(limitInput.value, 10);
        if (action.nickname) body.nickname = nicknameInput.value.trim();
        if (action.channel) body.channel_id = channelInput.value.trim();

        confirmBtn.disabled = true;
        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/members/${MEMBER_ID}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.ok) {
                const deleted = data.deleted !== undefined ? ` (${data.deleted} messages deleted)` : '';
                showToast('success', `${action.label} applied.${deleted}`);
                // The header carries the mute/timeout badges and the case list just grew.
                setTimeout(() => location.reload(), 600);
            } else {
                showToast('error', data.error || `Failed to ${action.verb} the member.`);
            }
        } catch {
            showToast('error', 'Network error.');
        } finally {
            confirmBtn.disabled = false;
            close();
        }
    });
})();

// ─── Recent messages ─────────────────────────────────────────────────
// Loaded on demand: the endpoint scans live channel history, which is far too costly to
// fire on every page view.
(function() {
    const btn = document.getElementById('load-messages-btn');
    const list = document.getElementById('member-messages');
    const meta = document.getElementById('messages-meta');
    if (!btn || !list) return;

    const escape = (s) => String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    function messageHtml(m) {
        const attachments = (m.attachments || []).map(
            url => `<a class="msg-attachment" href="${escape(url)}" target="_blank" rel="noopener">attachment</a>`
        ).join('');
        const embeds = m.embed_count ? `<span class="msg-embed-count">${m.embed_count} embed(s)</span>` : '';
        const content = m.content
            ? `<p class="msg-content">${escape(m.content)}</p>`
            : '<p class="msg-content msg-empty">(no text content)</p>';
        return `<article class="member-message">
            <header class="msg-head">
                <span class="msg-channel">#${escape(m.channel_name)}</span>
                <time class="js-ts" datetime="${escape(m.created_at)}">${escape(m.created_at)}</time>
                ${m.edited_at ? '<span class="msg-edited">(edited)</span>' : ''}
                <a class="msg-jump" href="${escape(m.jump_url)}" target="_blank" rel="noopener">Jump</a>
            </header>
            ${content}
            <div class="msg-extras">${attachments}${embeds}</div>
        </article>`;
    }

    btn.addEventListener('click', async () => {
        btn.disabled = true;
        btn.textContent = 'Loading…';
        try {
            const resp = await fetch(`/dashboard/guild/${GUILD_ID}/members/${MEMBER_ID}/messages?limit=25`);
            const data = await resp.json();
            if (data.error) {
                showToast('error', data.error);
                return;
            }
            if (!data.messages || !data.messages.length) {
                list.innerHTML = '<div class="empty-state"><p>No recent messages found in the scanned channels.</p></div>';
            } else {
                list.innerHTML = data.messages.map(messageHtml).join('');
                if (window.formatTimestamps) window.formatTimestamps(list);
            }
            meta.textContent = `Scanned ${data.scanned_channels} channel(s)`
                + (data.partial ? ' — some channels were skipped.' : '.');
        } catch {
            showToast('error', 'Failed to load messages.');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Reload recent messages';
        }
    });
})();
