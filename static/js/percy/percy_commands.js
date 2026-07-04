/* Percy Dashboard — Commands.
   Availability (enable / per-channel / disable), plonks, and per-command
   permission overrides ("custom access").
   Expects GUILD_ID, COMMANDS_DATA, ROLES_DATA and window.showToast (percy_common.js). */

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
        var out = [];
        PERMISSIONS.forEach(function(p) {
            if ((mask & p.bit) === p.bit && p.bit !== 0n) out.push(p.name);
        });
        return out;
    }

    // Human sentence describing a set of required permission names.
    function describePerms(names) {
        if (!names.length) return 'anyone';
        return names.map(humanize).join(' + ');
    }

    var rolesById = {};
    (ROLES_DATA || []).forEach(function(r) { rolesById[r.id] = r; });

    function roleColorHex(role) {
        if (!role || !role.color) return '#99aab5';
        return '#' + role.color.toString(16).padStart(6, '0');
    }

    // --- State -------------------------------------------------------------
    var editingIdx = null;
    var availMode = 'enabled';
    var accessMode = 'default';
    var selectedRoles = [];  // array of role id strings

    // --- Availability ------------------------------------------------------
    function setAvailMode(mode) {
        availMode = mode;
        document.querySelectorAll('.cmd-mode-btn').forEach(function(btn) {
            btn.className = 'cmd-mode-btn';
            if (btn.getAttribute('data-mode') === mode) btn.classList.add('active-' + mode);
        });
        document.getElementById('channel-select-wrap').hidden = (mode !== 'partial');
    }

    // --- Access / permissions ---------------------------------------------
    function setAccessMode(mode) {
        accessMode = mode;
        document.querySelectorAll('.cmd-access-btn').forEach(function(btn) {
            btn.className = 'cmd-access-btn';
            if (btn.getAttribute('data-access') === mode) btn.classList.add('active-custom');
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
            cb.setAttribute('data-perm', p.name);
            cb.addEventListener('change', updateSummary);
            var span = document.createElement('span');
            span.textContent = p.label;
            label.appendChild(cb);
            label.appendChild(span);
            grid.appendChild(label);
        });
    }

    function setCheckedPerms(names) {
        var set = {};
        names.forEach(function(n) { set[n] = true; });
        document.querySelectorAll('#cmd-perm-grid input[type="checkbox"]').forEach(function(cb) {
            cb.checked = !!set[cb.value];
        });
    }

    function checkedPermNames() {
        var names = [];
        document.querySelectorAll('#cmd-perm-grid input:checked').forEach(function(cb) {
            names.push(cb.value);
        });
        return names;
    }

    // Build the permission bitmask (as a JS Number — masks stay well below 2^53).
    function checkedPermMask() {
        var mask = 0n;
        document.querySelectorAll('#cmd-perm-grid input:checked').forEach(function(cb) {
            var p = PERM_BY_NAME[cb.value];
            if (p) mask |= p.bit;
        });
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
            x.innerHTML = '&times;';
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
        var perms = checkedPermNames();
        var parts = [];
        parts.push('Requires: ' + describePerms(perms) + '.');
        if (selectedRoles.length) {
            var roleNames = selectedRoles.map(function(id) {
                return (rolesById[id] && rolesById[id].name) || id;
            });
            parts.push('Roles that always pass: ' + roleNames.join(', ') + '.');
        }
        box.textContent = parts.join(' ');
    }

    // --- Modal open/close --------------------------------------------------
    function openCommandEdit(idx) {
        editingIdx = idx;
        var cmd = COMMANDS_DATA[idx];
        if (!cmd) return;

        document.getElementById('cmd-edit-title').textContent = cmd.name;
        document.getElementById('cmd-edit-desc').textContent = cmd.description || cmd.category;

        // Availability
        if (cmd.globally_disabled) setAvailMode('disabled');
        else if ((cmd.disabled_in || []).length > 0) setAvailMode('partial');
        else setAvailMode('enabled');

        document.querySelectorAll('#cmd-channels-list input[type="checkbox"]').forEach(function(cb) {
            cb.checked = (cmd.disabled_in || []).indexOf(cb.value) !== -1;
        });

        // Access — describe the built-in default gate.
        var defaults = cmd.default_permissions || [];
        var hint = document.getElementById('cmd-access-default-hint');
        if (defaults.length) {
            hint.innerHTML = 'Built-in gate: <strong>' + describePerms(defaults) + '</strong>.';
        } else {
            hint.innerHTML = 'This command has no built-in permission gate (anyone can use it, or it is owner-only).';
        }

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
    }

    function closeCommandEdit() {
        document.getElementById('cmd-edit-modal').hidden = true;
        editingIdx = null;
    }

    // --- Save --------------------------------------------------------------
    function postJSON(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body),
        }).then(function(r) { return r.json(); });
    }

    async function saveAvailability(cmd) {
        var base = '/dashboard/guild/' + GUILD_ID + '/commands/toggle';
        if (availMode === 'enabled') {
            return postJSON(base, { name: cmd.name, enabled: true, channel_id: null });
        }
        if (availMode === 'disabled') {
            return postJSON(base, { name: cmd.name, enabled: false, channel_id: null });
        }
        // Partial: clear everything, then disable in the checked channels.
        await postJSON(base, { name: cmd.name, enabled: true, channel_id: null });
        var checked = document.querySelectorAll('#cmd-channels-list input:checked');
        for (var i = 0; i < checked.length; i++) {
            await postJSON(base, { name: cmd.name, enabled: false, channel_id: checked[i].value });
        }
        return { ok: true };
    }

    async function saveAccess(cmd) {
        var url = '/dashboard/guild/' + GUILD_ID + '/commands/permissions';
        var hadOverride = !!cmd.permission_override;
        if (accessMode === 'default') {
            // Nothing to clear if there was never an override.
            if (!hadOverride) return { ok: true };
            return postJSON(url, { command: cmd.name, permissions: null, allowed_roles: [] });
        }
        // Custom: a 0 mask with no roles is a valid "anyone" override (distinct from default).
        return postJSON(url, {
            command: cmd.name,
            permissions: checkedPermMask(),
            allowed_roles: selectedRoles,
        });
    }

    async function onSave() {
        if (editingIdx === null) return;
        var cmd = COMMANDS_DATA[editingIdx];
        var btn = document.getElementById('cmd-save-btn');
        btn.disabled = true;
        try {
            var a = await saveAvailability(cmd);
            if (a && a.ok === false) { showToast('error', a.error || 'Failed to update availability'); btn.disabled = false; return; }
            var p = await saveAccess(cmd);
            if (p && p.ok === false) { showToast('error', p.error || 'Failed to update access'); btn.disabled = false; return; }
            showToast('success', 'Command "' + cmd.name + '" updated.');
            closeCommandEdit();
            setTimeout(function() { location.reload(); }, 400);
        } catch (err) {
            showToast('error', 'Network error');
        } finally {
            btn.disabled = false;
        }
    }

    // --- Wiring ------------------------------------------------------------
    buildPermGrid();

    var grid = document.getElementById('command-grid');
    if (grid) {
        grid.addEventListener('click', function(e) {
            var card = e.target.closest('.command-card');
            if (!card) return;
            var idx = parseInt(card.getAttribute('data-idx'), 10);
            if (!isNaN(idx)) openCommandEdit(idx);
        });
    }

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

    // --- Search + filters --------------------------------------------------
    var activeFilter = 'all';

    function applyFilters() {
        var q = (document.getElementById('cmd-search').value || '').toLowerCase();
        var cards = document.querySelectorAll('.command-card');
        for (var i = 0; i < cards.length; i++) {
            var c = cards[i];
            var name = c.getAttribute('data-name') || '';
            var cat = c.getAttribute('data-category') || '';
            var matchesSearch = name.indexOf(q) !== -1 || cat.indexOf(q) !== -1;
            var matchesFilter = activeFilter === 'all'
                || (activeFilter === 'custom' && c.getAttribute('data-custom') === '1')
                || (activeFilter === 'restricted' && c.getAttribute('data-restricted') === '1');
            c.style.display = (matchesSearch && matchesFilter) ? '' : 'none';
        }
    }

    document.getElementById('cmd-search').addEventListener('input', applyFilters);
    document.querySelectorAll('.cmd-filter').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.cmd-filter').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            activeFilter = btn.getAttribute('data-filter');
            applyFilters();
        });
    });

    // --- Plonks ------------------------------------------------------------
    var plonkModal = document.getElementById('plonk-modal');
    var plonkInput = document.getElementById('plonk-id-input');
    function openPlonkModal() { plonkInput.value = ''; plonkModal.hidden = false; plonkInput.focus(); }
    function closePlonkModal() { plonkModal.hidden = true; }

    document.getElementById('add-plonk-btn').addEventListener('click', openPlonkModal);
    document.getElementById('plonk-cancel-btn').addEventListener('click', closePlonkModal);
    plonkModal.addEventListener('click', function(e) { if (e.target === e.currentTarget) closePlonkModal(); });

    document.getElementById('plonk-add-btn').addEventListener('click', async function() {
        var id = plonkInput.value.trim();
        if (!/^\d{15,20}$/.test(id)) { showToast('error', 'Enter a valid Discord ID (15-20 digits).'); return; }
        var btn = this;
        btn.disabled = true;
        try {
            var data = await postJSON('/dashboard/guild/' + GUILD_ID + '/plonks', { action: 'add', entity_id: id });
            if (data.ok) {
                showToast('success', 'Entity plonked.');
                closePlonkModal();
                setTimeout(function() { location.reload(); }, 400);
            } else {
                showToast('error', data.error || 'Failed to plonk.');
            }
        } catch (err) {
            showToast('error', 'Network error');
        } finally {
            btn.disabled = false;
        }
    });

    window.removePlonk = async function(entityId) {
        try {
            var data = await postJSON('/dashboard/guild/' + GUILD_ID + '/plonks', { action: 'remove', entity_id: entityId });
            if (data.ok) {
                var row = document.querySelector('[data-entity-id="' + entityId + '"]');
                if (row) row.remove();
                showToast('success', 'Entity unplonked.');
            } else {
                showToast('error', data.error || 'Failed to unplonk');
            }
        } catch (err) {
            showToast('error', 'Network error');
        }
    };
})();
