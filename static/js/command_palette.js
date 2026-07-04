/* Percy Dashboard — command palette (Ctrl/Cmd+K).
   A single search surface over three result kinds:
     • Navigation — the dashboard tabs (scraped from the sidebar nav, so it never drifts).
     • Actions    — global shortcuts (theme, servers, docs, support).
     • Ask Percy   — natural-language questions answered by Percy's AI assistant, proxied through
                     the dashboard BFF (POST /dashboard/guild/<id>/ai/ask).
   Loaded on every dashboard page (guild config + every feature tab). Expects
   window.PERCY_DASH = { guildId, guildName } and window.showToast (percy_common.js). */

(function () {
    var dash = window.PERCY_DASH || {};
    var GUILD_ID = dash.guildId || null;
    if (!GUILD_ID) return; // not a guild-scoped page (window.PERCY_DASH is set on every dashboard page)

    function toast(level, msg) { (window.showToast || function () {})(level, msg); }

    // -- Item sources ----------------------------------------------------------

    // Dashboard tabs, read live from the sidebar nav so this list can never go stale.
    function navItems() {
        var items = [];
        document.querySelectorAll('.dash-nav .dash-nav-link').forEach(function (a) {
            var label = (a.textContent || '').trim();
            var href = a.getAttribute('href');
            if (!label || !href) return;
            var group = '';
            var node = a.previousElementSibling;
            while (node) {
                if (node.classList && node.classList.contains('dash-nav-heading')) { group = node.textContent.trim(); break; }
                node = node.previousElementSibling;
            }
            items.push({ kind: 'nav', label: label, sub: group || 'Section', href: href, icon: '#' });
        });
        return items;
    }

    // Global actions available from anywhere in the dashboard.
    function actionItems() {
        return [
            { kind: 'action', label: 'Toggle light / dark theme', sub: 'Appearance', icon: '◐', run: toggleTheme },
            { kind: 'action', label: 'All servers', sub: 'Navigate', icon: '⊞', href: '/dashboard' },
            { kind: 'action', label: 'Configure AI features', sub: 'AI', icon: '✦', href: '/dashboard/guild/' + GUILD_ID + '#ai' },
            { kind: 'action', label: 'Documentation', sub: 'Help', icon: '?', href: '/docs/' },
            { kind: 'action', label: 'Ask Percy: how do I set up leveling?', sub: 'Suggested', icon: '✦', ask: 'How do I set up leveling?' },
            { kind: 'action', label: 'Ask Percy: how do I enable the economy?', sub: 'Suggested', icon: '✦', ask: 'How do I enable the economy?' },
        ];
    }

    function toggleTheme() {
        var btn = document.getElementById('theme-toggle');
        if (btn) { btn.click(); return; }
        var root = document.documentElement;
        root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
        try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {}
    }

    // -- Fuzzy match (subsequence, with a small contiguous-substring bonus) -----
    function score(query, text) {
        if (!query) return 0;
        var q = query.toLowerCase(), t = text.toLowerCase();
        var idx = t.indexOf(q);
        if (idx !== -1) return 1000 - idx; // contiguous substring ranks highest
        // subsequence fallback
        var qi = 0;
        for (var ti = 0; ti < t.length && qi < q.length; ti++) {
            if (t[ti] === q[qi]) qi++;
        }
        return qi === q.length ? 100 - (t.length - q.length) : -1;
    }

    // -- DOM -------------------------------------------------------------------

    var overlay, input, list, statusBar, panel;
    var results = [];      // currently rendered result items
    var selected = 0;      // index into `results`
    var navCache = [], actionCache = [];

    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function build() {
        overlay = document.createElement('div');
        overlay.className = 'cmdk-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'Dashboard search');
        overlay.hidden = true;
        overlay.innerHTML =
            '<div class="cmdk-panel" role="document">' +
            '  <div class="cmdk-input-row">' +
            '    <span class="cmdk-input-glyph" aria-hidden="true">⌕</span>' +
            '    <input class="cmdk-input" type="text" autocomplete="off" spellcheck="false" ' +
            '           placeholder="Search tabs, actions, or ask Percy a question…" aria-label="Search" />' +
            '    <kbd class="cmdk-esc">esc</kbd>' +
            '  </div>' +
            '  <div class="cmdk-results" role="listbox"></div>' +
            '  <div class="cmdk-status"><span class="cmdk-hint"><kbd>↑</kbd><kbd>↓</kbd> navigate <kbd>↵</kbd> select</span>' +
            '       <span class="cmdk-brand">Percy</span></div>' +
            '</div>';
        document.body.appendChild(overlay);

        panel = overlay.querySelector('.cmdk-panel');
        input = overlay.querySelector('.cmdk-input');
        list = overlay.querySelector('.cmdk-results');
        statusBar = overlay.querySelector('.cmdk-status');

        overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
        input.addEventListener('input', function () { render(input.value.trim()); });
        input.addEventListener('keydown', onKeydown);
    }

    function render(query) {
        results = [];
        var pool = navCache.concat(actionCache);
        if (query) {
            var scored = [];
            pool.forEach(function (it) {
                var s = Math.max(score(query, it.label), score(query, it.sub) - 50);
                if (s > -1) scored.push({ it: it, s: s });
            });
            scored.sort(function (a, b) { return b.s - a.s; });
            results = scored.map(function (x) { return x.it; });
            // Always offer to ask the AI with the typed text.
            results.push({ kind: 'ask', label: 'Ask Percy: “' + query + '”', sub: 'AI assistant', icon: '✦', ask: query });
        } else {
            results = pool.slice();
        }

        list.innerHTML = '';
        if (!results.length) {
            list.innerHTML = '<div class="cmdk-empty">No matches.</div>';
            return;
        }

        var lastGroup = null;
        results.forEach(function (it, i) {
            var groupLabel = it.kind === 'nav' ? 'Navigation' : it.kind === 'ask' ? 'Ask Percy' : 'Actions';
            if (groupLabel !== lastGroup) {
                lastGroup = groupLabel;
                var h = document.createElement('div');
                h.className = 'cmdk-group';
                h.textContent = groupLabel;
                list.appendChild(h);
            }
            var row = document.createElement('div');
            row.className = 'cmdk-item';
            row.setAttribute('role', 'option');
            row.dataset.index = i;
            row.innerHTML =
                '<span class="cmdk-item-icon" aria-hidden="true">' + esc(it.icon || '›') + '</span>' +
                '<span class="cmdk-item-label">' + esc(it.label) + '</span>' +
                '<span class="cmdk-item-sub">' + esc(it.sub || '') + '</span>';
            row.addEventListener('mousemove', function () { setSelected(i); });
            row.addEventListener('click', function () { activate(i); });
            list.appendChild(row);
        });
        selected = 0;
        highlight();
    }

    function rows() { return list.querySelectorAll('.cmdk-item'); }

    function setSelected(i) { selected = i; highlight(); }

    function highlight() {
        var rs = rows();
        rs.forEach(function (r) {
            var on = Number(r.dataset.index) === selected;
            r.classList.toggle('selected', on);
            r.setAttribute('aria-selected', on ? 'true' : 'false');
            if (on) r.scrollIntoView({ block: 'nearest' });
        });
    }

    function move(delta) {
        if (!results.length) return;
        selected = (selected + delta + results.length) % results.length;
        highlight();
    }

    function onKeydown(e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
        else if (e.key === 'Enter') { e.preventDefault(); activate(selected); }
        else if (e.key === 'Escape') { e.preventDefault(); close(); }
    }

    function activate(i) {
        var it = results[i];
        if (!it) return;
        if (it.ask) { askAI(it.ask); return; }
        if (it.run) { close(); it.run(); return; }
        if (it.href) { window.location.href = it.href; }
    }

    // -- AI answer view --------------------------------------------------------

    // Minimal, safe markdown: escape first, then re-introduce a tiny allow-list.
    // `**bold**` → <strong> (linked to a tab when the text matches a tab label),
    // `` `code` `` → <code>, blank lines → paragraphs.
    function renderAnswer(text) {
        var navByLabel = {};
        navCache.forEach(function (n) { navByLabel[n.label.toLowerCase()] = n.href; });

        return text.split(/\n{2,}/).map(function (para) {
            var html = esc(para)
                .replace(/`([^`]+)`/g, '<code>$1</code>')
                .replace(/\*\*([^*]+)\*\*/g, function (_m, inner) {
                    var href = navByLabel[inner.toLowerCase()];
                    return href
                        ? '<a class="cmdk-answer-link" href="' + esc(href) + '">' + inner + '</a>'
                        : '<strong>' + inner + '</strong>';
                })
                .replace(/\n/g, '<br>');
            return '<p>' + html + '</p>';
        }).join('');
    }

    function showAnswerShell(question) {
        list.innerHTML =
            '<div class="cmdk-answer">' +
            '  <div class="cmdk-answer-q"><span class="cmdk-item-icon" aria-hidden="true">✦</span>' + esc(question) + '</div>' +
            '  <div class="cmdk-answer-body"><span class="cmdk-spinner" aria-hidden="true"></span> Percy is thinking…</div>' +
            '</div>';
    }

    function askAI(question) {
        results = [];
        showAnswerShell(question);
        statusBar.querySelector('.cmdk-hint').innerHTML = '<kbd>esc</kbd> back to search';

        fetch('/dashboard/guild/' + GUILD_ID + '/ai/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ question: question }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var body = list.querySelector('.cmdk-answer-body');
                if (!body) return;
                if (!data.ok) { body.textContent = data.error || 'Something went wrong.'; return; }
                if (!data.available) {
                    body.innerHTML = '<p>' + esc(data.reason || 'The AI assistant is unavailable.') + '</p>' +
                        '<a class="cmdk-answer-link" href="/dashboard/guild/' + GUILD_ID + '#ai">Open AI settings →</a>';
                    return;
                }
                var html = renderAnswer(data.answer || 'No answer.');
                if (data.suggestions && data.suggestions.length) {
                    html += '<div class="cmdk-chips" aria-label="Related commands">';
                    data.suggestions.forEach(function (s) {
                        html += '<button type="button" class="cmdk-chip" data-cmd="' + esc(s.command) + '">' + esc(s.label) + '</button>';
                    });
                    html += '</div>';
                }
                body.innerHTML = html;
                body.querySelectorAll('.cmdk-chip').forEach(function (chip) {
                    chip.addEventListener('click', function () {
                        var cmd = chip.getAttribute('data-cmd');
                        if (navigator.clipboard) {
                            navigator.clipboard.writeText(cmd).then(function () { toast('success', 'Copied “' + cmd + '”'); });
                        } else {
                            toast('info', cmd);
                        }
                    });
                });
            })
            .catch(function () {
                var body = list.querySelector('.cmdk-answer-body');
                if (body) body.textContent = 'Network error — please try again.';
            });
    }

    // -- Open / close ----------------------------------------------------------

    function open() {
        if (!overlay) build();
        navCache = navItems();
        actionCache = actionItems();
        overlay.hidden = false;
        document.body.classList.add('cmdk-open');
        statusBar.querySelector('.cmdk-hint').innerHTML = '<kbd>↑</kbd><kbd>↓</kbd> navigate <kbd>↵</kbd> select';
        input.value = '';
        render('');
        setTimeout(function () { input.focus(); }, 0);
    }

    function close() {
        if (!overlay || overlay.hidden) return;
        overlay.hidden = true;
        document.body.classList.remove('cmdk-open');
    }

    function isOpen() { return overlay && !overlay.hidden; }

    // -- Triggers --------------------------------------------------------------

    document.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
            e.preventDefault();
            isOpen() ? close() : open();
        }
    });

    // A discoverable launcher in the sidebar, injected so no template needs to know about it.
    function injectTrigger() {
        var top = document.querySelector('.dash-sidebar-top');
        if (!top || document.getElementById('cmdk-trigger')) return;
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.id = 'cmdk-trigger';
        btn.className = 'cmdk-trigger';
        btn.innerHTML = '<span class="cmdk-trigger-glyph" aria-hidden="true">⌕</span>' +
            '<span class="cmdk-trigger-text">Search</span>' +
            '<kbd class="cmdk-trigger-kbd">⌘K</kbd>';
        btn.addEventListener('click', open);
        top.appendChild(btn);
    }

    if (document.readyState !== 'loading') injectTrigger();
    else document.addEventListener('DOMContentLoaded', injectTrigger);
})();
