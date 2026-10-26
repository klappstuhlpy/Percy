/* Percy Dashboard — channel and role pickers that say whether Percy can use what you pick.

   Selects opt in with data-needs="post" (Percy must post there) or data-needs="assign"
   (Percy must give the role out). One request to pickers.json marks each option Percy can't
   use and shows a warning under the select while such an option is chosen — before saving,
   instead of a silent failure in Discord later. Any select with many options also gets a
   filter box. A multi-select becomes a pill picker: a search box whose dropdown adds picks
   as removable pills. Without JS the plain selects work exactly as before. */

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

    // The select stays in the form as the source of truth (FormData, PercyUnsaved, page code
    // reading selectedOptions all keep working); this UI only toggles option.selected.
    let pickerSeq = 0;
    function pillPicker(select) {
        if (select.dataset.pills) return;
        select.dataset.pills = '1';
        const id = `mp-${++pickerSeq}`;
        const label = select.id && document.querySelector(`label[for="${select.id}"]`);
        const noun = select.dataset.placeholder
            || ([...select.options].some((o) => o.textContent.trim().startsWith('#')) ? 'Add a channel…' : 'Add…');

        const wrap = document.createElement('div');
        wrap.className = 'multi-picker';
        wrap.innerHTML = `<div class="multi-picker-box"><input type="text" class="multi-picker-input" role="combobox"
            aria-autocomplete="list" aria-expanded="false" aria-controls="${id}" autocomplete="off"></div>
            <ul class="multi-picker-list" id="${id}" role="listbox" hidden></ul>`;
        const box = wrap.firstElementChild;
        const input = box.querySelector('input');
        const list = wrap.querySelector('ul');
        input.placeholder = noun;
        if (label) {
            label.removeAttribute('for');
            label.id ||= `${id}-label`;
            input.setAttribute('aria-labelledby', label.id);
            label.addEventListener('click', () => input.focus());
        } else if (select.hasAttribute('aria-label')) {
            input.setAttribute('aria-label', select.getAttribute('aria-label'));
        }
        select.hidden = true;
        select.insertAdjacentElement('afterend', wrap);

        let active = -1;
        const text = (opt) => opt.textContent.trim();
        const commit = () => select.dispatchEvent(new Event('change', { bubbles: true }));

        function drawPills() {
            box.querySelectorAll('.multi-pill').forEach((p) => p.remove());
            [...select.selectedOptions].forEach((opt) => {
                const pill = document.createElement('span');
                pill.className = 'multi-pill';
                pill.textContent = text(opt);
                const x = document.createElement('button');
                x.type = 'button';
                x.className = 'multi-pill-remove';
                x.setAttribute('aria-label', `Remove ${text(opt)}`);
                x.textContent = '×';
                x.addEventListener('click', (e) => { e.stopPropagation(); opt.selected = false; commit(); input.focus(); });
                pill.appendChild(x);
                box.insertBefore(pill, input);
            });
            input.placeholder = select.selectedOptions.length ? '' : noun;
        }

        function drawList() {
            const q = input.value.trim().toLowerCase();
            const matches = [...select.options].filter((o) => o.value && !o.selected && !o.disabled
                && (!q || text(o).toLowerCase().includes(q)));
            list.replaceChildren(...matches.map((opt, i) => {
                const li = document.createElement('li');
                li.id = `${id}-${i}`;
                li.setAttribute('role', 'option');
                li.className = 'multi-picker-option';
                li.textContent = text(opt);
                li.addEventListener('mousedown', (e) => e.preventDefault()); // keep focus in the input
                li.addEventListener('click', () => pick(opt));
                li._opt = opt;
                return li;
            }));
            if (!matches.length) {
                const li = document.createElement('li');
                li.className = 'multi-picker-empty';
                li.textContent = q ? 'No matches' : 'Everything is selected';
                list.appendChild(li);
            }
            active = matches.length ? Math.min(Math.max(active, 0), matches.length - 1) : -1;
            highlight();
        }

        function highlight() {
            const items = list.querySelectorAll('.multi-picker-option');
            items.forEach((li, i) => li.classList.toggle('active', i === active));
            if (items[active]) {
                input.setAttribute('aria-activedescendant', items[active].id);
                items[active].scrollIntoView({ block: 'nearest' });
            } else {
                input.removeAttribute('aria-activedescendant');
            }
        }

        function open() { list.hidden = false; input.setAttribute('aria-expanded', 'true'); drawList(); }
        function close() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; }

        function pick(opt) {
            opt.selected = true;
            input.value = '';
            commit();
            drawList();
        }

        box.addEventListener('click', () => input.focus());
        input.addEventListener('focus', open);
        input.addEventListener('input', () => { active = 0; open(); });
        input.addEventListener('blur', close);
        input.addEventListener('keydown', (e) => {
            const items = list.querySelectorAll('.multi-picker-option');
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                if (list.hidden) open();
                const n = items.length;
                if (n) { active = (active + (e.key === 'ArrowDown' ? 1 : -1) + n) % n; highlight(); }
            } else if (e.key === 'Enter') {
                if (!list.hidden && items[active]) { e.preventDefault(); pick(items[active]._opt); }
            } else if (e.key === 'Escape' && !list.hidden) {
                e.preventDefault();
                e.stopPropagation();
                close();
            } else if (e.key === 'Backspace' && !input.value && select.selectedOptions.length) {
                const last = select.selectedOptions[select.selectedOptions.length - 1];
                last.selected = false;
                commit();
            }
        });
        // Redraw on every change, including PercyUnsaved's discard writing the select back.
        select.addEventListener('change', () => { drawPills(); if (!list.hidden) drawList(); });
        drawPills();
    }

    document.querySelectorAll('select[multiple]').forEach(pillPicker);
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
