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

    const hash = location.hash.slice(1);
    if (hash && document.getElementById('tab-' + hash)) {
        switchTab(hash);
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
