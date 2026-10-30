/* Percy Dashboard — Economy (shop items + lottery management).
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    const guildId = GUILD_ID;
    const base = `/dashboard/guild/${guildId}/economy`;

    // Guild economy settings — no Save button; the global tracker (unsaved.js) raises the
    // banner on any edit and calls save() below.
    const payoutInput = document.getElementById('eco_payout_multiplier');
    if (payoutInput) {
        PercyUnsaved.register({
            label: 'Economy Settings',
            element: payoutInput.closest('.config-section'),
            read: () => ({
                payout_multiplier: parseFloat(payoutInput.value),
                daily_base: parseInt(document.getElementById('eco_daily_base').value),
                max_bet: parseInt(document.getElementById('eco_max_bet').value),
                rob_enabled: document.getElementById('eco_rob_enabled').checked,
            }),
            write: (state) => {
                payoutInput.value = state.payout_multiplier;
                document.getElementById('eco_daily_base').value = state.daily_base;
                document.getElementById('eco_max_bet').value = state.max_bet;
                document.getElementById('eco_rob_enabled').checked = state.rob_enabled;
            },
            save: async (state) => {
                if (!(state.payout_multiplier >= 0.1 && state.payout_multiplier <= 10)) {
                    return { ok: false, error: 'Payout multiplier must be between 0.1 and 10.' };
                }
                if (!(state.daily_base >= 10 && state.daily_base <= 100000)) {
                    return { ok: false, error: 'Daily base must be between 10 and 100,000.' };
                }
                if (!(state.max_bet >= 0)) {
                    return { ok: false, error: 'Max bet must be 0 (uncapped) or positive.' };
                }
                const r = await fetch(`${base}/settings`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(state),
                });
                return r.json();
            },
        });
    }

    // Add / edit item: one modal; `editingId` switches it to PATCH (the effect is create-only).
    const addItemBtn = document.getElementById('add-item-btn');
    const addItemModal = document.getElementById('add-item-modal');
    const effectSelect = document.getElementById('item_effect');
    const effectValueGroup = document.getElementById('effect-value-group');
    const effectValueLabel = document.getElementById('effect-value-label');
    const effectRoleGroup = document.getElementById('effect-role-group');
    const effectDurationGroup = document.getElementById('effect-duration-group');
    const $ = (id) => document.getElementById(id);
    let editingId = null;

    function updateEffectFields() {
        const effect = effectSelect.value;
        effectValueGroup.hidden = true;
        effectRoleGroup.hidden = true;
        effectDurationGroup.hidden = true;
        if (editingId !== null) return; // the effect and its parameters are fixed once created
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

    function openItemModal(d) {
        editingId = d ? d.id : null;
        $('item-modal-title').textContent = d ? 'Edit Shop Item' : 'Add Shop Item';
        $('item-modal-confirm').textContent = d ? 'Save' : 'Create';
        effectSelect.disabled = !!d;
        $('effect-fixed-note').hidden = !d;
        $('item_name').value = d ? d.name : '';
        $('item_price').value = d ? d.price : '';
        $('item_desc').value = d ? d.description : '';
        $('item_emoji').value = d ? d.emoji : '';
        $('item_image').value = d ? d.image : '';
        $('item_rarity').value = d ? d.rarity : 'common';
        effectSelect.value = d ? d.effect : 'none';
        $('item_effect_value').value = '';
        $('item_effect_role').value = '';
        $('item_duration').value = '';
        $('item_stock').value = d ? d.stock : '';
        $('item_max_per_user').value = d ? d.maxPerUser : '';
        $('item_required_role').value = d ? d.requiredRole : '';
        $('item_remove_role').value = d ? d.removeRole : '';
        // Percy answers naive UTC ("2026-12-01T00:00:00"); datetime-local wants minutes.
        $('item_available_until').value = d ? d.availableUntil.slice(0, 16) : '';
        $('item_reply').value = d ? d.reply : '';
        updateEffectFields();
        addItemModal.hidden = false;
    }

    if (addItemBtn && addItemModal) {
        effectSelect.addEventListener('change', updateEffectFields);
        addItemBtn.addEventListener('click', () => openItemModal(null));
        document.querySelectorAll('.edit-item-btn').forEach(btn => {
            btn.addEventListener('click', () => openItemModal({ ...btn.dataset }));
        });
        document.getElementById('item-modal-cancel').addEventListener('click', () => { addItemModal.hidden = true; });
        addItemModal.addEventListener('click', (e) => { if (e.target === addItemModal) addItemModal.hidden = true; });
        document.getElementById('item-modal-confirm').addEventListener('click', async () => {
            const name = $('item_name').value.trim();
            const price = parseInt($('item_price').value);
            if (!name || !price) { showToast('error', 'Name and price required.'); return; }
            const intOrNull = (id) => { const v = parseInt($(id).value); return Number.isNaN(v) ? null : v; };
            const body = {
                name, price,
                description: $('item_desc').value.trim() || null,
                emoji: $('item_emoji').value.trim() || null,
                image_url: $('item_image').value.trim() || null,
                rarity: $('item_rarity').value,
                stock: intOrNull('item_stock'),
                max_per_user: intOrNull('item_max_per_user'),
                required_role_id: $('item_required_role').value || null,
                remove_role_id: $('item_remove_role').value || null,
                available_until: $('item_available_until').value || null,
                reply_message: $('item_reply').value.trim() || null,
            };
            if (editingId === null) {
                const effect = effectSelect.value;
                if (effect !== 'none') {
                    body.effect = effect;
                    if (effect === 'cash' || effect === 'lootbox' || effect === 'xp_boost' || effect === 'loot_boost') {
                        const val = parseInt($('item_effect_value').value);
                        if (!val || val < 1) { showToast('error', 'Effect value is required.'); return; }
                        body.effect_value = val;
                    }
                    if (effect === 'role') {
                        const roleId = $('item_effect_role').value;
                        if (!roleId) { showToast('error', 'Please select a role.'); return; }
                        body.effect_value = roleId; // a string: the server converts it without losing snowflake precision
                    }
                    if (effect === 'xp_boost' || effect === 'loot_boost') {
                        const dur = parseInt($('item_duration').value);
                        if (!dur || dur < 1) { showToast('error', 'Duration is required for boost effects.'); return; }
                        body.duration_minutes = dur;
                    }
                }
            }
            const url = editingId === null ? `${base}/items` : `${base}/items/${editingId}`;
            try {
                const r = await fetch(url, { method: editingId === null ? 'POST' : 'PATCH', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(body) });
                const d = await r.json();
                if (d.ok) { showToast('success', editingId === null ? 'Item created.' : 'Item updated.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', d.error || 'Failed.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    }

    // Delete item
    document.querySelectorAll('.delete-item-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = btn.dataset.name;
            if (!(await PercyConfirm.ask({ title: `Delete shop item "${name}"?`, consequence: "Every copy members already own is deleted too. To stop sales but let people keep their copies, set its stock to 0 instead.", undo: null, confirmLabel: "Delete item", danger: true }))) return;
            try {
                const r = await fetch(`${base}/items/${encodeURIComponent(btn.dataset.id)}`, { method: 'DELETE', headers: { 'Accept': 'application/json' } });
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
            if (!(await PercyConfirm.ask({ title: "Cancel the active lottery?", consequence: "No winner is drawn.", undo: null, confirmLabel: "Cancel lottery", danger: true }))) return;
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
