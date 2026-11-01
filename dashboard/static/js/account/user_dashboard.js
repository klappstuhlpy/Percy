/* Percy personal dashboard — settings form, consent-tracked history
   (names, avatars, per-day presence activity timeline) and the data
   download/delete controls. GUILD_ID is injected by the template. */

(function () {
    'use strict';

    // -- Settings form ---------------------------------------------------------
    // No Save button: the global tracker (unsaved.js) raises the banner on any edit.
    const form = document.getElementById('user-settings-form');

    if (form) {
        PercyUnsaved.watchForm(form, {
            label: 'My Settings',
            save: async (f) => {
                const res = await fetch(`/dashboard/guild/${GUILD_ID}/me/settings`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({
                        timezone: f.querySelector('[name="timezone"]').value.trim() || null,
                        track_presence: f.querySelector('[name="track_presence"]').checked,
                        track_history: f.querySelector('[name="track_history"]').checked,
                    }),
                });
                return res.json();
            },
        });
    }

    // -- Rank card ---------------------------------------------------------------
    // The card shows first; Customize opens a modal whose preview is the member's real card
    // re-rendered by Percy for the draft. The modal's Save/Cancel replaces the unsaved banner
    // (an explicit exception to the no-Save-button rule), so the form is not PercyUnsaved-watched.
    const cardForm = document.getElementById('rank-card-form');
    const cardImg = document.getElementById('rank-card-img');
    const cardDialog = document.getElementById('rank-card-dialog');
    const cardLive = document.getElementById('rank-card-live');
    const cardStatus = document.getElementById('rank-card-status');

    if (cardForm && cardDialog && cardLive) {
        const selects = [...cardForm.querySelectorAll('select')];
        const cardStyle = () => Object.fromEntries(selects.map((el) => [el.name, el.value]));
        let snapshot = cardStyle();
        let timer = null;
        let ctrl = null;
        let objUrl = null;

        const render = async () => {
            if (ctrl) ctrl.abort();
            const mine = ctrl = new AbortController();
            cardStatus.textContent = 'Rendering…';
            try {
                const res = await fetch(`/dashboard/guild/${GUILD_ID}/me/rank-card.png?${new URLSearchParams(cardStyle())}`, { signal: mine.signal });
                if (!res.ok) throw new Error(res.status);
                const url = URL.createObjectURL(await res.blob());
                if (objUrl) URL.revokeObjectURL(objUrl);
                objUrl = url;
                cardLive.src = url;
                cardStatus.textContent = JSON.stringify(cardStyle()) === JSON.stringify(snapshot)
                    ? 'Your saved card.' : 'Preview only — save to keep it.';
            } catch (e) {
                if (e.name !== 'AbortError') cardStatus.textContent = 'The preview could not be rendered right now.';
            }
        };
        cardForm.addEventListener('change', () => { clearTimeout(timer); timer = setTimeout(render, 250); });

        // Drop pending work and the preview blob; shared by Cancel, Escape and Save.
        const cleanup = () => {
            clearTimeout(timer);
            if (ctrl) ctrl.abort();
            if (objUrl) { URL.revokeObjectURL(objUrl); objUrl = null; }
        };
        const restore = () => selects.forEach((el) => { el.value = snapshot[el.name]; });

        document.getElementById('rank-card-customize').addEventListener('click', () => {
            snapshot = cardStyle();
            cardLive.src = cardImg.src;
            cardStatus.textContent = 'Your saved card.';
            cardDialog.showModal();
        });
        document.getElementById('rank-card-cancel').addEventListener('click', () => { restore(); cleanup(); cardDialog.close(); });
        // Escape fires `cancel` and then closes the dialog itself.
        cardDialog.addEventListener('cancel', () => { restore(); cleanup(); });

        const saveBtn = document.getElementById('rank-card-save');
        saveBtn.addEventListener('click', async () => {
            saveBtn.disabled = true;
            try {
                const res = await fetch(`/dashboard/guild/${GUILD_ID}/me/rank-card`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(cardStyle()),
                });
                const data = await res.json();
                if (!data.ok) throw new Error(data.error || 'Could not save the rank card.');
                snapshot = cardStyle();
                cleanup();
                cardDialog.close();
                cardImg.src = `/dashboard/guild/${GUILD_ID}/me/rank-card.png?${new URLSearchParams({ ...cardStyle(), t: Date.now() })}`;
                showToast('success', 'Rank card saved.');
            } catch (e) {
                showToast('error', e.message || 'Could not save the rank card.');
            } finally {
                saveBtn.disabled = false;
            }
        });
    }

    // -- Reminders -----------------------------------------------------------------
    document.querySelectorAll('.reminder-cancel-btn').forEach((btn) => btn.addEventListener('click', async () => {
        const item = btn.closest('.reminder-item');
        const text = item.querySelector('.reminder-message').textContent.trim();
        if (!(await PercyConfirm.ask({
            title: 'Cancel this reminder?',
            consequence: `Percy won't remind you about "${text}"${item.querySelector('.reminder-meta').textContent.includes('repeats') ? ', and it stops repeating' : ''}.`,
            undo: null,
            confirmLabel: 'Cancel reminder',
        }))) return;
        const res = await fetch(`/dashboard/guild/${GUILD_ID}/me/reminders/${btn.dataset.id}/delete`, {
            method: 'POST', headers: { 'Accept': 'application/json' },
        }).then((r) => r.json()).catch(() => ({ ok: false, error: 'Network error.' }));
        if (res.ok) { item.remove(); showToast('success', 'Reminder cancelled.'); }
        else showToast('error', res.error || 'Could not cancel the reminder.');
    }));

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

})();
