/* Percy Dashboard — Analytics: the metric/range picker + uPlot trend chart and
   the top-commands / headline-tile refresh. Expects GUILD_ID, window.showToast,
   and uPlot loaded before this file. */

(function() {
    const guildId = GUILD_ID;
    const base = `/dashboard/guild/${guildId}/analytics`;
    const el = document.getElementById('analytics-chart');
    const metricSel = document.getElementById('metric-select');
    const rangeSel = document.getElementById('range-select');
    if (!el) return;

    const message = (msg) => { el.innerHTML = `<div class="chart-message">${msg}</div>`; };
    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#8351ed';
    const faint = getComputedStyle(document.documentElement).getPropertyValue('--text-faint').trim() || '#7a7a82';
    const size = () => ({ width: Math.max(220, el.clientWidth - 4), height: 260 });

    let chart = null;

    // Pretty labels for the legend, keyed by the metric value.
    const METRIC_LABELS = {
        commands: 'Commands',
        command_failures: 'Failures',
        xp: 'XP earned',
        members: 'New members',
    };

    function render(points, metric) {
        if (!window.uPlot) { message('Chart library failed to load (CDN blocked?).'); return; }
        if (!points || points.length === 0) { message('No data in this window yet.'); return; }

        const xs = points.map(p => Math.floor(Date.parse(p.bucket) / 1000));
        const ys = points.map(p => p.value);

        // Rebuild the chart on each change — the series label and scales depend on
        // the metric, and uPlot has no cheap in-place relabel.
        el.innerHTML = '';
        if (chart) { chart.destroy(); chart = null; }
        chart = new uPlot({
            ...size(),
            cursor: { drag: { setScale: false } },
            legend: { live: true },
            scales: { x: { time: true }, y: { auto: true } },
            series: [
                {},
                {
                    label: METRIC_LABELS[metric] || metric,
                    stroke: accent,
                    width: 2,
                    fill: accent + '22',
                    points: { show: points.length < 60, size: 4 },
                },
            ],
            axes: [
                { stroke: faint },
                { stroke: faint, grid: { stroke: 'rgba(127,127,127,0.15)' } },
            ],
        }, [xs, ys], el);
    }

    // Initial paint from the server-embedded series (commands / current range).
    let initial = [];
    try { initial = JSON.parse(el.dataset.points || '[]'); } catch { initial = []; }
    render(initial, 'commands');

    async function refreshSeries() {
        const metric = metricSel.value;
        const range = rangeSel.value;
        message('Loading chart…');
        try {
            const resp = await fetch(`${base}/series?metric=${encodeURIComponent(metric)}&range=${encodeURIComponent(range)}`, {
                headers: { 'Accept': 'application/json' },
            });
            const data = await resp.json();
            if (data.error) { message(data.error); return; }
            render(data.points, metric);
        } catch { message('Network error loading chart.'); }
    }

    // Refresh the headline tiles + top-commands table when only the range changes.
    async function refreshSummary() {
        const range = rangeSel.value;
        try {
            const resp = await fetch(`${base}/summary?range=${encodeURIComponent(range)}`, {
                headers: { 'Accept': 'application/json' },
            });
            const data = await resp.json();
            if (data.error) return;
            const body = document.getElementById('top-commands-body');
            if (body && Array.isArray(data.top_commands)) {
                body.innerHTML = data.top_commands.length
                    ? data.top_commands.map(r =>
                        `<tr><td data-label="Command"><code>${escapeHtml(r.command)}</code></td><td data-label="Uses">${r.uses}</td></tr>`).join('')
                    : '<tr><td colspan="2" class="empty-state">No command usage recorded in this window yet.</td></tr>';
            }
        } catch { /* tiles just stay put on a failed refresh */ }
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    metricSel.addEventListener('change', refreshSeries);
    rangeSel.addEventListener('change', () => { refreshSeries(); refreshSummary(); });

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => { if (chart) chart.setSize(size()); }, 150);
    });
})();
