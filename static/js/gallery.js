/* Guild image gallery — drag & drop upload with a selectable preview, plus
   delete. Expects GUILD_ID and window.showToast (common.js). All user feedback
   goes through toasts; server errors are never dumped raw into the page. */
(function () {
    const guildId = GUILD_ID;
    const form = document.getElementById('gallery-upload-form');
    const dropzone = document.getElementById('gallery-dropzone');
    const input = document.getElementById('gallery-files');
    const preview = document.getElementById('gallery-preview');
    const actions = document.getElementById('gallery-upload-actions');
    const uploadBtn = document.getElementById('gallery-upload-btn');
    const clearBtn = document.getElementById('gallery-clear-btn');
    const countLabel = document.getElementById('gallery-selected-count');

    const toast = (level, msg) => (window.showToast || function () {})(level, msg);

    // Mirror the server's allow-list + 10 MB cap so we can reject bad files
    // before spending bandwidth on them (the server still enforces both).
    const ALLOWED = ['apng', 'png', 'jpg', 'jpeg', 'gif', 'avif', 'webp'];
    const MAX_BYTES = 10 * 1024 * 1024;

    /** @type {File[]} pending selection, shown in the preview until uploaded. */
    let selected = [];

    const extOf = (name) => name.slice((name.lastIndexOf('.') - 1 >>> 0) + 2).toLowerCase();
    const fmtBytes = (b) =>
        b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : b >= 1024 ? Math.round(b / 1024) + ' KB' : b + ' B';
    const plural = (n) => (n === 1 ? '' : 's');

    function addFiles(fileList) {
        let badType = 0;
        let tooBig = 0;
        for (const f of Array.from(fileList)) {
            if (!ALLOWED.includes(extOf(f.name))) {
                badType++;
                continue;
            }
            if (f.size > MAX_BYTES) {
                tooBig++;
                continue;
            }
            if (selected.some((s) => s.name === f.name && s.size === f.size)) continue;
            selected.push(f);
        }
        if (badType) toast('error', `${badType} file${plural(badType)} skipped — unsupported format.`);
        if (tooBig) toast('error', `${tooBig} file${plural(tooBig)} skipped — over 10 MB.`);
        renderPreview();
    }

    function renderPreview() {
        preview.innerHTML = '';
        if (!selected.length) {
            actions.hidden = true;
            return;
        }
        actions.hidden = false;
        countLabel.textContent = `${selected.length} file${plural(selected.length)} ready`;

        selected.forEach((f, i) => {
            const item = document.createElement('div');
            item.className = 'gallery-preview-item';

            const img = document.createElement('img');
            img.className = 'gallery-preview-thumb';
            img.alt = f.name;
            img.src = URL.createObjectURL(f);
            img.addEventListener('load', () => URL.revokeObjectURL(img.src));

            const meta = document.createElement('div');
            meta.className = 'gallery-preview-meta';
            const name = document.createElement('span');
            name.className = 'gallery-preview-name';
            name.textContent = f.name;
            const size = document.createElement('span');
            size.className = 'gallery-preview-size';
            size.textContent = fmtBytes(f.size);
            meta.append(name, size);

            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'gallery-preview-remove';
            remove.setAttribute('aria-label', `Remove ${f.name}`);
            remove.textContent = '✕';
            remove.addEventListener('click', () => {
                selected.splice(i, 1);
                renderPreview();
            });

            item.append(img, meta, remove);
            preview.appendChild(item);
        });
    }

    async function upload() {
        if (!selected.length) {
            toast('error', 'Choose at least one image first.');
            return;
        }
        const fd = new FormData();
        for (const f of selected) fd.append('files', f);

        const original = uploadBtn.textContent;
        uploadBtn.disabled = true;
        uploadBtn.textContent = 'Uploading…';
        dropzone.classList.add('busy');

        try {
            const resp = await fetch(`/dashboard/guild/${guildId}/gallery/upload`, { method: 'POST', body: fd });
            let data = {};
            try {
                data = await resp.json();
            } catch (_) {
                /* non-JSON body — fall through to the generic message */
            }
            if (resp.ok && data.ok) {
                const n = data.total || selected.length;
                toast('success', `Uploaded ${n} image${plural(n)}.`);
                setTimeout(() => location.reload(), 600);
                return;
            }
            toast('error', data.error || 'Upload failed. Please try again.');
        } catch (_) {
            toast('error', 'Network error — upload failed.');
        } finally {
            uploadBtn.disabled = false;
            uploadBtn.textContent = original;
            dropzone.classList.remove('busy');
        }
    }

    // ── Wiring ───────────────────────────────────────────────────────────────
    dropzone.addEventListener('click', () => input.click());
    dropzone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            input.click();
        }
    });

    input.addEventListener('change', () => {
        addFiles(input.files);
        input.value = ''; // allow re-selecting the same file after a remove
    });

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        upload();
    });

    clearBtn.addEventListener('click', () => {
        selected = [];
        renderPreview();
    });

    // Drag highlight on the dropzone. A depth counter avoids flicker as the
    // pointer moves across child elements (each fires dragenter/dragleave).
    let dragDepth = 0;
    dropzone.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragDepth++;
        dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragover', (e) => e.preventDefault());
    dropzone.addEventListener('dragleave', () => {
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) dropzone.classList.remove('dragover');
    });
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dragDepth = 0;
        dropzone.classList.remove('dragover');
        if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });

    // Stop the browser from navigating away if a file is dropped outside the zone.
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => {
        if (!dropzone.contains(e.target)) e.preventDefault();
    });

    // ── Delete ───────────────────────────────────────────────────────────────
    document.querySelectorAll('.gallery-delete').forEach((btn) => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this image from the gallery?')) return;
            const id = btn.dataset.id;
            btn.disabled = true;
            try {
                const resp = await fetch(`/dashboard/guild/${guildId}/gallery/${encodeURIComponent(id)}/delete`, {
                    method: 'POST',
                });
                let data = {};
                try {
                    data = await resp.json();
                } catch (_) {
                    /* ignore */
                }
                if (resp.ok && data.ok) {
                    const tile = document.querySelector(`.gallery-item[data-id="${CSS.escape(id)}"]`);
                    if (tile) tile.remove();
                    toast('success', 'Image deleted.');
                } else {
                    toast('error', data.error || 'Delete failed.');
                    btn.disabled = false;
                }
            } catch (_) {
                toast('error', 'Network error — delete failed.');
                btn.disabled = false;
            }
        });
    });
})();
