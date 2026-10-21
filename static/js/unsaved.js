/* Percy Dashboard — the global unsaved-changes tracker.

   Settings cards do not carry their own "Save" button. Instead every card that holds
   savable state registers a section here; the tracker watches it for edits and raises a
   single floating banner that saves — or discards — every dirty section at once.

   Two ways to register:

     PercyUnsaved.watchForm(form, { label, save?, beforeSave?, reload? })
         For a plain <form>. Snapshot/restore/submit are derived from the form itself;
         the default save POSTs the form to its own action, like a normal submit would.

     PercyUnsaved.register({ label, element, read, write, save, reload? })
         For a card that is not a form (a checkbox grid, an image editor, …).
         `read()` returns a JSON-able snapshot, `write(state)` restores one, and
         `save(state)` persists it and resolves to { ok, error?, message? }.

   Both return a handle: { markClean(), refresh(), remove() }. `markClean()` re-baselines
   the section (call it after a save the tracker did not perform itself).

   Loaded on every dashboard page, before the page's own script. */

(function() {
    'use strict';

    const sections = [];

    let banner = null;
    let bannerText = null;
    let saveBtn = null;
    let discardBtn = null;
    let saving = false;

    // -- Banner ----------------------------------------------------------------

    function ensureBanner() {
        if (banner) return banner;
        banner = document.createElement('div');
        banner.className = 'unsaved-banner';
        banner.setAttribute('role', 'status');
        banner.innerHTML = `
            <span class="unsaved-banner-text">You have unsaved changes</span>
            <div class="unsaved-banner-actions">
                <button type="button" class="button small" data-unsaved="discard">Discard</button>
                <button type="button" class="button primary small" data-unsaved="save">Save Changes</button>
            </div>
        `;
        document.body.appendChild(banner);

        bannerText = banner.querySelector('.unsaved-banner-text');
        saveBtn = banner.querySelector('[data-unsaved="save"]');
        discardBtn = banner.querySelector('[data-unsaved="discard"]');

        saveBtn.addEventListener('click', saveAll);
        discardBtn.addEventListener('click', discardAll);
        return banner;
    }

    function dirtySections() {
        return sections.filter(section => section.isDirty());
    }

    function describe(dirty) {
        if (dirty.length === 1) return `Unsaved changes in ${dirty[0].label}`;
        return `Unsaved changes in ${dirty.length} sections`;
    }

    function refresh() {
        const dirty = dirtySections();
        if (!dirty.length) {
            if (banner) banner.classList.remove('visible');
            return;
        }
        ensureBanner();
        bannerText.textContent = describe(dirty);
        requestAnimationFrame(() => banner.classList.add('visible'));
    }

    function hide() {
        if (banner) banner.classList.remove('visible');
    }

    function setBusy(busy) {
        saving = busy;
        if (!banner) return;
        saveBtn.disabled = busy;
        discardBtn.disabled = busy;
        saveBtn.textContent = busy ? 'Saving…' : 'Save Changes';
    }

    // -- Save / discard --------------------------------------------------------

    async function saveAll() {
        if (saving) return;
        const dirty = dirtySections();
        if (!dirty.length) return;

        setBusy(true);
        let reload = false;
        const warnings = [];
        // Baselines before they are replaced: what Undo restores.
        const previous = dirty.map(section => section.baseline);
        try {
            for (const section of dirty) {
                if (section.beforeSave && !(await section.beforeSave())) return;

                let result;
                try {
                    result = await section.save(section.read());
                } catch {
                    showToast('error', `Network error while saving ${section.label}.`);
                    return;
                }
                if (!result || result.ok === false) {
                    showToast('error', (result && result.error) || `Failed to save ${section.label}.`);
                    return;
                }
                section.baseline = snapshot(section);
                reload = reload || section.reload;
                // Saved, but Percy has advice (e.g. a channel still visible to @everyone): show it and
                // skip the reload, which would wipe the toast before it can be read.
                if (result.warning) warnings.push(result.warning);
            }
            const saved = dirty.length === 1 ? 'Settings saved.' : `Saved ${dirty.length} sections.`;
            // Toasts stack on one spot, so the advice rides in the same toast as the confirmation.
            if (warnings.length) showToast('warning', `${saved} ${warnings.join(' ')}`);
            // No Undo when a section reloads the page (the reload would wipe the toast before it
            // could be used) or when there is advice to read in the same toast.
            else if (!reload) {
                showToast('success', saved, { action: { label: 'Undo', onClick: () => undo(dirty, previous) } });
            } else showToast('success', saved);
            if (warnings.length) reload = false;
            refresh();
            // Some sections change what the page renders (feature flags gate whole cards),
            // so they ask for a reload once everything is safely persisted.
            if (reload) setTimeout(() => location.reload(), 400);
        } finally {
            setBusy(false);
        }
    }

    // Write each section's pre-save baseline back and save it through the normal path. On
    // failure the form keeps the restored values, so it is dirty again and the banner offers
    // a retry or a discard.
    async function undo(sectionsToUndo, baselines) {
        if (saving) return;
        setBusy(true);
        try {
            for (let i = 0; i < sectionsToUndo.length; i++) {
                const section = sectionsToUndo[i];
                const state = JSON.parse(baselines[i]);
                section.write(state);
                let result;
                try {
                    result = await section.save(state);
                } catch {
                    showToast('error', `Network error while undoing ${section.label}.`);
                    return;
                }
                if (!result || result.ok === false) {
                    showToast('error', (result && result.error) || `Failed to undo ${section.label}.`);
                    return;
                }
                section.baseline = snapshot(section);
            }
            showToast('success', 'Change undone.');
        } finally {
            setBusy(false);
            refresh();
        }
    }

    function discardAll() {
        if (saving) return;
        const dirty = dirtySections();
        if (!dirty.length) return;
        dirty.forEach(section => section.write(JSON.parse(section.baseline)));
        hide();
        showToast('success', 'Changes discarded.');
    }

    // Baselines are stored serialized: it makes the dirty check a string compare and keeps
    // the snapshot immune to later mutation of the object the section handed back.
    function snapshot(section) {
        return JSON.stringify(section.read());
    }

    // -- Registration ----------------------------------------------------------

    function register(options) {
        const section = {
            label: options.label || 'this section',
            read: options.read,
            write: options.write,
            save: options.save,
            beforeSave: options.beforeSave || null,
            reload: !!options.reload,
            baseline: null,
        };
        section.baseline = snapshot(section);
        section.isDirty = () => snapshot(section) !== section.baseline;

        sections.push(section);

        if (options.element) {
            options.element.addEventListener('input', refresh);
            options.element.addEventListener('change', refresh);
        }

        return {
            markClean() {
                section.baseline = snapshot(section);
                refresh();
            },
            refresh,
            remove() {
                const i = sections.indexOf(section);
                if (i !== -1) sections.splice(i, 1);
                refresh();
            },
        };
    }

    // A form field is savable state unless it is hidden plumbing (the `_section`
    // discriminator the config endpoint routes on) — those never change, so tracking
    // them would only add noise to the snapshot.
    function fields(form) {
        return Array.from(form.elements).filter(el => el.name && el.type !== 'hidden');
    }

    function readForm(form) {
        const state = {};
        for (const el of fields(form)) {
            state[el.name] = el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value;
        }
        return state;
    }

    function writeForm(form, state) {
        for (const el of fields(form)) {
            const value = state[el.name];
            if (el.type === 'checkbox' || el.type === 'radio') el.checked = !!value;
            else el.value = value !== undefined ? value : '';
        }
        // Fields whose visibility depends on another field (the leveling channel picker)
        // re-sync off the same change event a user edit would fire.
        form.dispatchEvent(new Event('change', { bubbles: true }));
    }

    async function submitForm(form) {
        const resp = await fetch(form.action, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: new URLSearchParams(new FormData(form)),
        });
        return resp.json();
    }

    function watchForm(form, options) {
        options = options || {};
        const handle = register({
            label: options.label || form.dataset.section || 'this section',
            element: form,
            reload: options.reload,
            beforeSave: options.beforeSave,
            read: () => readForm(form),
            write: state => writeForm(form, state),
            save: options.save ? () => options.save(form) : () => submitForm(form),
        });

        // A card with no Save button can still be submitted with Enter in a text field —
        // route that through the tracker so it behaves exactly like the banner.
        form.addEventListener('submit', e => {
            e.preventDefault();
            saveAll();
        });

        return handle;
    }

    // -- Guards ----------------------------------------------------------------

    window.addEventListener('beforeunload', e => {
        if (!dirtySections().length) return;
        e.preventDefault();
        e.returnValue = '';
    });

    document.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 's' && dirtySections().length) {
            e.preventDefault();
            saveAll();
        }
    });

    window.PercyUnsaved = { register, watchForm, refresh, saveAll, discardAll };
})();
