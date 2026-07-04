/* Percy personal dashboard — settings form, consent-tracked history
   (names, avatars, per-day presence activity timeline) and the data
   download/delete controls. GUILD_ID is injected by the template. */

(function () {
    'use strict';

    // -- Settings form ---------------------------------------------------------
    const form = document.getElementById('user-settings-form');
    const statusEl = document.getElementById('settings-status');
    const saveBtn = document.getElementById('settings-save-btn');

    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            saveBtn.disabled = true;
            statusEl.textContent = 'Saving…';
            statusEl.className = 'setting-status';

            const payload = {
                timezone: form.querySelector('[name="timezone"]').value.trim() || null,
                track_presence: form.querySelector('[name="track_presence"]').checked,
                track_history: form.querySelector('[name="track_history"]').checked,
            };

            try {
                const res = await fetch(`/dashboard/guild/${GUILD_ID}/me/settings`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await res.json();
                if (res.ok && data.ok) {
                    statusEl.textContent = 'Saved';
                    statusEl.className = 'setting-status is-success';
                    if (window.showToast) showToast('success', 'Settings saved.');
                } else {
                    statusEl.textContent = data.error || 'Failed to save';
                    statusEl.className = 'setting-status is-error';
                    if (window.showToast) showToast('error', data.error || 'Failed to save settings.');
                }
            } catch (err) {
                statusEl.textContent = 'Network error';
                statusEl.className = 'setting-status is-error';
                if (window.showToast) showToast('error', 'Network error — check your connection.');
            } finally {
                saveBtn.disabled = false;
                setTimeout(() => { statusEl.textContent = ''; }, 4000);
            }
        });
    }

    // -- History (names, avatars, presence) ------------------------------------
    const esc = (s) => String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    // `tsHtml` (from timestamps.js) humanises an ISO string; fall back to raw.
    const ts = (iso) => (window.tsHtml ? window.tsHtml(iso) : esc(iso || '—'));

    function renderNameList(el, entries) {
        if (!el) return;
        if (!entries.length) {
            el.innerHTML = '<li class="history-empty">No changes recorded.</li>';
            return;
        }
        el.innerHTML = entries.map((e) =>
            `<li><span class="history-value">${esc(e.name)}</span>` +
            `<span class="history-when">${ts(e.changed_at)}</span></li>`
        ).join('');
    }

    function renderAvatars(el, avatars) {
        if (!el) return;
        el.innerHTML = avatars.slice().reverse().map((a) =>
            `<figure class="avatar-snap">` +
            `<img loading="lazy" alt="Previous avatar" src="data:image/png;base64,${a.image}">` +
            `<figcaption>${ts(a.changed_at)}</figcaption></figure>`
        ).join('');
    }

    // Presence status → canonical key + display metadata. Percy records
    // "Online" / "Idle" / "Do Not Disturb" / "Offline"; we lower-case on lookup
    // so casing can't miss. Colors match Discord's familiar status palette.
    const PRESENCE_META = {
        online:  { label: 'Online',          color: '#3ba55d' },
        idle:    { label: 'Idle',            color: '#faa81a' },
        dnd:     { label: 'Do Not Disturb',  color: '#ed4245' },
        offline: { label: 'Offline',         color: '#747f8d' },
    };
    const PRESENCE_ORDER = ['online', 'idle', 'dnd', 'offline'];
    const DAY_MS = 86400000;

    function statusKey(s) {
        s = (s || '').toLowerCase().trim();
        if (s === 'online') return 'online';
        if (s === 'idle') return 'idle';
        if (s === 'dnd' || s === 'do not disturb') return 'dnd';
        return 'offline'; // offline, invisible, unknown
    }

    const fmtClock = (ms) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Renders a per-day activity timeline: one row per day (newest first), each a
    // 24-hour track of colored status segments, above a legend showing the share
    // of time spent in each status. Far more readable than a stepped line chart
    // and needs no charting library.
    function renderPresence(el, presence) {
        if (!el) return;
        const message = (msg) => { el.innerHTML = `<div class="chart-message">${esc(msg)}</div>`; };

        // Change events → sorted ascending [{ t, key }] (Percy returns newest-first).
        const evts = presence
            .filter((p) => p.changed_at)
            .map((p) => ({ t: Date.parse(p.changed_at), key: statusKey(p.status) }))
            .filter((p) => Number.isFinite(p.t))
            .sort((a, b) => a.t - b.t);

        if (evts.length < 2) { message('Not enough presence history yet — it builds up over time while tracking is on.'); return; }

        const now = Date.now();
        // Look back at most 30 days, and no earlier than the first record.
        const windowStart = Math.max(now - 30 * DAY_MS, evts[0].t);

        // Build [start, end, key] segments; each status persists until the next
        // change, and the final one runs to "now".
        const segments = [];
        for (let i = 0; i < evts.length; i++) {
            const start = evts[i].t;
            const end = i + 1 < evts.length ? evts[i + 1].t : now;
            if (end <= windowStart) continue;
            segments.push({ start: Math.max(start, windowStart), end, key: evts[i].key });
        }
        if (!segments.length) { message('Not enough presence history yet — it builds up over time while tracking is on.'); return; }

        // Totals per status across the visible window (for the legend %).
        const totals = { online: 0, idle: 0, dnd: 0, offline: 0 };
        let grandTotal = 0;
        for (const seg of segments) {
            const dur = seg.end - seg.start;
            totals[seg.key] += dur;
            grandTotal += dur;
        }

        // One row per day, newest at the top.
        const startOfDay = (ms) => { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); };
        const firstDay = startOfDay(windowStart);
        const lastDay = startOfDay(now);
        const days = [];
        for (let d = lastDay; d >= firstDay; d -= DAY_MS) days.push(d);

        const rowFor = (dayStart) => {
            const dayEnd = dayStart + DAY_MS;
            const label = new Date(dayStart).toLocaleDateString([], { weekday: 'short', day: '2-digit', month: 'short' });
            let bars = '';
            for (const seg of segments) {
                const s = Math.max(seg.start, dayStart);
                const e = Math.min(seg.end, dayEnd);
                if (e <= s) continue;
                const left = ((s - dayStart) / DAY_MS) * 100;
                const width = ((e - s) / DAY_MS) * 100;
                const meta = PRESENCE_META[seg.key];
                const title = `${meta.label} · ${fmtClock(s)}–${fmtClock(e)}`;
                bars += `<span class="pt-seg" style="left:${left}%;width:${width}%;background:${meta.color}" title="${esc(title)}"></span>`;
            }
            return `<div class="pt-day"><span class="pt-date">${esc(label)}</span><div class="pt-track">${bars}</div></div>`;
        };

        // Legend chips with percentage of time per status.
        const legend = PRESENCE_ORDER
            .filter((k) => totals[k] > 0)
            .map((k) => {
                const raw = grandTotal ? (totals[k] / grandTotal) * 100 : 0;
                const pct = raw > 0 && raw < 1 ? '<1%' : `${Math.round(raw)}%`;
                const meta = PRESENCE_META[k];
                return `<span class="pt-legend-item"><span class="pt-swatch" style="background:${meta.color}"></span>${esc(meta.label)} <strong>${pct}</strong></span>`;
            })
            .join('');

        el.innerHTML = `
            <div class="presence-timeline">
                <div class="pt-legend">${legend}</div>
                <div class="pt-rows">${days.map(rowFor).join('')}</div>
                <div class="pt-axis"><span class="pt-date"></span><div class="pt-hours"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div></div>
            </div>`;
    }

    async function loadHistory() {
        let data;
        try {
            const res = await fetch(`/dashboard/guild/${GUILD_ID}/me/history`, {
                headers: { 'Accept': 'application/json' },
            });
            if (!res.ok) return;
            data = await res.json();
        } catch (err) {
            return;
        }

        const usernames = data.usernames || [];
        const nicknames = data.nicknames || [];
        const avatars = data.avatars || [];
        const presence = data.presence || [];

        if (usernames.length || nicknames.length) {
            renderNameList(document.getElementById('username-history'), usernames);
            renderNameList(document.getElementById('nickname-history'), nicknames);
            document.getElementById('names-section').hidden = false;
        }
        if (avatars.length) {
            renderAvatars(document.getElementById('avatar-history'), avatars);
            document.getElementById('avatars-section').hidden = false;
        }
        if (presence.length) {
            const section = document.getElementById('presence-section');
            section.hidden = false;
            renderPresence(document.getElementById('presence-chart'), presence);
        }
    }

    loadHistory();

    // -- Data deletion ---------------------------------------------------------
    const deleteBtn = document.getElementById('delete-data-btn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', async function () {
            const ok = window.confirm(
                'Permanently delete your stored presence, avatar and name/nickname history?\n\n' +
                'This cannot be undone.'
            );
            if (!ok) return;

            deleteBtn.disabled = true;
            const original = deleteBtn.textContent;
            deleteBtn.textContent = 'Deleting…';
            try {
                const res = await fetch(`/dashboard/guild/${GUILD_ID}/me/delete-data`, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await res.json().catch(() => ({}));
                if (res.ok && data.ok) {
                    if (window.showToast) showToast('success', 'Your history has been deleted.');
                    setTimeout(() => window.location.reload(), 800);
                } else {
                    if (window.showToast) showToast('error', data.error || 'Failed to delete data.');
                    deleteBtn.disabled = false;
                    deleteBtn.textContent = original;
                }
            } catch (err) {
                if (window.showToast) showToast('error', 'Network error — check your connection.');
                deleteBtn.disabled = false;
                deleteBtn.textContent = original;
            }
        });
    }
})();
