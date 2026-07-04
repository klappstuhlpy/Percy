/* Percy Dashboard — guild Moderation tab (ignore list, lockdowns, audit-log
   event flags). Loaded alongside percy_dashboard.js on the guild config page.
   Expects GUILD_ID and window.showToast (percy_common.js). */

(function() {
    function toast(level, msg) { (window.showToast || function(){})(level, msg); }

    async function postJSON(url, body) {
        var resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body),
        });
        return resp.json();
    }

    // -- Ignored entities --
    var ignoreAdd = document.getElementById('ignore-add-btn');
    if (ignoreAdd) {
        ignoreAdd.addEventListener('click', async function() {
            var input = document.getElementById('ignore-id-input');
            var sel = document.getElementById('ignore-select');
            var id = (input.value.trim() || sel.value || '').trim();
            if (!/^\d{15,20}$/.test(id)) { toast('error', 'Select an entity or paste a valid Discord ID.'); return; }
            ignoreAdd.disabled = true;
            try {
                var data = await postJSON('/dashboard/guild/' + GUILD_ID + '/moderation/ignore', { action: 'add', entity_id: id });
                if (data.ok) { toast('success', 'Added to ignore list.'); setTimeout(function() { location.reload(); }, 400); }
                else { toast('error', data.error || 'Failed.'); ignoreAdd.disabled = false; }
            } catch (e) { toast('error', 'Network error'); ignoreAdd.disabled = false; }
        });
    }

    function bindRemove(selector, url, rowSel, tbodyId, emptyId, wrapId, key) {
        document.querySelectorAll(selector).forEach(function(btn) {
            btn.addEventListener('click', async function() {
                var id = btn.getAttribute('data-' + key);
                btn.disabled = true;
                var payload = {};
                if (url.indexOf('/ignore') !== -1) { payload = { action: 'remove', entity_id: id }; }
                else { payload = { channel_ids: [id] }; }
                try {
                    var data = await postJSON(url, payload);
                    if (data.ok) {
                        var row = document.querySelector(rowSel.replace('{id}', id));
                        if (row) row.remove();
                        var tbody = document.getElementById(tbodyId);
                        if (tbody && tbody.children.length === 0) {
                            var wrap = document.getElementById(wrapId); if (wrap) wrap.hidden = true;
                            var empty = document.getElementById(emptyId); if (empty) empty.hidden = false;
                        }
                        toast('success', 'Done.');
                    } else { toast('error', data.error || 'Failed.'); btn.disabled = false; }
                } catch (e) { toast('error', 'Network error'); btn.disabled = false; }
            });
        });
    }

    bindRemove('.ignore-remove-btn', '/dashboard/guild/' + GUILD_ID + '/moderation/ignore',
        '#ignore-tbody tr[data-entity-id="{id}"]', 'ignore-tbody', 'ignore-empty', 'ignore-table-wrap', 'entity-id');
    bindRemove('.lockdown-unlock-btn', '/dashboard/guild/' + GUILD_ID + '/lockdowns/unlock',
        '#lockdown-tbody tr[data-channel-id="{id}"]', 'lockdown-tbody', 'lockdown-empty', 'lockdown-table-wrap', 'channel-id');

    // -- Lockdown (lock selected) --
    var lockBtn = document.getElementById('lockdown-lock-btn');
    if (lockBtn) {
        lockBtn.addEventListener('click', async function() {
            var sel = document.getElementById('lockdown-select');
            var ids = Array.prototype.map.call(sel.selectedOptions, function(o) { return o.value; });
            if (!ids.length) { toast('error', 'Select at least one channel.'); return; }
            if (!confirm('Lock ' + ids.length + ' channel(s)? @everyone will not be able to send messages.')) return;
            lockBtn.disabled = true;
            try {
                var data = await postJSON('/dashboard/guild/' + GUILD_ID + '/lockdowns/lock', { channel_ids: ids });
                if (data.ok) { toast('success', 'Channels locked.'); setTimeout(function() { location.reload(); }, 400); }
                else { toast('error', data.error || 'Failed.'); lockBtn.disabled = false; }
            } catch (e) { toast('error', 'Network error'); lockBtn.disabled = false; }
        });
    }

    // -- Audit Log Flags --
    var auditGrid = document.getElementById('audit-flags-grid');
    if (auditGrid) {
        async function saveAuditFlags(flags) {
            try {
                var resp = await fetch('/dashboard/guild/' + GUILD_ID + '/audit-log-flags', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(flags),
                });
                var data = await resp.json();
                if (data.ok) toast('success', 'Audit log events updated.');
                else toast('error', data.error || 'Failed to update.');
            } catch (e) { toast('error', 'Network error'); }
        }

        auditGrid.addEventListener('change', function(e) {
            if (e.target.tagName !== 'INPUT') return;
            var flags = {};
            flags[e.target.dataset.flag] = e.target.checked;
            saveAuditFlags(flags);
        });

        var allBtn = document.getElementById('audit-flags-all');
        var noneBtn = document.getElementById('audit-flags-none');
        if (allBtn) {
            allBtn.addEventListener('click', function() {
                var flags = {};
                auditGrid.querySelectorAll('input[data-flag]').forEach(function(cb) {
                    cb.checked = true;
                    flags[cb.dataset.flag] = true;
                });
                saveAuditFlags(flags);
            });
        }
        if (noneBtn) {
            noneBtn.addEventListener('click', function() {
                var flags = {};
                auditGrid.querySelectorAll('input[data-flag]').forEach(function(cb) {
                    cb.checked = false;
                    flags[cb.dataset.flag] = false;
                });
                saveAuditFlags(flags);
            });
        }
    }

    // ── Bot status feed (General tab) ──────────────────────────────────────
    var statusSubBtn = document.getElementById('status-feed-sub');
    var statusUnsubBtn = document.getElementById('status-feed-unsub');
    var statusSelect = document.getElementById('status-feed-channel');
    var statusFeedUrl = '/dashboard/guild/' + GUILD_ID + '/status-feed';
    if (statusSubBtn && statusSelect) {
        statusSubBtn.addEventListener('click', async function() {
            var channelId = statusSelect.value;
            if (!channelId) { toast('error', 'Select a channel first.'); return; }
            try {
                var data = await postJSON(statusFeedUrl, { channel_id: channelId });
                if (data.ok) { toast('success', 'Subscribed to status feed.'); setTimeout(function(){ location.reload(); }, 400); }
                else { toast('error', data.error || 'Failed.'); }
            } catch (e) { toast('error', 'Network error.'); }
        });
    }
    if (statusUnsubBtn) {
        statusUnsubBtn.addEventListener('click', async function() {
            if (!confirm('Unsubscribe this server from the status feed?')) return;
            try {
                var resp = await fetch(statusFeedUrl, { method: 'DELETE', headers: { 'Accept': 'application/json' } });
                var data = await resp.json();
                if (data.ok) { toast('success', 'Unsubscribed.'); setTimeout(function(){ location.reload(); }, 400); }
                else { toast('error', data.error || 'Failed.'); }
            } catch (e) { toast('error', 'Network error.'); }
        });
    }

    // ── Custom Bot Profile (Custom Bot tab) ───────────────────────────────
    var cbAvatarArea = document.getElementById('cb-avatar-area');
    var cbAvatarFile = document.getElementById('cb-avatar-file');
    var cbBannerArea = document.getElementById('cb-banner-area');
    var cbBannerFile = document.getElementById('cb-banner-file');
    var cbNameInput = document.getElementById('cb-name-input');
    var cbAboutInput = document.getElementById('cb-about-input');
    var cbCharcount = document.getElementById('cb-charcount');
    var cbSaveBtn = document.getElementById('cb-save-btn');
    var cbResetBtn = document.getElementById('cb-reset-btn');

    if (cbAboutInput && cbCharcount) {
        cbAboutInput.addEventListener('input', function() {
            cbCharcount.textContent = cbAboutInput.value.length + '/190';
        });
    }

    var pendingAvatar = null;
    var pendingBanner = null;

    // ── Crop Modal Logic (Cropper.js — Discord-style) ────────────────
    var cropModal = document.getElementById('cb-crop-modal');
    var cropImg = document.getElementById('cb-crop-img');
    var cropZoom = document.getElementById('cb-crop-zoom');
    var cropApply = document.getElementById('cb-crop-apply');
    var cropCancel = document.getElementById('cb-crop-cancel');
    var cropClose = document.getElementById('cb-crop-close');
    var cropReset = document.getElementById('cb-crop-reset');
    var cropReselect = document.getElementById('cb-crop-reselect');
    var cropTitle = document.getElementById('cb-crop-title');
    var cropper = null;
    var cropMode = null;
    var minRatio = 0;   // zoom where the image just covers the crop box (slider = 0)
    var maxRatio = 0;   // most zoomed-in state (slider = 100)
    var lastZoom = 0;   // previous slider value, to detect zoom-out

    function openCropModal(file, mode) {
        cropMode = mode;
        cropTitle.textContent = mode === 'avatar' ? 'Edit Avatar' : 'Edit Banner';
        if (cropZoom) cropZoom.value = 0;

        var url = URL.createObjectURL(file);
        cropImg.src = url;
        cropModal.hidden = false;

        if (cropper) { cropper.destroy(); cropper = null; }

        cropImg.onload = function() {
            cropper = new Cropper(cropImg, {
                aspectRatio: mode === 'avatar' ? 1 : 17 / 6,
                viewMode: 1,
                dragMode: 'none',
                movable: false,     // image stays put — only the crop grid moves
                autoCropArea: 0.68,
                restore: false,
                guides: true,
                center: true,
                highlight: false,
                cropBoxMovable: true,
                cropBoxResizable: false,
                toggleDragModeOnDblclick: false,
                background: false,
                zoomable: true,
                zoomOnWheel: false,   // image is static — only the slider resizes it
                zoomOnTouch: false,
                ready: function() {
                    // Slider 0 = image at its outermost position (edges at the crop border).
                    var img = cropper.getImageData();
                    var box = cropper.getCropBoxData();
                    // *1.001 so the image always fully covers the box despite rounding —
                    // otherwise viewMode:1 snaps the under-covering image into a corner.
                    minRatio = Math.max(box.width / img.naturalWidth, box.height / img.naturalHeight) * 1.001;
                    maxRatio = minRatio * 3;
                    cropper.zoomTo(minRatio);
                    recenterImage();
                    if (cropZoom) cropZoom.value = 0;
                    lastZoom = 0;
                },
            });
        };
    }

    // Center the image (canvas) over the crop box.
    function recenterImage() {
        if (!cropper) return;
        var box = cropper.getCropBoxData();
        var canvas = cropper.getCanvasData();
        cropper.setCanvasData({
            left: box.left + box.width / 2 - canvas.width / 2,
            top: box.top + box.height / 2 - canvas.height / 2,
        });
    }

    function closeCropModal() {
        cropModal.hidden = true;
        if (cropper) { cropper.destroy(); cropper = null; }
        URL.revokeObjectURL(cropImg.src);
        cropImg.src = '';
    }

    // Zoom slider — maps 0-100 between the outermost fit (minRatio) and 3x (maxRatio)
    if (cropZoom) {
        cropZoom.addEventListener('input', function() {
            if (!cropper) return;
            var val = parseFloat(cropZoom.value);
            var ratio = minRatio + (val / 100) * (maxRatio - minRatio);
            cropper.zoomTo(ratio);
            // Zooming out can leave the previously-dragged image off-centre with
            // gaps at the crop border — recenter it over the crop box.
            if (val < lastZoom) recenterImage();
            lastZoom = val;
        });
    }

    if (cropCancel) cropCancel.addEventListener('click', closeCropModal);
    if (cropClose) cropClose.addEventListener('click', closeCropModal);

    if (cropReset) {
        cropReset.addEventListener('click', function() {
            if (!cropper) return;
            cropper.reset();
            cropper.zoomTo(minRatio);
            recenterImage();
            if (cropZoom) cropZoom.value = 0;
            lastZoom = 0;
        });
    }

    if (cropReselect) {
        cropReselect.addEventListener('click', function() {
            var fileInput = cropMode === 'avatar' ? cbAvatarFile : cbBannerFile;
            if (fileInput) fileInput.click();
        });
    }

    if (cropApply) {
        cropApply.addEventListener('click', function() {
            if (!cropper) return;
            var outW, outH;
            if (cropMode === 'avatar') { outW = 1024; outH = 1024; }
            else { outW = 680; outH = 240; }

            var canvas = cropper.getCroppedCanvas({ width: outW, height: outH, imageSmoothingQuality: 'high' });
            var dataUrl = canvas.toDataURL('image/png');
            var b64 = dataUrl.split(',')[1];

            if (cropMode === 'avatar') {
                pendingAvatar = b64;
                var avatarEl = document.getElementById('cb-avatar-img');
                if (avatarEl && avatarEl.tagName === 'IMG') { avatarEl.src = dataUrl; }
                else {
                    var old = cbAvatarArea.querySelector('.cb-avatar-fallback');
                    if (old) old.remove();
                    var img = document.createElement('img');
                    img.src = dataUrl; img.alt = ''; img.className = 'cb-avatar-img'; img.id = 'cb-avatar-img';
                    cbAvatarArea.insertBefore(img, cbAvatarArea.querySelector('.cb-avatar-overlay'));
                }
            } else {
                pendingBanner = b64;
                var bannerEl = document.getElementById('cb-banner-img');
                if (bannerEl && bannerEl.tagName === 'IMG') { bannerEl.src = dataUrl; }
                else {
                    if (bannerEl) bannerEl.remove();
                    var img2 = document.createElement('img');
                    img2.src = dataUrl; img2.alt = ''; img2.className = 'cb-banner-img'; img2.id = 'cb-banner-img';
                    cbBannerArea.insertBefore(img2, cbBannerArea.querySelector('.cb-banner-btn'));
                }
            }

            closeCropModal();
        });
    }

    // ── File upload triggers ─────────────────────────────────────────
    if (cbAvatarArea && cbAvatarFile) {
        cbAvatarArea.addEventListener('click', function() { cbAvatarFile.click(); });
        cbAvatarFile.addEventListener('change', function() {
            var file = cbAvatarFile.files[0];
            if (!file) return;
            openCropModal(file, 'avatar');
            cbAvatarFile.value = '';
        });
    }

    if (cbBannerArea && cbBannerFile) {
        cbBannerArea.addEventListener('click', function() { cbBannerFile.click(); });
        cbBannerFile.addEventListener('change', function() {
            var file = cbBannerFile.files[0];
            if (!file) return;
            openCropModal(file, 'banner');
            cbBannerFile.value = '';
        });
    }

    // ── Save / Reset ──────────────────────────────────────────────────
    if (cbSaveBtn) {
        cbSaveBtn.addEventListener('click', async function() {
            var body = {};
            if (cbNameInput) body.name = cbNameInput.value.trim() || null;
            if (cbAboutInput) body.about_me = cbAboutInput.value.trim() || null;
            if (pendingAvatar) body.avatar = pendingAvatar;
            if (pendingBanner) body.banner = pendingBanner;
            if (!Object.keys(body).length) { toast('info', 'Nothing to save.'); return; }
            cbSaveBtn.disabled = true;
            try {
                var resp = await fetch('/dashboard/guild/' + GUILD_ID + '/custom-bot', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify(body)
                });
                var data = await resp.json();
                if (data.ok) { toast('success', 'Saved'); pendingAvatar = null; pendingBanner = null; }
                else toast('error', data.error || 'Failed to save');
            } catch (ex) { toast('error', 'Network error'); }
            cbSaveBtn.disabled = false;
        });
    }

    if (cbResetBtn) {
        cbResetBtn.addEventListener('click', async function() {
            if (!confirm('Reset the bot profile to defaults?')) return;
            cbResetBtn.disabled = true;
            try {
                var resp = await fetch('/dashboard/guild/' + GUILD_ID + '/custom-bot/reset', {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' }
                });
                var data = await resp.json();
                if (data.ok) { toast('success', 'Profile reset'); setTimeout(function() { location.reload(); }, 600); }
                else toast('error', data.error || 'Failed');
            } catch (ex) { toast('error', 'Network error'); }
            cbResetBtn.disabled = false;
        });
    }
})();
