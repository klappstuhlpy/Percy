/* Percy Dashboard — channel and role pickers that say whether Percy can use what you pick.

   Selects opt in with data-needs="post" (Percy must post there) or data-needs="assign"
   (Percy must give the role out). One request to pickers.json marks each option Percy can't
   use and shows a warning under the select while such an option is chosen — before saving,
   instead of a silent failure in Discord later. Any select with many options also gets a
   filter box. Without JS the plain selects work exactly as before. */

(function () {
    'use strict';

    const guildId = window.PERCY_DASH && window.PERCY_DASH.guildId;
    const FILTER_MIN_OPTIONS = 15;

    const NOTE = {
        post: "Percy can't post here: it needs View Channel, Send Messages and Embed Links in this channel.",
        assign: "Percy can't give out this role: move Percy's role above it in Server Settings → Roles.",
    };

    function annotate(select, usable) {
        const need = select.dataset.needs;
        [...select.options].forEach((opt) => {
            if (opt.value && usable(opt.value) === false && !opt.dataset.blocked) {
                opt.dataset.blocked = '1';
                opt.textContent += need === 'post' ? '  (Percy can’t post)' : '  (Percy can’t assign)';
            }
        });
        const note = document.createElement('p');
        note.className = 'picker-warning';
        note.setAttribute('role', 'status');
        select.insertAdjacentElement('afterend', note);
        const sync = () => {
            const chosen = select.selectedOptions[0];
            note.textContent = chosen && chosen.dataset.blocked ? NOTE[need] : '';
            note.hidden = !note.textContent;
        };
        select.addEventListener('change', sync);
        sync();
    }

    function addFilter(select) {
        if (select.multiple || select.options.length < FILTER_MIN_OPTIONS || select.dataset.filtered) return;
        select.dataset.filtered = '1';
        const input = document.createElement('input');
        input.type = 'search';
        input.className = 'config-input picker-filter';
        input.placeholder = 'Filter…';
        input.setAttribute('aria-label', 'Filter the options below');
        select.insertAdjacentElement('beforebegin', input);
        input.addEventListener('input', () => {
            const q = input.value.trim().toLowerCase();
            [...select.options].forEach((opt) => {
                opt.hidden = !!q && !!opt.value && !opt.selected && !opt.textContent.toLowerCase().includes(q);
            });
        });
    }

    document.querySelectorAll('select.config-select, select.config-input').forEach(addFilter);

    const targets = document.querySelectorAll('select[data-needs]');
    if (!guildId || !targets.length) return;
    fetch(`/dashboard/guild/${guildId}/pickers.json`, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
            if (!data) return;
            targets.forEach((select) => annotate(select, (id) => {
                const entry = select.dataset.needs === 'post' ? data.channels[id] : data.roles[id];
                return entry ? (entry.can_post ?? entry.assignable) : undefined;
            }));
        })
        .catch(() => { /* annotations are advisory; the selects still work */ });
})();
