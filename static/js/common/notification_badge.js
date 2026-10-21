/* Sidebar badge: unread notifications + pending action requests. Hidden on zero or any error. */
(function () {
    'use strict';
    const badge = document.querySelector('.dash-nav-badge');
    if (!badge || !window.PERCY_DASH) return;
    async function refresh() {
        try {
            const res = await fetch(`/dashboard/guild/${window.PERCY_DASH.guildId}/notifications/summary`, {
                headers: { 'Accept': 'application/json' },
            });
            const s = await res.json();
            const n = s.ok ? (s.unread || 0) + (s.pending_requests || 0) : 0;
            badge.textContent = n > 99 ? '99+' : String(n);
            badge.hidden = n === 0;
        } catch (e) {
            badge.hidden = true;
        }
    }
    window.PercyBadge = { refresh };
    refresh();
})();
