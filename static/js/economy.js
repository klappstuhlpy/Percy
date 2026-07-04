/* Percy Dashboard — Economy (shop items + lottery management).
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const base = `/dashboard/guild/${guildId}/economy`;

    // Guild economy settings
    const settingsSaveBtn = document.getElementById('eco-settings-save');
    if (settingsSaveBtn) {
        settingsSaveBtn.addEventListener('click', async () => {
            const payout_multiplier = parseFloat(document.getElementById('eco_payout_multiplier').value);
            const daily_base = parseInt(document.getElementById('eco_daily_base').value);
            const max_bet = parseInt(document.getElementById('eco_max_bet').value);
            const rob_enabled = document.getElementById('eco_rob_enabled').checked;
            if (!payout_multiplier || payout_multiplier < 0.1 || payout_multiplier > 10) { showToast('error', 'Payout multiplier must be between 0.1 and 10.'); return; }
            if (!daily_base || daily_base < 10 || daily_base > 100000) { showToast('error', 'Daily base must be between 10 and 100,000.'); return; }
            if (isNaN(max_bet) || max_bet < 0) { showToast('error', 'Max bet must be 0 (uncapped) or positive.'); return; }
            try {
                const r = await fetch(`${base}/settings`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify({ payout_multiplier, daily_base, max_bet, rob_enabled }) });
                const d = await r.json();
                if (d.ok) { showToast('success', 'Economy settings saved.'); }
                else { showToast('error', d.error || 'Failed.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    }

    // Add item
    const addItemBtn = document.getElementById('add-item-btn');
    const addItemModal = document.getElementById('add-item-modal');
    const effectSelect = document.getElementById('item_effect');
    const effectValueGroup = document.getElementById('effect-value-group');
    const effectValueLabel = document.getElementById('effect-value-label');
    const effectRoleGroup = document.getElementById('effect-role-group');
    const effectDurationGroup = document.getElementById('effect-duration-group');

    function updateEffectFields() {
        const effect = effectSelect.value;
        effectValueGroup.hidden = true;
        effectRoleGroup.hidden = true;
        effectDurationGroup.hidden = true;
        if (effect === 'cash' || effect === 'lootbox') {
            effectValueGroup.hidden = false;
            effectValueLabel.textContent = 'Cash Amount';
        } else if (effect === 'xp_boost' || effect === 'loot_boost') {
            effectValueGroup.hidden = false;
            effectValueLabel.textContent = 'Bonus Percent (1-500)';
            effectDurationGroup.hidden = false;
        } else if (effect === 'role') {
            effectRoleGroup.hidden = false;
        }
    }

    if (addItemBtn && addItemModal) {
        effectSelect.addEventListener('change', updateEffectFields);
        addItemBtn.addEventListener('click', () => { addItemModal.hidden = false; });
        document.getElementById('item-modal-cancel').addEventListener('click', () => { addItemModal.hidden = true; });
        addItemModal.addEventListener('click', (e) => { if (e.target === addItemModal) addItemModal.hidden = true; });
        document.getElementById('item-modal-confirm').addEventListener('click', async () => {
            const name = document.getElementById('item_name').value.trim();
            const price = parseInt(document.getElementById('item_price').value);
            const description = document.getElementById('item_desc').value.trim() || null;
            if (!name || !price) { showToast('error', 'Name and price required.'); return; }
            const effect = effectSelect.value;
            const body = { name, price, description };
            if (effect !== 'none') {
                body.effect = effect;
                if (effect === 'cash' || effect === 'lootbox' || effect === 'xp_boost' || effect === 'loot_boost') {
                    const val = parseInt(document.getElementById('item_effect_value').value);
                    if (!val || val < 1) { showToast('error', 'Effect value is required.'); return; }
                    body.effect_value = val;
                }
                if (effect === 'role') {
                    const roleId = document.getElementById('item_effect_role').value;
                    if (!roleId) { showToast('error', 'Please select a role.'); return; }
                    body.effect_value = parseInt(roleId);
                }
                if (effect === 'xp_boost' || effect === 'loot_boost') {
                    const dur = parseInt(document.getElementById('item_duration').value);
                    if (!dur || dur < 1) { showToast('error', 'Duration is required for boost effects.'); return; }
                    body.duration_minutes = dur;
                }
            }
            try {
                const r = await fetch(`${base}/items`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(body) });
                const d = await r.json();
                if (d.ok) { showToast('success', 'Item created.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', d.error || 'Failed.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    }

    // Delete item
    document.querySelectorAll('.delete-item-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = btn.dataset.name;
            if (!confirm(`Delete item "${name}"?`)) return;
            try {
                const r = await fetch(`${base}/items/${encodeURIComponent(name)}`, { method: 'DELETE', headers: { 'Accept': 'application/json' } });
                const d = await r.json();
                if (d.ok) { showToast('success', 'Deleted.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', d.error || 'Failed.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });

    // Lottery start
    const startBtn = document.getElementById('start-lottery-btn');
    const lotteryModal = document.getElementById('lottery-modal');
    if (startBtn && lotteryModal) {
        startBtn.addEventListener('click', () => { lotteryModal.hidden = false; });
        document.getElementById('lottery-modal-cancel').addEventListener('click', () => { lotteryModal.hidden = true; });
        lotteryModal.addEventListener('click', (e) => { if (e.target === lotteryModal) lotteryModal.hidden = true; });
        document.getElementById('lottery-modal-confirm').addEventListener('click', async () => {
            const ticket_price = parseInt(document.getElementById('lottery_price').value);
            const duration_minutes = parseInt(document.getElementById('lottery_duration').value);
            const channel_id = document.getElementById('lottery_channel').value.trim();
            if (!ticket_price || !duration_minutes || !channel_id) { showToast('error', 'All fields required.'); return; }
            try {
                const r = await fetch(`${base}/lottery`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify({ ticket_price, duration_minutes, channel_id }) });
                const d = await r.json();
                if (d.ok) { showToast('success', 'Lottery started.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', d.error || 'Failed.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    }

    // Lottery cancel
    const cancelBtn = document.getElementById('cancel-lottery-btn');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', async () => {
            if (!confirm('Cancel the active lottery?')) return;
            try {
                const r = await fetch(`${base}/lottery`, { method: 'DELETE', headers: { 'Accept': 'application/json' } });
                const d = await r.json();
                if (d.ok) { showToast('success', 'Lottery cancelled.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', d.error || 'Failed.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    }

    // Edit member balance
    const balanceModal = document.getElementById('edit-balance-modal');
    if (balanceModal) {
        let editingUserId = null;
        const usernameSpan = document.getElementById('balance-username');
        const cashInput = document.getElementById('balance_cash');
        const bankInput = document.getElementById('balance_bank');

        document.querySelectorAll('.edit-balance-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                editingUserId = btn.dataset.userId;
                usernameSpan.textContent = btn.dataset.username;
                cashInput.value = btn.dataset.cash;
                bankInput.value = btn.dataset.bank;
                balanceModal.hidden = false;
            });
        });

        const closeModal = () => { balanceModal.hidden = true; editingUserId = null; };
        document.getElementById('balance-modal-cancel').addEventListener('click', closeModal);
        balanceModal.addEventListener('click', (e) => { if (e.target === balanceModal) closeModal(); });

        document.getElementById('balance-modal-confirm').addEventListener('click', async () => {
            if (!editingUserId) return;
            const cash = parseInt(cashInput.value, 10);
            const bank = parseInt(bankInput.value, 10);
            if (Number.isNaN(cash) && Number.isNaN(bank)) { showToast('error', 'Enter a cash or bank value.'); return; }
            const body = {};
            if (!Number.isNaN(cash)) body.cash = cash;
            if (!Number.isNaN(bank)) body.bank = bank;
            try {
                const r = await fetch(`${base}/balances/${encodeURIComponent(editingUserId)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(body),
                });
                const d = await r.json();
                if (d.ok) { showToast('success', 'Balance updated.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', d.error || 'Failed.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    }
})();
