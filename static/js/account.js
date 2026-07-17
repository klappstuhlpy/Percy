(function () {
    'use strict';

    const form = document.getElementById('account-settings-form');

    if (form) {
        PercyUnsaved.watchForm(form, {
            label: 'Account Settings',
            save: async (f) => {
                const res = await fetch('/account/settings', {
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

    const deleteBtn = document.getElementById('delete-data-btn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', async function () {
            const ok = window.confirm(
                'Permanently delete your stored presence, avatar and name/nickname history across ALL servers?\n\n' +
                'This cannot be undone.'
            );
            if (!ok) return;

            deleteBtn.disabled = true;
            const original = deleteBtn.textContent;
            deleteBtn.textContent = 'Deleting…';
            try {
                const res = await fetch('/account/delete-data', {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                });
                const data = await res.json().catch(() => ({}));
                if (res.ok && data.ok) {
                    if (window.showToast) showToast('success', 'Your history has been deleted.');
                    setTimeout(() => window.location.reload(), 800);
                } else {
                    if (window.showToast) showToast('error', data.error || 'Failed to delete data.');
                    deleteBtn.disabled = false;
                    deleteBtn.textContent = original;
                }
            } catch (err) {
                if (window.showToast) showToast('error', 'Network error — check your connection.');
                deleteBtn.disabled = false;
                deleteBtn.textContent = original;
            }
        });
    }
})();
