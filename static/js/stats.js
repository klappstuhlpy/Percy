/* Percy Dashboard — live bot stats over the /ws WebSocket (percy topic). */

(function percyLiveStats() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.addEventListener('open', () => {
        ws.send(JSON.stringify({ action: 'subscribe', topics: ['percy'] }));
    });

    ws.addEventListener('message', (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        if (msg.topic !== 'percy' || !msg.data) return;

        const tiles = document.getElementById('bot-stats-tiles');
        if (!tiles) return;

        for (const [key, value] of Object.entries(msg.data)) {
            const el = tiles.querySelector(`[data-key="${key}"]`);
            if (!el) continue;
            if (key === 'latency_ms') {
                el.textContent = Math.round(value) + 'ms';
            } else {
                el.textContent = typeof value === 'number' ? value.toLocaleString() : value;
            }
        }
    });

    ws.addEventListener('close', () => {
        setTimeout(percyLiveStats, 5000);
    });
})();
