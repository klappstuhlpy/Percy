/* Notifications page: relative times, page view = read, in-place request decisions, settings card.
   Every control is a plain form first; this only upgrades them. */
(function () {
    'use strict';
    const guildId = (location.pathname.match(/\/guild\/(\d+)/) || [])[1];
    const base = `/dashboard/guild/${guildId}`;

    // -- Relative times (the server renders an absolute fallback) -------------------------
    function rel(iso) {
        const secs = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
        if (isNaN(secs)) return null;
        const f = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
        const abs = Math.abs(secs);
        if (abs < 60) return f.format(secs, 'second');
        if (abs < 3600) return f.format(Math.round(secs / 60), 'minute');
        if (abs < 86400) return f.format(Math.round(secs / 3600), 'hour');
        return f.format(Math.round(secs / 86400), 'day');
    }
    document.querySelectorAll('time[datetime]').forEach((t) => {
        const text = rel(t.getAttribute('datetime'));
        if (text) { t.title = t.textContent; t.textContent = text; }
    });

    // -- Page view marks the feed read -----------------------------------------------------
    const feed = document.getElementById('feed');
    if (feed && +feed.dataset.unread > 0 && feed.dataset.newestId) {
        fetch(`${base}/notifications/read`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ up_to_id: feed.dataset.newestId }),
        }).then((r) => r.json()).then((res) => {
            if (res.ok && window.PercyBadge) window.PercyBadge.refresh();
        }).catch(() => {});
    }

    // JS handles the destructive confirm with PercyConfirm; the native checkbox is the JS-off fallback.
    document.querySelectorAll('.notif-confirm').forEach((l) => { l.hidden = true; l.querySelector('input').required = false; });

    // -- Approve / Deny --------------------------------------------------------------------
    document.querySelectorAll('form.notif-request').forEach((form) => {
        form.addEventListener('submit', async (e) => {
            const decision = e.submitter && e.submitter.value;
            if (!decision || !window.fetch) return; // fall back to the plain form post
            e.preventDefault();
            const { action, target, destructive } = form.dataset;
            if (decision === 'approve' && destructive === 'true') {
                const ok = await PercyConfirm.ask({
                    title: `Approve this ${action}?`,
                    consequence: `${target} will be ${action === 'ban' ? 'banned' : action === 'kick' ? 'kicked' : 'softbanned'} right away.`,
                    undo: action === 'ban' ? 'You can unban them later from the members page.' : null,
                    confirmLabel: `Approve ${action}`,
                    danger: true,
                });
                if (!ok) return;
            }
            const data = new URLSearchParams(new FormData(form));
            data.set('decision', decision);
            form.querySelectorAll('button').forEach((b) => { b.disabled = true; });
            const res = await fetch(form.action, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' },
                body: data,
            }).then((r) => r.json()).catch(() => ({ ok: false, error: 'Network error.' }));
            const message = res.ok ? res.message : (res.error || 'Could not record the decision.');
            showToast(res.ok ? 'success' : 'error', message);
            // Only a settled (or vanished) request retires the card; a refusal that leaves it pending,
            // a network error or a 5xx keeps the buttons so the click can be retried.
            if (!['approved', 'denied', 'cancelled', 'failed', 'expired', 'not_found'].includes(res.status)) {
                form.querySelectorAll('button').forEach((b) => { b.disabled = false; });
                return;
            }
            const p = document.createElement('p');
            p.className = 'notif-meta';
            p.textContent = message;
            form.replaceChildren(p);
            if (window.PercyBadge) window.PercyBadge.refresh();
        });
    });

    // -- Request settings ------------------------------------------------------------------
    const settings = document.getElementById('request-settings-form');
    if (settings) {
        PercyUnsaved.watchForm(settings, {
            label: 'Action requests',
            save: async (f) => {
                const res = await fetch(`${base}/request-settings`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({
                        channel_id: f.querySelector('[name="channel_id"]').value || null,
                        role_ids: [...f.querySelector('[name="role_ids"]').selectedOptions].map((o) => o.value),
                    }),
                });
                return res.json();
            },
        });
    }
})();
