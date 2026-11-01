/* Percy Dashboard — the guild configuration page: tab switching, and registration of
   every settings card with the global unsaved-changes tracker (unsaved.js). The cards
   themselves carry no Save button; the tracker's banner saves them all. */

(function() {
    const tabs = document.querySelectorAll('[role="tab"]');
    const panels = document.querySelectorAll('[role="tabpanel"]');

    function switchTab(tabId) {
        tabs.forEach(t => t.setAttribute('aria-selected', t.dataset.tab === tabId ? 'true' : 'false'));
        panels.forEach(p => {
            p.hidden = p.id !== 'tab-' + tabId;
        });
        history.replaceState(null, '', '#' + tabId);
    }

    tabs.forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    // On load and when an in-page link (e.g. a Setup status "Fix") changes the hash.
    function syncTabWithHash(scroll) {
        const hash = location.hash.slice(1);
        if (hash && document.getElementById('tab-' + hash)) {
            switchTab(hash);
            if (scroll === true) document.querySelector('.config-tabs')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }
    syncTabWithHash();
    window.addEventListener('hashchange', () => syncTabWithHash(true));

    // -- Quick setup: logging (an action, not a tracked edit: it saves on its own) --------
    const qs = document.getElementById('quick-setup-logging');
    if (qs) {
        const select = document.getElementById('qs-channel');
        qs.querySelectorAll('[name="qs_where"]').forEach(r => r.addEventListener('change', () => {
            select.disabled = qs.querySelector('[name="qs_where"]:checked').value !== 'existing';
        }));
        document.getElementById('qs-logging-btn').addEventListener('click', async () => {
            const logs = [...qs.querySelectorAll('[name="qs_log"]:checked')].map(c => c.value);
            const create = qs.querySelector('[name="qs_where"]:checked').value === 'create';
            if (!logs.length) { showToast('error', 'Pick at least one log.'); return; }
            if (!create && !select.value) { showToast('error', 'Pick a channel, or let Percy create one.'); return; }
            const names = [...qs.querySelectorAll('[name="qs_log"]:checked')]
                .map(c => c.closest('label').querySelector('.quick-title').textContent.trim()).join(', ');
            const where = create ? 'a new private #percy-logs channel' : select.selectedOptions[0].textContent.trim();
            if (!(await PercyConfirm.ask({
                title: 'Set up logging?',
                consequence: `${names} will go to ${where}. Any channel these logs used before is replaced.`,
                undo: 'Pick other channels below at any time.',
                confirmLabel: 'Set up logging',
            }))) return;
            const res = await fetch(`/dashboard/guild/${window.PERCY_DASH.guildId}/setup/logging`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ channel_id: create ? null : select.value, logs }),
            }).then(r => r.json()).catch(() => ({ ok: false, error: 'Network error.' }));
            showToast(res.ok ? 'success' : 'error', res.ok ? res.message : (res.error || 'Setup failed.'));
            if (res.ok) setTimeout(() => location.reload(), 900);
        });
    }

    // -- Quick setup: sentinel (an action, like the logging one above) -------------------
    const qsGk = document.getElementById('quick-setup-sentinel');
    if (qsGk) {
        const pick = (name, select) => {
            qsGk.querySelectorAll(`[name="${name}"]`).forEach(r => r.addEventListener('change', () => {
                select.disabled = qsGk.querySelector(`[name="${name}"]:checked`).value !== 'existing';
            }));
            return () => qsGk.querySelector(`[name="${name}"]:checked`).value === 'create' ? null : select;
        };
        const channelPick = pick('qs_gk_channel', document.getElementById('qs-gk-channel'));
        const rolePick = pick('qs_gk_role', document.getElementById('qs-gk-role'));
        document.getElementById('qs-sentinel-btn').addEventListener('click', async () => {
            const channel = channelPick(), role = rolePick();
            const start = document.getElementById('qs-gk-start').checked;
            if (channel && !channel.value) { showToast('error', 'Pick a channel, or let Percy create #verify.'); return; }
            if (role && !role.value) { showToast('error', 'Pick a role, or let Percy create Unverified.'); return; }
            const label = s => s.selectedOptions[0].textContent.trim();
            const where = channel ? label(channel) : 'a new #verify channel';
            const who = role ? label(role) : 'a new Unverified role';
            if (!(await PercyConfirm.ask({
                title: start ? 'Set up and start Sentinel?' : 'Set up Sentinel?',
                consequence: `New members will wait in ${where} with ${who}, which is hidden from every other channel. `
                    + 'Percy posts the captcha message there.'
                    + (start ? ' Protection starts now: every new member is held until they verify.' : ''),
                undo: 'Stop Sentinel or pick other channels and roles below at any time.',
                confirmLabel: start ? 'Set up and start' : 'Set up Sentinel',
            }))) return;
            const res = await fetch(`/dashboard/guild/${window.PERCY_DASH.guildId}/setup/sentinel`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ channel_id: channel?.value ?? null, role_id: role?.value ?? null, start }),
            }).then(r => r.json()).catch(() => ({ ok: false, error: 'Network error.' }));
            showToast(res.ok ? 'success' : 'error', res.ok ? res.message : (res.error || 'Setup failed.'));
            // A blocked start still set Sentinel up, so reload on that error too to show the new state.
            if (res.ok || /Sentinel is set up/.test(res.error || '')) setTimeout(() => location.reload(), 1500);
        });
    }

    document.querySelectorAll('.gk-setup-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab('sentinel');
        });
    });

    document.querySelectorAll('.mod-setup-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab('moderation');
        });
    });

    // -- Sentinel starter message modal ----------------------------------------
    // Pointing sentinel at a new channel re-deploys the captcha embed, so the save is
    // held until the moderator has written that embed. The tracker awaits this promise
    // before it POSTs the sentinel card.

    const gkChannel = document.getElementById('gk_channel');
    const gkModal = document.getElementById('gk-message-modal');
    const gkHiddenTitle = document.getElementById('gk_starter_title');
    const gkHiddenContent = document.getElementById('gk_starter_content');

    function sentinelStarterMessage() {
        if (!gkChannel || !gkModal) return Promise.resolve(true);

        const initial = gkChannel.dataset.initial || '';
        const changed = gkChannel.value !== initial && gkChannel.value !== '';
        if (!changed || gkHiddenTitle.value) return Promise.resolve(true);

        const modalTitle = document.getElementById('gk_modal_title');
        const modalContent = document.getElementById('gk_modal_content');
        const confirm = document.getElementById('gk-modal-confirm');
        const cancel = document.getElementById('gk-modal-cancel');

        gkModal.hidden = false;

        return new Promise(resolve => {
            function close(result) {
                gkModal.hidden = true;
                confirm.removeEventListener('click', onConfirm);
                cancel.removeEventListener('click', onCancel);
                resolve(result);
            }
            function onConfirm() {
                const title = modalTitle.value.trim();
                const content = modalContent.value.trim();
                if (!title || !content) {
                    showToast('error', 'Both title and content are required.');
                    return;
                }
                gkHiddenTitle.value = title;
                gkHiddenContent.value = content;
                close(true);
            }
            function onCancel() {
                gkChannel.value = gkChannel.dataset.initial || '';
                gkHiddenTitle.value = '';
                gkHiddenContent.value = '';
                PercyUnsaved.refresh();
                close(false);
            }
            confirm.addEventListener('click', onConfirm);
            cancel.addEventListener('click', onCancel);
        });
    }

    // -- Settings cards --------------------------------------------------------
    // Feature flags and moderation change which cards the server renders (a callout
    // appears when a flag is on but unconfigured), so those reload after saving.

    const RELOAD_SECTIONS = { flags: true, moderation: true };

    document.querySelectorAll('.config-tab-panel form').forEach(form => {
        const section = form.querySelector('input[name="_section"]');
        const key = section ? section.value : '';
        const isSentinel = form.action.includes('/sentinel');

        PercyUnsaved.watchForm(form, {
            label: form.closest('.config-section').querySelector('h2').textContent.trim(),
            reload: !!RELOAD_SECTIONS[key] || isSentinel,
            beforeSave: isSentinel ? sentinelStarterMessage : undefined,
        });
    });

    // The remaining cards on this page (audit-log events, status feed, custom bot) are
    // not forms; guild.js registers them with the tracker directly.
})();
