/* Percy Dashboard — live bot stats over the shared server-sent event stream (PercyLive, common.js).
   Percy pushes a `stats` snapshot every ~10 s while this page is open; without the stream the
   server-rendered numbers simply stay as they were. */

(function percyLiveStats() {
    if (!window.PercyLive || typeof GUILD_ID === 'undefined') return;

    window.PercyLive.connect(GUILD_ID, 'stats', (ev) => {
        if (ev.type !== 'stats' || !ev.data) return;

        const tiles = document.getElementById('bot-stats-tiles');
        if (!tiles) return;

        for (const [key, value] of Object.entries(ev.data)) {
            const el = tiles.querySelector(`[data-key="${key}"]`);
            if (!el) continue;
            if (key === 'latency_ms') {
                el.textContent = Math.round(value) + 'ms';
            } else {
                el.textContent = typeof value === 'number' ? value.toLocaleString() : value;
            }
        }
    });
})();
