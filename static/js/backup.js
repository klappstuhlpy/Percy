/* Percy Dashboard — Backup / restore + shareable templates. Expects GUILD_ID and
   window.showToast (common.js). Import + template-apply always preview (dry run)
   before writing. */

(function() {
    const guildId = GUILD_ID;
    const base = `/dashboard/guild/${guildId}`;
    const jsonHeaders = { 'Content-Type': 'application/json', 'Accept': 'application/json' };

    const fileInput = document.getElementById('import-file');
    const jsonArea = document.getElementById('import-json');
    const previewBtn = document.getElementById('import-preview');
    const applyBtn = document.getElementById('import-apply');
    const resultBox = document.getElementById('import-result');

    let previewedData = null;

    // Load a .json file into the textarea.
    if (fileInput) {
        fileInput.addEventListener('change', () => {
            const file = fileInput.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => { jsonArea.value = reader.result; applyBtn.disabled = true; previewedData = null; };
            reader.readAsText(file);
        });
    }

    // Invalidate a prior preview whenever the blob is edited.
    jsonArea.addEventListener('input', () => { applyBtn.disabled = true; previewedData = null; });

    function parseBlob() {
        const raw = jsonArea.value.trim();
        if (!raw) { showToast('error', 'Paste a backup blob or load a file first.'); return null; }
        try { return JSON.parse(raw); }
        catch { showToast('error', 'That is not valid JSON.'); return null; }
    }

    function renderCounts(obj) {
        // Percy reports either a flat `plan` ({section: n}) or a nested `applied`
        // report ({section: {created, skipped, …}}); render both shapes as a list.
        const parts = Object.entries(obj || {}).map(([k, v]) => {
            if (v && typeof v === 'object') {
                const inner = Object.entries(v).map(([ik, iv]) => `${ik}: ${iv}`).join(', ');
                return `<li><strong>${escapeHtml(k)}</strong> — ${escapeHtml(inner)}</li>`;
            }
            return `<li><strong>${escapeHtml(k)}</strong>: ${escapeHtml(String(v))}</li>`;
        });
        return parts.length ? `<ul>${parts.join('')}</ul>` : '<p>Nothing to apply.</p>';
    }

    async function doImport(data, dryRun) {
        const resp = await fetch(`${base}/backup/import?dry_run=${dryRun}`, {
            method: 'POST', headers: jsonHeaders, body: JSON.stringify({ data }),
        });
        return resp.json();
    }

    previewBtn.addEventListener('click', async () => {
        const data = parseBlob();
        if (!data) return;
        resultBox.innerHTML = '<p class="text-muted">Previewing…</p>';
        try {
            const res = await doImport(data, true);
            if (res.ok === false || res.error) { resultBox.innerHTML = `<p>${escapeHtml(res.error || 'Invalid backup.')}</p>`; return; }
            resultBox.innerHTML = `<p><strong>Preview</strong> — this restore would apply:</p>${renderCounts(res.plan)}`;
            previewedData = data;
            applyBtn.disabled = false;
        } catch { resultBox.innerHTML = '<p>Network error.</p>'; }
    });

    applyBtn.addEventListener('click', async () => {
        if (!previewedData) { showToast('error', 'Preview first.'); return; }
        if (!confirm('Restore this backup into the current server? Existing tags/triggers are skipped, not overwritten.')) return;
        resultBox.innerHTML = '<p class="text-muted">Restoring…</p>';
        try {
            const res = await doImport(previewedData, false);
            if (res.ok === false || res.error) { resultBox.innerHTML = `<p>${escapeHtml(res.error || 'Restore failed.')}</p>`; return; }
            resultBox.innerHTML = `<p><strong>Restored.</strong></p>${renderCounts(res.applied)}`;
            showToast('success', 'Backup restored.');
            applyBtn.disabled = true;
            previewedData = null;
        } catch { resultBox.innerHTML = '<p>Network error.</p>'; }
    });

    // -- Publish template ------------------------------------------------------
    const publishBtn = document.getElementById('tpl-publish');
    if (publishBtn) {
        publishBtn.addEventListener('click', async () => {
            const slug = document.getElementById('tpl_slug').value.trim().toLowerCase();
            const name = document.getElementById('tpl_name').value.trim();
            const description = document.getElementById('tpl_desc').value.trim();
            const pub = document.getElementById('tpl_public').checked;
            if (!slug || !name) { showToast('error', 'Slug and name are required.'); return; }
            try {
                const resp = await fetch(`${base}/templates`, {
                    method: 'POST', headers: jsonHeaders,
                    body: JSON.stringify({ slug, name, description: description || null, public: pub }),
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Template published.'); setTimeout(() => location.reload(), 500); }
                else { showToast('error', data.error || 'Failed to publish.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    }

    // -- Apply / delete template ----------------------------------------------
    async function applyTemplate(slug, dryRun) {
        const resp = await fetch(`${base}/templates/${encodeURIComponent(slug)}/apply?dry_run=${dryRun}`, {
            method: 'POST', headers: { 'Accept': 'application/json' },
        });
        return resp.json();
    }

    document.querySelectorAll('.tpl-apply').forEach(btn => {
        btn.addEventListener('click', async () => {
            const slug = btn.dataset.slug;
            try {
                const preview = await applyTemplate(slug, true);
                if (preview.ok === false || preview.error) { showToast('error', preview.error || 'Template not found.'); return; }
                const summary = Object.entries(preview.plan || {}).map(([k, v]) => `${k}: ${v}`).join(', ') || 'nothing';
                if (!confirm(`Apply template "${slug}" to this server?\n\nWould add — ${summary}\n(existing tags/triggers are skipped)`)) return;
                const res = await applyTemplate(slug, false);
                if (res.ok === false || res.error) { showToast('error', res.error || 'Failed to apply.'); return; }
                showToast('success', 'Template applied.');
                setTimeout(() => location.reload(), 600);
            } catch { showToast('error', 'Network error.'); }
        });
    });

    document.querySelectorAll('.tpl-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
            const slug = btn.dataset.slug;
            if (!confirm(`Delete template "${slug}"?`)) return;
            try {
                const resp = await fetch(`${base}/templates/${encodeURIComponent(slug)}`, {
                    method: 'DELETE', headers: { 'Accept': 'application/json' },
                });
                const data = await resp.json();
                if (data.ok) { showToast('success', 'Deleted.'); setTimeout(() => location.reload(), 400); }
                else { showToast('error', data.error || 'Failed to delete.'); }
            } catch { showToast('error', 'Network error.'); }
        });
    });

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
})();
