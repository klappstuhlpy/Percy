/* Percy Dashboard — shared helpers loaded on every dashboard page.
   Provides the global toast notifier and the nav "Features"/"Browse"
   dropdown behaviour that every page's header relies on. */

(function() {
    // -- Toast notifications ---------------------------------------------------
    // Single shared implementation; page scripts call window.showToast(...).
    if (typeof window.showToast === 'undefined') {
        // options.action = { label, onClick } adds a button (e.g. Undo); clicking it runs
        // onClick and dismisses the toast. A toast with an action stays 8 s.
        window.showToast = function(level, message, options) {
            const action = options && options.action;
            // No action button: the shared PercyToast stack (toast.js) renders it.
            if (!action && window.PercyToast) {
                window.PercyToast.show(level, message);
                return;
            }
            const toast = document.createElement('div');
            toast.className = 'toast toast-' + level;
            toast.textContent = message;
            let timer;
            const dismiss = () => {
                clearTimeout(timer);
                toast.classList.remove('visible');
                setTimeout(() => toast.remove(), 300);
            };
            if (action) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'toast-action';
                btn.textContent = action.label;
                btn.addEventListener('click', () => {
                    btn.disabled = true; // no second fire during the fade-out
                    dismiss();
                    action.onClick();
                });
                toast.appendChild(btn);
            }
            document.body.appendChild(toast);
            requestAnimationFrame(() => toast.classList.add('visible'));
            // advice needs reading time, and so does a chance to undo
            timer = setTimeout(dismiss, level === 'warning' || action ? 8000 : 3000);
        };
    }
})();

/* PercyLive — one server-sent event stream per page (GET /dashboard/guild/:id/events).
   PercyLive.connect(guildId, topics, onEvent, onState) -> { close() }
     topics   comma list ('music', 'case', 'stats'); onEvent receives {type, data}.
     onState  optional fn(live): true while the stream is open, false when it is not. A page
              keeps (or resumes) its old polling whenever live is false, so an unsupported
              browser, a proxy that buffers, or an outage degrades to polling and never breaks.
   The stream is closed while the tab is hidden and reopened on return. After three failures
   in a row without a single open (or a refusal such as 403) it gives up for good. */
window.PercyLive = (function() {
    const MAX_FAILS = 3;
    function connect(guildId, topics, onEvent, onState) {
        const state = typeof onState === 'function' ? onState : () => {};
        if (typeof window.EventSource === 'undefined' || !guildId) { state(false); return { close() {} }; }
        const url = `/dashboard/guild/${guildId}/events?topics=${encodeURIComponent(topics)}`;
        let es = null, fails = 0, gaveUp = false;

        function close() {
            if (!es) return;
            es.close();
            es = null;
            state(false);
        }
        function open() {
            if (es || gaveUp) return;
            es = new EventSource(url);
            es.onopen = () => { fails = 0; state(true); };
            es.onmessage = (m) => {
                let ev;
                try { ev = JSON.parse(m.data); } catch (_) { return; }
                if (ev && ev.type) onEvent(ev);
            };
            es.onerror = () => {
                state(false);
                fails += 1;
                // EventSource retries by itself while CONNECTING; a CLOSED one (403, 404) will not.
                if (es && (es.readyState === 2 || fails >= MAX_FAILS)) {
                    es.close();
                    es = null;
                    gaveUp = true;
                }
            };
        }
        const onVisibility = () => { if (document.hidden) close(); else open(); };
        const onShow = () => { if (!document.hidden) open(); };
        document.addEventListener('visibilitychange', onVisibility);
        window.addEventListener('pagehide', close);
        window.addEventListener('pageshow', onShow);
        if (!document.hidden) open();
        return {
            close() {
                gaveUp = true;
                document.removeEventListener('visibilitychange', onVisibility);
                window.removeEventListener('pagehide', close);
                window.removeEventListener('pageshow', onShow);
                close();
            },
        };
    }
    return { connect };
})();
