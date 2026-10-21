/* "Send test" buttons beside the channel pickers of posting features.
   The test goes to the SAVED destination, so an unsaved pick is refused first. */
(function () {
    document.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-test-send]');
        if (!btn) return;
        const select = document.getElementById(btn.dataset.for);
        const saved = select && [...select.options].find(o => o.defaultSelected);
        if (select && select.value !== (saved ? saved.value : '')) {
            showToast('error', 'Save your changes first — the test uses the saved channel.');
            return;
        }
        btn.disabled = true;
        const res = await fetch(`/dashboard/guild/${window.PERCY_DASH.guildId}/test-message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ feature: btn.dataset.testSend }),
        }).then(r => r.json()).catch(() => ({ ok: false, error: 'Network error.' }));
        btn.disabled = false;
        showToast(res.ok ? 'success' : 'error', res.ok ? res.message : (res.error || 'Test message failed.'));
    });
})();
