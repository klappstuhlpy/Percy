/* Percy Dashboard — permission rules, custom commands and aliases.
   Expects GUILD_ID, CC_DATA, ROLES_DATA, CHANNELS_DATA, window.showToast and PercyConfirm.
   Everything here is an action (add / save / remove), not an edit to a settings form, so
   nothing registers with PercyUnsaved. Percy's 400 message is shown as-is. */

(function() {
    'use strict';

    var base = '/dashboard/guild/' + GUILD_ID;

    async function api(method, url, body) {
        try {
            var opts = { method: method, headers: { 'Accept': 'application/json' } };
            if (body !== undefined) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(body);
            }
            var resp = await fetch(url, opts);
            return await resp.json();
        } catch (e) {
            return { ok: false, error: 'Network error.' };
        }
    }

    function done(msg) {
        showToast('success', msg);
        setTimeout(function() { location.reload(); }, 1200);
    }

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function showError(id, message) {
        var node = document.getElementById(id);
        node.textContent = message || '';
        node.hidden = !message;
    }

    function bindModal(modal, cancelId) {
        document.getElementById(cancelId).addEventListener('click', function() { modal.hidden = true; });
        modal.addEventListener('click', function(e) { if (e.target === modal) modal.hidden = true; });
    }

    // ── Permission rules ────────────────────────────────────────────────
    var ruleModal = document.getElementById('rule-modal');
    var ruleType = document.getElementById('rule-type');
    var ruleInput = document.getElementById('rule-target');
    var ruleSelect = document.getElementById('rule-target-select');
    var ruleAllow = true;

    function fillTargets() {
        var type = ruleType.value;
        var wrap = document.getElementById('rule-target-wrap');
        wrap.hidden = (type === 'guild');
        ruleInput.hidden = (type !== 'user');
        memberSearch.hidden = (type !== 'user');
        memberResults.innerHTML = '';
        memberSearch.value = '';
        ruleSelect.hidden = (type === 'user' || type === 'guild');
        ruleInput.value = '';
        ruleInput.placeholder = 'or paste a Discord user ID';
        document.getElementById('rule-target-hint').textContent =
            type === 'user' ? 'Search by name, or paste an ID.' : '';
        document.getElementById('rule-target-label').textContent =
            { user: 'Member ID', role: 'Role', channel: 'Channel', category: 'Category' }[type] || 'Target';
        ruleSelect.innerHTML = '';
        var items = [];
        if (type === 'role') items = ROLES_DATA.map(function(r) { return [r.id, r.name]; });
        if (type === 'channel') {
            items = CHANNELS_DATA.filter(function(c) { return c.type !== 'category'; })
                .map(function(c) { return [c.id, '#' + c.name]; });
        }
        if (type === 'category') {
            items = CHANNELS_DATA.filter(function(c) { return c.type === 'category'; })
                .map(function(c) { return [c.id, c.name]; });
        }
        items.forEach(function(it) {
            var opt = el('option', null, it[1]);
            opt.value = it[0];
            ruleSelect.appendChild(opt);
        });
    }

    // Member search: debounced, fills the ID input from the chosen result.
    var memberSearch = document.getElementById('rule-member-search');
    var memberResults = document.getElementById('rule-member-results');
    var searchTimer = null;
    memberSearch.addEventListener('input', function() {
        clearTimeout(searchTimer);
        var q = memberSearch.value.trim();
        memberResults.innerHTML = '';
        if (!q) return;
        searchTimer = setTimeout(async function() {
            try {
                var resp = await fetch(base + '/members.json?limit=8&search=' + encodeURIComponent(q));
                var data = await resp.json();
                memberResults.innerHTML = '';
                (data.members || []).forEach(function(m) {
                    var btn = el('button', 'button small', m.display_name + ' (' + m.name + ')');
                    btn.type = 'button';
                    btn.addEventListener('click', function() {
                        ruleInput.value = m.id;
                        memberSearch.value = m.display_name;
                        memberResults.innerHTML = '';
                    });
                    memberResults.appendChild(btn);
                });
            } catch (e) { /* the ID box still works */ }
        }, 250);
    });

    function setAllow(allow) {
        ruleAllow = allow;
        ruleModal.querySelectorAll('[data-allow]').forEach(function(btn) {
            var on = (btn.getAttribute('data-allow') === '1') === allow;
            btn.className = 'cmd-mode-btn' + (on ? (allow ? ' active-enabled' : ' active-disabled') : '');
        });
    }

    document.getElementById('add-rule-btn').addEventListener('click', function() {
        document.getElementById('rule-subject').value = '';
        ruleType.value = 'user';
        fillTargets();
        setAllow(true);
        showError('rule-error', '');
        ruleModal.hidden = false;
        document.getElementById('rule-subject').focus();
    });
    bindModal(ruleModal, 'rule-cancel');
    ruleType.addEventListener('change', fillTargets);
    ruleModal.querySelectorAll('[data-allow]').forEach(function(btn) {
        btn.addEventListener('click', function() { setAllow(btn.getAttribute('data-allow') === '1'); });
    });

    document.getElementById('rule-save').addEventListener('click', async function() {
        var type = ruleType.value;
        if (type === 'user' && !/^\d+$/.test(ruleInput.value.trim())) {
            showError('rule-error', 'Pick a member from the search, or enter a numeric user ID.');
            return;
        }
        var target = type === 'guild' ? GUILD_ID : (type === 'user' ? ruleInput.value.trim() : ruleSelect.value);
        var data = await api('PUT', base + '/command-rules', {
            subject: document.getElementById('rule-subject').value.trim(),
            target_type: type,
            target_id: target,
            allow: ruleAllow,
        });
        if (data.ok) done('Rule saved.');
        else showError('rule-error', data.error || 'Could not save the rule.');
    });

    document.querySelectorAll('.rule-remove').forEach(function(btn) {
        btn.addEventListener('click', async function() {
            var row = btn.closest('tr');
            var ok = await PercyConfirm.ask({
                title: 'Remove this rule?',
                consequence: 'The command goes back to following its normal permissions for this target.',
                undo: 'Add the rule again.',
                confirmLabel: 'Remove rule',
                danger: true,
            });
            if (!ok) return;
            var q = new URLSearchParams({
                subject: row.dataset.subject, target_type: row.dataset.type, target_id: row.dataset.target,
            });
            var data = await api('DELETE', base + '/command-rules?' + q.toString());
            if (data.ok) done('Rule removed.');
            else showToast('error', data.error || 'Could not remove the rule.');
        });
    });

    // ── Aliases ─────────────────────────────────────────────────────────
    document.getElementById('alias-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        var name = document.getElementById('alias-name').value.trim();
        var data = await api('PUT', base + '/aliases/' + encodeURIComponent(name), {
            expansion: document.getElementById('alias-exp').value.trim(),
        });
        if (data.ok) done('Alias saved.');
        else showToast('error', data.error || 'Could not save the alias.');
    });

    document.querySelectorAll('.alias-remove').forEach(function(btn) {
        btn.addEventListener('click', async function() {
            var alias = btn.closest('tr').dataset.alias;
            var ok = await PercyConfirm.ask({
                title: 'Remove the alias "' + alias + '"?',
                consequence: 'Typing it will no longer run anything.',
                undo: 'Add it again.',
                confirmLabel: 'Remove alias',
                danger: true,
            });
            if (!ok) return;
            var data = await api('DELETE', base + '/aliases/' + encodeURIComponent(alias));
            if (data.ok) done('Alias removed.');
            else showToast('error', data.error || 'Could not remove the alias.');
        });
    });

    // ── Custom commands ─────────────────────────────────────────────────
    // Bits can exceed 2^31, so masks are BigInt throughout.
    var PERMISSIONS = [
        ['administrator', 'Administrator', 8n], ['manage_guild', 'Manage Server', 32n],
        ['manage_roles', 'Manage Roles', 268435456n], ['manage_channels', 'Manage Channels', 16n],
        ['kick_members', 'Kick Members', 2n], ['ban_members', 'Ban Members', 4n],
        ['moderate_members', 'Timeout Members', 1099511627776n], ['manage_messages', 'Manage Messages', 8192n],
        ['manage_nicknames', 'Manage Nicknames', 134217728n], ['manage_threads', 'Manage Threads', 17179869184n],
        ['manage_webhooks', 'Manage Webhooks', 536870912n], ['mention_everyone', 'Mention Everyone', 131072n],
        ['view_audit_log', 'View Audit Log', 128n], ['manage_events', 'Manage Events', 8589934592n],
    ];
    var KNOWN_BITS = PERMISSIONS.reduce(function(acc, p) { return acc | p[2]; }, 0n);

    var ccModal = document.getElementById('cc-modal');
    var editing = null;      // the entry being edited, or null for a new command
    var ccRoles = [];        // role id strings
    var extraBits = 0n;      // permission bits the picker has no checkbox for; kept on save

    var grid = document.getElementById('cc-perm-grid');
    PERMISSIONS.forEach(function(p) {
        var label = el('label', 'cmd-perm-row');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = p[0];
        label.appendChild(cb);
        label.appendChild(el('span', null, p[1]));
        grid.appendChild(label);
    });

    function addResponseRow(text) {
        var row = el('div', 'cc-response-row');
        var ta = document.createElement('textarea');
        ta.className = 'config-input';
        ta.maxLength = 2000;
        ta.value = text || '';
        ta.setAttribute('aria-label', 'Response');
        var rm = el('button', 'button small danger', 'Remove');
        rm.type = 'button';
        rm.addEventListener('click', function() {
            var wrap = document.getElementById('cc-responses');
            if (wrap.children.length > 1) row.remove();
            else ta.value = '';
        });
        row.appendChild(ta);
        row.appendChild(rm);
        document.getElementById('cc-responses').appendChild(row);
    }
    document.getElementById('cc-add-response').addEventListener('click', function() { addResponseRow(''); });

    function renderRoleChips() {
        var wrap = document.getElementById('cc-role-chips');
        wrap.innerHTML = '';
        ccRoles.forEach(function(id) {
            var role = ROLES_DATA.filter(function(r) { return r.id === id; })[0];
            var chip = el('span', 'cmd-role-chip', role ? role.name : id);
            var x = el('button', 'cmd-role-chip-x', '×');
            x.type = 'button';
            x.setAttribute('aria-label', 'Remove role');
            x.addEventListener('click', function() {
                ccRoles = ccRoles.filter(function(r) { return r !== id; });
                renderRoleChips();
            });
            chip.appendChild(x);
            wrap.appendChild(chip);
        });
    }
    document.getElementById('cc-role-select').addEventListener('change', function(e) {
        var id = e.target.value;
        if (id && ccRoles.indexOf(id) < 0) ccRoles.push(id);
        e.target.value = '';
        renderRoleChips();
    });

    function openCc(entry) {
        editing = entry;
        document.getElementById('cc-title').textContent = entry ? 'Edit ' + entry.name : 'New custom command';
        document.getElementById('cc-name').value = entry ? entry.name : '';
        document.getElementById('cc-sig').value = entry ? entry.signature : '';
        document.getElementById('cc-aliases').value = entry ? entry.aliases.join(', ') : '';
        document.getElementById('cc-responses').innerHTML = '';
        (entry && entry.responses.length ? entry.responses : ['']).forEach(addResponseRow);
        var mask = entry && entry.required_permissions ? BigInt(entry.required_permissions) : 0n;
        extraBits = mask & ~KNOWN_BITS;
        grid.querySelectorAll('input').forEach(function(cb) {
            var p = PERMISSIONS.filter(function(x) { return x[0] === cb.value; })[0];
            cb.checked = (mask & p[2]) === p[2];
        });
        ccRoles = entry ? entry.allowed_role_ids.slice() : [];
        renderRoleChips();
        document.getElementById('cc-rate').value = entry && entry.cooldown_rate ? entry.cooldown_rate : '';
        document.getElementById('cc-per').value = entry && entry.cooldown_per ? entry.cooldown_per : '';
        document.getElementById('cc-delete').checked = !!(entry && entry.delete_invocation);
        showError('cc-error', '');
        ccModal.hidden = false;
        document.getElementById('cc-name').focus();
    }

    document.getElementById('add-cc-btn').addEventListener('click', function() { openCc(null); });
    bindModal(ccModal, 'cc-cancel');

    document.querySelectorAll('.cc-edit').forEach(function(btn) {
        btn.addEventListener('click', function() { openCc(CC_DATA[Number(btn.closest('tr').dataset.idx)]); });
    });

    document.querySelectorAll('.cc-delete').forEach(function(btn) {
        btn.addEventListener('click', async function() {
            var entry = CC_DATA[Number(btn.closest('tr').dataset.idx)];
            var ok = await PercyConfirm.ask({
                title: 'Delete "' + entry.name + '"?',
                consequence: 'The command and its aliases stop working. Rules that name it stay until you remove them.',
                undo: null,
                confirmLabel: 'Delete command',
                danger: true,
            });
            if (!ok) return;
            var data = await api('DELETE', base + '/custom-commands/' + entry.id);
            if (data.ok) done('Command deleted.');
            else showToast('error', data.error || 'Could not delete the command.');
        });
    });

    document.getElementById('cc-save').addEventListener('click', async function() {
        var mask = extraBits;
        grid.querySelectorAll('input:checked').forEach(function(cb) {
            mask |= PERMISSIONS.filter(function(x) { return x[0] === cb.value; })[0][2];
        });
        var rate = document.getElementById('cc-rate').value.trim();
        var per = document.getElementById('cc-per').value.trim();
        var responses = Array.prototype.map.call(
            document.querySelectorAll('#cc-responses textarea'), function(t) { return t.value; }
        ).filter(function(t) { return t.trim(); });
        var body = {
            name: document.getElementById('cc-name').value.trim(),
            signature: document.getElementById('cc-sig').value.trim(),
            responses: responses,
            aliases: document.getElementById('cc-aliases').value.split(',')
                .map(function(a) { return a.trim(); }).filter(Boolean),
            required_permissions: mask ? mask.toString() : null,
            allowed_role_ids: ccRoles,
            cooldown_rate: rate ? Number(rate) : null,
            cooldown_per: per ? Number(per) : null,
            delete_invocation: document.getElementById('cc-delete').checked,
        };
        var data = editing
            ? await api('PATCH', base + '/custom-commands/' + editing.id, body)
            : await api('POST', base + '/custom-commands', body);
        if (data.ok) done('Command saved.');
        else showError('cc-error', data.error || 'Could not save the command.');
    });
})();
