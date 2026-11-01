// /commands: client-side search, category + type filters, expand/collapse, copy buttons, deep links.
// Everything here is progressive: with JS off the page is the full list of <details> rows.
(function () {
    'use strict';
    const page = document.querySelector('.cmd-page');
    if (!page) return;
    page.classList.add('js');

    const input = document.getElementById('cmd-search');
    const select = document.getElementById('cmd-category-select');
    const none = document.getElementById('cmd-none');
    const sidebarLinks = Array.from(document.querySelectorAll('.cmd-sidebar a'));
    const typeButtons = Array.from(document.querySelectorAll('.cmd-type'));
    const sections = Array.from(document.querySelectorAll('.cmd-category'));
    const state = { q: '', type: 'all', cat: '' };

    function typeOk(el) {
        const slash = el.dataset.slash === 'true';
        return !(state.type === 'slash' && !slash) && !(state.type === 'prefix' && slash);
    }
    function passes(el) {
        return typeOk(el) && (!state.q || el.dataset.search.includes(state.q));
    }

    function apply() {
        let shown = 0;
        sections.forEach(function (sec) {
            let visible = 0;
            const inCat = !state.cat || sec.dataset.category === state.cat;
            sec.querySelectorAll(':scope > .cmd').forEach(function (top) {
                const subs = Array.from(top.querySelectorAll('.cmd.sub'));
                const topOk = passes(top);
                let anySub = false;
                subs.forEach(function (sub) {
                    // A group that matches the search keeps all its subcommands (type filter still applies).
                    const ok = passes(sub) || (topOk && typeOk(sub));
                    sub.hidden = !ok;
                    anySub = anySub || ok;
                });
                const ok = inCat && (topOk || anySub);
                top.hidden = !ok;
                if (ok) visible++;
                // Searching for a subcommand opens its group so the hit is visible.
                if (ok && state.q && anySub && !topOk) top.open = true;
            });
            sec.hidden = !visible;
            shown += visible;
        });
        if (none) none.hidden = shown > 0;
    }

    function setCategory(cat) {
        state.cat = cat;
        if (select) select.value = cat;
        sidebarLinks.forEach(function (a) { a.classList.toggle('active', a.dataset.cat === cat); });
        apply();
    }

    if (input) input.addEventListener('input', function () { state.q = input.value.trim().toLowerCase(); apply(); });
    typeButtons.forEach(function (b) {
        b.addEventListener('click', function () {
            state.type = b.dataset.type;
            typeButtons.forEach(function (o) { o.classList.toggle('active', o === b); });
            apply();
        });
    });
    sidebarLinks.forEach(function (a) {
        a.addEventListener('click', function (e) { e.preventDefault(); setCategory(a.dataset.cat); });
    });
    if (select) select.addEventListener('change', function () { setCategory(select.value); });

    // Expand / collapse all
    const bulk = document.querySelector('.cmd-bulk');
    if (bulk) bulk.hidden = false;
    function setAll(open) {
        document.querySelectorAll('.cmd:not([hidden])').forEach(function (d) { d.open = open; });
    }
    const expand = document.getElementById('cmd-expand');
    const collapse = document.getElementById('cmd-collapse');
    if (expand) expand.addEventListener('click', function () { setAll(true); });
    if (collapse) collapse.addEventListener('click', function () { setAll(false); });

    // Copy buttons
    document.querySelectorAll('.cmd-copy').forEach(function (btn) {
        btn.hidden = false;
        btn.addEventListener('click', function () {
            const code = btn.parentElement.querySelector('code');
            if (!code || !navigator.clipboard) return;
            navigator.clipboard.writeText(code.textContent).then(function () {
                const old = btn.textContent;
                btn.textContent = 'Copied';
                setTimeout(function () { btn.textContent = old; }, 1200);
            }).catch(function () { /* clipboard blocked: leave the text selectable */ });
        });
    });

    // Deep links: /commands#economy-shop-add opens the row (and its parent group) and scrolls to it.
    function openHash() {
        const id = decodeURIComponent(location.hash.slice(1));
        if (!id) return;
        const el = document.getElementById(id);
        if (!el) return;
        if (el.classList.contains('cmd-category')) { setCategory(el.dataset.category); }
        if (el.tagName === 'DETAILS') {
            // Reset filters that would hide the target.
            if (state.cat || state.q || state.type !== 'all') {
                state.q = ''; state.type = 'all';
                if (input) input.value = '';
                typeButtons.forEach(function (o) { o.classList.toggle('active', o.dataset.type === 'all'); });
                setCategory('');
            }
            for (let d = el; d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
            el.scrollIntoView({ block: 'start' });
        }
    }
    window.addEventListener('hashchange', openHash);
    openHash();
})();
