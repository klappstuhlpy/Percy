/* Percy Dashboard — role menus: create, edit text/single-choice, add/remove roles, delete.
   Each action posts JSON and reloads so the page shows what Percy now has; menu text and
   single-choice are settings and go through the unsaved-changes banner. GUILD_ID is injected. */

(function () {
    'use strict';

    const base = `/dashboard/guild/${GUILD_ID}/role-menus`;
    const post = (path, body) => fetch(base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(body || {}),
    }).then((r) => r.json()).catch(() => ({ ok: false, error: 'Network error.' }));
    const done = (res, success) => {
        if (res.ok) {
            showToast('success', res.message || success);
            setTimeout(() => location.reload(), 500);
        } else {
            showToast('error', res.error || 'That did not work.');
        }
        return res;
    };

    // -- New menu ----------------------------------------------------------------
    const modal = document.getElementById('rm-new-modal');
    document.getElementById('rm-new-btn').addEventListener('click', () => { modal.hidden = false; });
    document.getElementById('rm-new-cancel').addEventListener('click', () => { modal.hidden = true; });
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });
    document.getElementById('rm-new-confirm').addEventListener('click', async () => {
        const channel = document.getElementById('rm-new-channel').value;
        const title = document.getElementById('rm-new-title').value.trim();
        if (!channel) { showToast('error', 'Pick the channel to post the menu in.'); return; }
        if (!title) { showToast('error', 'Give the menu a title.'); return; }
        done(await post('', {
            channel_id: channel,
            title,
            description: document.getElementById('rm-new-desc').value.trim() || null,
            unique: document.getElementById('rm-new-unique').checked,
        }), 'Role menu posted.');
    });

    document.querySelectorAll('.rm-menu').forEach((section) => {
        const id = section.dataset.menuId;
        const title = section.querySelector('[name="title"]').value;

        // -- Text + single choice: settings, saved via the banner ----------------
        PercyUnsaved.watchForm(section.querySelector('.rm-settings'), {
            label: `Role menu "${title}"`,
            save: (f) => post(`/${id}`, {
                title: f.querySelector('[name="title"]').value.trim(),
                description: f.querySelector('[name="description"]').value.trim(),
                unique: f.querySelector('[name="unique"]').checked,
            }),
        });

        // -- Add a role ----------------------------------------------------------
        section.querySelector('.rm-add-btn').addEventListener('click', async () => {
            const role = section.querySelector('.rm-add-role');
            if (!role.value) { showToast('error', 'Pick the role to offer.'); return; }
            done(await post(`/${id}/roles`, {
                role_id: role.value,
                emoji: section.querySelector('.rm-add-emoji').value.trim() || null,
                label: section.querySelector('.rm-add-label').value.trim() || null,
            }), 'Role added.');
        });

        // -- Remove a role -------------------------------------------------------
        section.querySelectorAll('.rm-remove-btn').forEach((btn) => btn.addEventListener('click', async () => {
            if (!(await PercyConfirm.ask({
                title: `Remove @${btn.dataset.roleName} from this menu?`,
                consequence: 'Its button disappears from the posted message. Members who already have the role keep it.',
                undo: 'Add the role again at any time.',
                confirmLabel: 'Remove role',
            }))) return;
            done(await post(`/${id}/roles/${btn.dataset.roleId}/delete`), 'Role removed.');
        }));

        // -- Delete the menu -----------------------------------------------------
        section.querySelector('.rm-delete-btn').addEventListener('click', async () => {
            if (!(await PercyConfirm.ask({
                title: `Delete the role menu "${title}"?`,
                consequence: 'Its message is deleted from the channel. Members keep the roles they already picked.',
                undo: null,
                confirmLabel: 'Delete menu',
                danger: true,
            }))) return;
            done(await post(`/${id}/delete`), 'Role menu deleted.');
        });
    });
})();
