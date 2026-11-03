/* Percy Dashboard — Commands.
   Availability (everywhere / off in channels / only in channels / nowhere), the ignore list
   (plonks), and per-command permission overrides ("custom access") — everything `config` does.
   Expects GUILD_ID, COMMANDS_DATA, ROLES_DATA, PercyToast and PercyConfirm. */

(function() {
    'use strict';

    // Discord permission catalogue: canonical flag name -> label + bit value.
    // Bits can exceed 2^31, so they are handled as BigInt throughout (JS bitwise
    // operators are 32-bit and would silently truncate the larger flags).
    var PERMISSIONS = [
        { name: 'administrator',      label: 'Administrator',            bit: 8n },
        { name: 'manage_guild',       label: 'Manage Server',           bit: 32n },
        { name: 'manage_roles',       label: 'Manage Roles',            bit: 268435456n },
        { name: 'manage_channels',    label: 'Manage Channels',         bit: 16n },
        { name: 'kick_members',       label: 'Kick Members',            bit: 2n },
        { name: 'ban_members',        label: 'Ban Members',             bit: 4n },
        { name: 'moderate_members',   label: 'Timeout Members',         bit: 1099511627776n },
        { name: 'manage_messages',    label: 'Manage Messages',         bit: 8192n },
        { name: 'manage_nicknames',   label: 'Manage Nicknames',        bit: 134217728n },
        { name: 'manage_threads',     label: 'Manage Threads',          bit: 17179869184n },
        { name: 'manage_webhooks',    label: 'Manage Webhooks',         bit: 536870912n },
        { name: 'manage_expressions', label: 'Manage Emojis & Stickers', bit: 1073741824n },
        { name: 'mention_everyone',   label: 'Mention Everyone',        bit: 131072n },
        { name: 'view_audit_log',     label: 'View Audit Log',          bit: 128n },
        { name: 'manage_events',      label: 'Manage Events',           bit: 8589934592n },
    ];
    var PERM_BY_NAME = {};
    PERMISSIONS.forEach(function(p) { PERM_BY_NAME[p.name] = p; });

    function humanize(name) {
        var known = PERM_BY_NAME[name];
        if (known) return known.label;
        return name.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
    }

    // Decompose a bitmask string/number into the catalogue names it contains.
    function maskToNames(maskValue) {
        if (maskValue === null || maskValue === undefined) return [];
        var mask = BigInt(maskValue);
        return PERMISSIONS.filter(function(p) { return (mask & p.bit) === p.bit; }).map(function(p) { return p.name; });
    }

    // Human sentence describing a set of required permission names.
    function describePerms(names) {
        return names.length ? names.map(humanize).join(' + ') : 'anyone';
    }

    var rolesById = {};
    (ROLES_DATA || []).forEach(function(r) { rolesById[r.id] = r; });

    function roleColorHex(role) {
        if (!role || !role.color) return '#99aab5';
        return '#' + role.color.toString(16).padStart(6, '0');
    }

    function toast(kind, text) { PercyToast.show(kind, text); }

    // --- State -------------------------------------------------------------
    var editingIdx = null;
    var availMode = 'enabled';
    var accessMode = 'default';
    var selectedRoles = [];  // array of role id strings

    // --- Availability ------------------------------------------------------
    var CHANNEL_HINTS = {
        partial: 'Blocked in the checked channels, works everywhere else.',
        limited: 'Works only in the checked channels, blocked everywhere else.',
    };

    function modeOf(cmd) {
        if (cmd.globally_disabled) return (cmd.enabled_in || []).length ? 'limited' : 'disabled';
        return (cmd.disabled_in || []).length ? 'partial' : 'enabled';
    }

    function setAvailMode(mode) {
        availMode = mode;
        document.querySelectorAll('.cmd-mode-btn').forEach(function(btn) {
            var on = btn.getAttribute('data-mode') === mode;
            btn.className = 'cmd-mode-btn' + (on ? ' active-' + mode : '');
            btn.setAttribute('aria-checked', on ? 'true' : 'false');
        });
        document.getElementById('channel-select-wrap').hidden = !CHANNEL_HINTS[mode];
        if (CHANNEL_HINTS[mode]) document.getElementById('channel-select-hint').textContent = CHANNEL_HINTS[mode];
    }

    function checkChannels(ids) {
        document.querySelectorAll('#cmd-channels-list input[type="checkbox"]').forEach(function(cb) {
            cb.checked = ids.indexOf(cb.value) !== -1;
        });
    }

    function checkedChannels() {
        return Array.prototype.map.call(
            document.querySelectorAll('#cmd-channels-list input:checked'), function(cb) { return cb.value; });
    }

    document.getElementById('cmd-channel-filter').addEventListener('input', function() {
        var q = this.value.trim().toLowerCase();
        document.querySelectorAll('#cmd-channels-list .cmd-channel-row').forEach(function(row) {
            row.hidden = q !== '' && (row.getAttribute('data-name') || '').indexOf(q) === -1;
        });
    });

    // --- Access / permissions ---------------------------------------------
    function setAccessMode(mode) {
        accessMode = mode;
        document.querySelectorAll('.cmd-access-btn').forEach(function(btn) {
            var on = btn.getAttribute('data-access') === mode;
            btn.className = 'cmd-access-btn' + (on ? ' active-custom' : '');
            btn.setAttribute('aria-checked', on ? 'true' : 'false');
        });
        document.getElementById('cmd-access-custom').hidden = (mode !== 'custom');
        updateSummary();
    }

    function buildPermGrid() {
        var grid = document.getElementById('cmd-perm-grid');
        grid.innerHTML = '';
        PERMISSIONS.forEach(function(p) {
            var label = document.createElement('label');
            label.className = 'cmd-perm-row';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = p.name;
            cb.addEventListener('change', updateSummary);
            var span = document.createElement('span');
            span.textContent = p.label;
            label.appendChild(cb);
            label.appendChild(span);
            grid.appendChild(label);
        });
    }

    function setCheckedPerms(names) {
        document.querySelectorAll('#cmd-perm-grid input[type="checkbox"]').forEach(function(cb) {
            cb.checked = names.indexOf(cb.value) !== -1;
        });
    }

    function checkedPermNames() {
        return Array.prototype.map.call(document.querySelectorAll('#cmd-perm-grid input:checked'), function(cb) { return cb.value; });
    }

    // The permission bitmask as a JS Number — masks stay well below 2^53.
    function checkedPermMask() {
        var mask = 0n;
        checkedPermNames().forEach(function(name) { mask |= PERM_BY_NAME[name].bit; });
        return Number(mask);
    }

    function renderRoleChips() {
        var wrap = document.getElementById('cmd-role-chips');
        wrap.innerHTML = '';
        if (!selectedRoles.length) {
            var empty = document.createElement('span');
            empty.className = 'cmd-role-empty';
            empty.textContent = 'No roles — anyone meeting the permissions above may run it.';
            wrap.appendChild(empty);
            return;
        }
        selectedRoles.forEach(function(id) {
            var role = rolesById[id];
            var chip = document.createElement('span');
            chip.className = 'cmd-role-chip';
            chip.style.setProperty('--role-color', roleColorHex(role));
            var dot = document.createElement('span');
            dot.className = 'cmd-role-dot';
            var name = document.createElement('span');
            name.textContent = role ? role.name : id;
            var x = document.createElement('button');
            x.type = 'button';
            x.className = 'cmd-role-x';
            x.setAttribute('aria-label', 'Remove ' + (role ? role.name : id));
            x.textContent = '×';
            x.addEventListener('click', function() {
                selectedRoles = selectedRoles.filter(function(r) { return r !== id; });
                renderRoleChips();
                updateSummary();
            });
            chip.appendChild(dot);
            chip.appendChild(name);
            chip.appendChild(x);
            wrap.appendChild(chip);
        });
    }

    function updateSummary() {
        var box = document.getElementById('cmd-access-summary');
        if (accessMode !== 'custom') { box.textContent = ''; return; }
        var parts = ['Requires: ' + describePerms(checkedPermNames()) + '.'];
        if (selectedRoles.length) {
            parts.push('Roles that always pass: ' + selectedRoles.map(function(id) {
                return (rolesById[id] && rolesById[id].name) || id;
            }).join(', ') + '.');
        }
        box.textContent = parts.join(' ');
    }

    // --- Modal open/close --------------------------------------------------
    function openCommandEdit(idx) {
        var cmd = COMMANDS_DATA[idx];
        if (!cmd) return;
        editingIdx = idx;

        document.getElementById('cmd-edit-title').firstElementChild.textContent = cmd.name;
        document.getElementById('cmd-edit-desc').textContent = cmd.description || cmd.category;

        // Disabling a group blocks its subcommands too — say so before someone finds out.
        var subs = COMMANDS_DATA.filter(function(c) { return c.name.indexOf(cmd.name + ' ') === 0; }).length;
        var subsNote = document.getElementById('cmd-edit-subs');
        subsNote.hidden = !subs;
        subsNote.textContent = subs
            ? 'Restricting this group also restricts its ' + subs + ' subcommand' + (subs === 1 ? '' : 's') + '.'
            : '';

        var mode = modeOf(cmd);
        setAvailMode(mode);
        checkChannels(mode === 'limited' ? (cmd.enabled_in || []) : (cmd.disabled_in || []));
        document.getElementById('cmd-channel-filter').value = '';
        document.querySelectorAll('#cmd-channels-list .cmd-channel-row').forEach(function(row) { row.hidden = false; });

        // Custom commands carry their own access settings (Rules & Custom).
        document.getElementById('cmd-access-form').hidden = !!cmd.custom;
        document.getElementById('cmd-custom-note').hidden = !cmd.custom;

        var defaults = cmd.default_permissions || [];
        document.getElementById('cmd-access-default-hint').textContent = defaults.length
            ? 'Built-in gate: ' + describePerms(defaults) + '.'
            : 'No built-in permission gate — anyone can use it.';

        var ov = cmd.permission_override;
        if (ov) {
            setAccessMode('custom');
            // permissions == null on an override means "no permission requirement" (anyone).
            setCheckedPerms(ov.permissions === null || ov.permissions === undefined ? [] : maskToNames(ov.permissions));
            selectedRoles = (ov.allowed_roles || []).slice();
        } else {
            setAccessMode('default');
            setCheckedPerms(defaults);  // pre-seed the checklist with the current gate
            selectedRoles = [];
        }
        renderRoleChips();
        document.getElementById('cmd-role-select').value = '';
        updateSummary();

        document.getElementById('cmd-edit-modal').hidden = false;
        document.getElementById('cmd-cancel-btn').focus();
    }

    function closeCommandEdit() {
        document.getElementById('cmd-edit-modal').hidden = true;
        editingIdx = null;
    }

    // --- Save --------------------------------------------------------------
    async function postJSON(url, body) {
        var r = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body),
        });
        var data = await r.json().catch(function() { return { error: 'HTTP ' + r.status }; });
        if (data && data.error) throw new Error(data.error);
        return data;
    }

    async function saveAvailability(cmd) {
        var url = '/dashboard/guild/' + GUILD_ID + '/commands/toggle';
        var channels = (availMode === 'partial' || availMode === 'limited') ? checkedChannels() : [];
        // Start from a clean slate: drops the server-wide row and every per-channel row.
        await postJSON(url, { name: cmd.name, enabled: true, channel_id: null });
        if (availMode === 'disabled' || availMode === 'limited') {
            await postJSON(url, { name: cmd.name, enabled: false, channel_id: null });
        }
        for (var i = 0; i < channels.length; i++) {
            await postJSON(url, availMode === 'limited'
                ? { name: cmd.name, enabled: true, channel_id: channels[i], allow: true }
                : { name: cmd.name, enabled: false, channel_id: channels[i] });
        }
    }

    async function saveAccess(cmd) {
        if (cmd.custom) return;
        var url = '/dashboard/guild/' + GUILD_ID + '/commands/permissions';
        if (accessMode === 'default') {
            if (cmd.permission_override) await postJSON(url, { command: cmd.name, permissions: null, allowed_roles: [] });
            return;
        }
        // Custom: a 0 mask with no roles is a valid "anyone" override (distinct from default).
        await postJSON(url, { command: cmd.name, permissions: checkedPermMask(), allowed_roles: selectedRoles });
    }

    async function onSave() {
        if (editingIdx === null) return;
        var cmd = COMMANDS_DATA[editingIdx];
        if ((availMode === 'partial' || availMode === 'limited') && !checkedChannels().length) {
            toast('warning', 'Pick at least one channel.');
            return;
        }
        var btn = document.getElementById('cmd-save-btn');
        btn.disabled = true;
        try {
            await saveAvailability(cmd);
            await saveAccess(cmd);
            toast('success', cmd.name + ' updated.');
            closeCommandEdit();
            setTimeout(function() { location.reload(); }, 400);
        } catch (err) {
            toast('error', err.message || 'Network error');
        } finally {
            btn.disabled = false;
        }
    }

    // --- Wiring ------------------------------------------------------------
    buildPermGrid();

    document.getElementById('cm-groups').addEventListener('click', function(e) {
        var row = e.target.closest('.cm-row');
        if (row) openCommandEdit(parseInt(row.getAttribute('data-idx'), 10));
    });
    document.querySelectorAll('.cmd-mode-btn').forEach(function(btn) {
        btn.addEventListener('click', function() { setAvailMode(btn.getAttribute('data-mode')); });
    });
    document.querySelectorAll('.cmd-access-btn').forEach(function(btn) {
        btn.addEventListener('click', function() { setAccessMode(btn.getAttribute('data-access')); });
    });
    document.getElementById('cmd-role-select').addEventListener('change', function() {
        var id = this.value;
        this.value = '';
        if (!id || selectedRoles.indexOf(id) !== -1) return;
        selectedRoles.push(id);
        renderRoleChips();
        updateSummary();
    });
    document.getElementById('cmd-save-btn').addEventListener('click', onSave);
    document.getElementById('cmd-cancel-btn').addEventListener('click', closeCommandEdit);
    document.getElementById('cmd-edit-modal').addEventListener('click', function(e) {
        if (e.target === e.currentTarget) closeCommandEdit();
    });
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        closeCommandEdit();
        closePlonkModal();
    });

    // --- Search + filters --------------------------------------------------
    var filter = { q: '', cat: '', kind: 'all' };

    function applyFilters() {
        var shown = 0;
        document.querySelectorAll('.cm-group').forEach(function(group) {
            var inCat = !filter.cat || group.getAttribute('data-category') === filter.cat;
            var groupShown = 0;
            group.querySelectorAll('.cm-section').forEach(function(section) {
                var sectionShown = 0;
                section.querySelectorAll('.cm-row').forEach(function(row) {
                    var ok = inCat
                        && (!filter.q || row.getAttribute('data-search').indexOf(filter.q) !== -1)
                        && (filter.kind === 'all' || row.getAttribute('data-' + filter.kind) === '1');
                    row.parentElement.hidden = !ok;
                    if (ok) sectionShown++;
                });
                section.hidden = !sectionShown;
                groupShown += sectionShown;
            });
            group.hidden = !groupShown;
            // A search or filter opens the groups it found something in.
            if (groupShown && (filter.q || filter.kind !== 'all')) group.open = true;
            shown += groupShown;
        });
        document.getElementById('cm-none').hidden = shown > 0;
    }

    document.getElementById('cmd-search').addEventListener('input', function() {
        filter.q = this.value.trim().toLowerCase();
        applyFilters();
    });
    document.getElementById('cmd-category').addEventListener('change', function() {
        filter.cat = this.value;
        applyFilters();
    });
    document.querySelectorAll('.cm-filter').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.cm-filter').forEach(function(b) { b.classList.toggle('active', b === btn); });
            filter.kind = btn.getAttribute('data-filter');
            applyFilters();
        });
    });

    // --- Ignore list (plonks) ---------------------------------------------
    var plonkUrl = '/dashboard/guild/' + GUILD_ID + '/plonks';
    var plonkModal = document.getElementById('plonk-modal');
    var plonkInput = document.getElementById('plonk-id-input');
    var plonkChannel = document.getElementById('plonk-channel-select');

    function openPlonkModal() {
        plonkInput.value = '';
        plonkChannel.value = '';
        plonkModal.hidden = false;
        plonkChannel.focus();
    }
    function closePlonkModal() { plonkModal.hidden = true; }

    async function plonk(body, done) {
        try {
            await postJSON(plonkUrl, body);
            toast('success', done);
            setTimeout(function() { location.reload(); }, 400);
            return true;
        } catch (err) {
            toast('error', err.message || 'Network error');
            return false;
        }
    }

    document.getElementById('add-plonk-btn').addEventListener('click', openPlonkModal);
    document.getElementById('plonk-cancel-btn').addEventListener('click', closePlonkModal);
    plonkModal.addEventListener('click', function(e) { if (e.target === e.currentTarget) closePlonkModal(); });

    document.getElementById('plonk-add-btn').addEventListener('click', async function() {
        var id = plonkChannel.value || plonkInput.value.trim();
        if (!/^\d{15,20}$/.test(id)) { toast('error', 'Pick a channel or enter a valid user ID.'); return; }
        this.disabled = true;
        if (await plonk({ action: 'add', entity_id: id }, 'Percy now ignores it.')) closePlonkModal();
        this.disabled = false;
    });

    document.getElementById('plonk-all-btn').addEventListener('click', async function() {
        var ok = await PercyConfirm.ask({
            title: 'Ignore every text channel?',
            consequence: 'Percy stops answering commands in every text channel that exists now. Channels created later are not included.',
            undo: 'Stop ignoring channels one by one, or use Clear all.',
            confirmLabel: 'Ignore all channels',
        });
        if (ok) plonk({ action: 'all' }, 'Every text channel is ignored.');
    });

    document.getElementById('plonk-clear-btn').addEventListener('click', async function() {
        var ok = await PercyConfirm.ask({
            title: 'Clear the whole ignore list?',
            consequence: 'Percy answers commands from every member and in every channel again.',
            undo: null,
            confirmLabel: 'Clear all',
            danger: true,
        });
        if (ok) plonk({ action: 'clear' }, 'Ignore list cleared.');
    });

    document.getElementById('plonks-tbody').addEventListener('click', async function(e) {
        var btn = e.target.closest('[data-unplonk]');
        if (!btn) return;
        btn.disabled = true;
        try {
            await postJSON(plonkUrl, { action: 'remove', entity_id: btn.getAttribute('data-unplonk') });
            btn.closest('tr').remove();
            var empty = !document.querySelector('#plonks-tbody tr');
            document.getElementById('plonks-table-wrap').hidden = empty;
            document.getElementById('plonks-empty').hidden = !empty;
            document.getElementById('plonk-clear-btn').hidden = empty;
            toast('success', 'No longer ignored.');
        } catch (err) {
            btn.disabled = false;
            toast('error', err.message || 'Network error');
        }
    });
})();
