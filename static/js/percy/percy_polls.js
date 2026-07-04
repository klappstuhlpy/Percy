/* Percy Dashboard — Polls (browse / create / edit / end).
   openCreatePoll, openEditPoll, endPoll, closeEditPoll and closeCreatePoll
   are global so the inline onclick handlers in the table can reach them.
   Expects GUILD_ID, POLLS_DATA and window.showToast (percy_common.js). */

let editingPollId = null;

function openEditPoll(pollId) {
    const poll = POLLS_DATA.find(p => p.id === pollId);
    if (!poll) return;

    editingPollId = pollId;
    document.getElementById('edit-poll-question').value = poll.question || '';
    document.getElementById('edit-poll-description').value = poll.description || '';
    document.getElementById('edit-poll-image').value = poll.image_url || '';
    document.getElementById('edit-poll-color').value = poll.color || '';

    const optionsContainer = document.getElementById('edit-poll-options');
    optionsContainer.innerHTML = '';

    const opts = poll.options || [];
    const count = Math.max(opts.length, 2);
    for (let i = 0; i < Math.min(count, 8); i++) {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'config-input';
        input.placeholder = 'Option ' + (i + 1);
        input.value = opts[i] || '';
        input.dataset.index = i;
        optionsContainer.appendChild(input);
    }

    // Add button for more options (up to 8)
    if (count < 8) {
        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'button small';
        addBtn.textContent = '+ Add option';
        addBtn.style.alignSelf = 'flex-start';
        addBtn.style.marginTop = '0.25rem';
        addBtn.addEventListener('click', () => {
            const current = optionsContainer.querySelectorAll('input').length;
            if (current >= 8) return;
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'config-input';
            input.placeholder = 'Option ' + (current + 1);
            input.dataset.index = current;
            optionsContainer.insertBefore(input, addBtn);
            if (current + 1 >= 8) addBtn.remove();
        });
        optionsContainer.appendChild(addBtn);
    }

    document.getElementById('poll-edit-modal').hidden = false;
}

function closeEditPoll() {
    document.getElementById('poll-edit-modal').hidden = true;
    editingPollId = null;
}

document.getElementById('poll-save-btn').addEventListener('click', async () => {
    if (!editingPollId) return;

    const question = document.getElementById('edit-poll-question').value.trim();
    const description = document.getElementById('edit-poll-description').value.trim();
    const image_url = document.getElementById('edit-poll-image').value.trim();
    const color = document.getElementById('edit-poll-color').value.trim();

    const optInputs = document.getElementById('edit-poll-options').querySelectorAll('input');
    const options = [];
    optInputs.forEach(input => {
        const v = input.value.trim();
        if (v) options.push(v);
    });

    if (!question) {
        showToast('error', 'Question is required.');
        return;
    }
    if (options.length < 2) {
        showToast('error', 'At least 2 options are required.');
        return;
    }

    const body = { question, description, image_url, color, options };

    const btn = document.getElementById('poll-save-btn');
    btn.disabled = true;

    try {
        const resp = await fetch(`/dashboard/guild/${GUILD_ID}/polls/${editingPollId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.ok) {
            showToast('success', 'Poll updated successfully.');
            closeEditPoll();
            setTimeout(() => location.reload(), 500);
        } else {
            showToast('error', data.error || 'Failed to update poll.');
        }
    } catch {
        showToast('error', 'Network error.');
    } finally {
        btn.disabled = false;
    }
});

async function endPoll(pollId, btn) {
    if (!confirm('Are you sure you want to end this poll? This cannot be undone.')) return;

    btn.disabled = true;
    try {
        const resp = await fetch(`/dashboard/guild/${GUILD_ID}/polls/${pollId}/end`, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
        });
        const data = await resp.json();
        if (data.ok) {
            showToast('success', 'Poll ended successfully.');
            setTimeout(() => location.reload(), 500);
        } else {
            showToast('error', data.error || 'Failed to end poll.');
        }
    } catch {
        showToast('error', 'Network error.');
    } finally {
        btn.disabled = false;
    }
}

// Close modal on overlay click
document.getElementById('poll-edit-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeEditPoll();
});

// ─── Create Poll ──────────────────────────────────────────────────────
function buildCreateOptionInputs() {
    const container = document.getElementById('create-poll-options');
    container.innerHTML = '';
    for (let i = 0; i < 2; i++) {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'config-input';
        input.placeholder = 'Option ' + (i + 1);
        container.appendChild(input);
    }
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'button small';
    addBtn.textContent = '+ Add option';
    addBtn.style.alignSelf = 'flex-start';
    addBtn.style.marginTop = '0.25rem';
    addBtn.addEventListener('click', () => {
        const current = container.querySelectorAll('input').length;
        if (current >= 8) return;
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'config-input';
        input.placeholder = 'Option ' + (current + 1);
        container.insertBefore(input, addBtn);
        if (current + 1 >= 8) addBtn.remove();
    });
    container.appendChild(addBtn);
}

// Banner image source toggle (URL vs file upload — mutually exclusive)
function selectedImageSource() {
    const checked = document.querySelector('input[name="create-poll-image-source"]:checked');
    return checked ? checked.value : 'url';
}

function updateImageSourceUI() {
    const useFile = selectedImageSource() === 'file';
    const urlInput = document.getElementById('create-poll-image');
    const fileInput = document.getElementById('create-poll-image-file');
    urlInput.hidden = useFile;
    fileInput.hidden = !useFile;
    // Clear the unused input so only one source is ever submitted.
    if (useFile) urlInput.value = '';
    else fileInput.value = '';
}

document.querySelectorAll('input[name="create-poll-image-source"]').forEach(radio => {
    radio.addEventListener('change', updateImageSourceUI);
});

function openCreatePoll() {
    document.getElementById('create-poll-question').value = '';
    document.getElementById('create-poll-description').value = '';
    document.getElementById('create-poll-channel').value = '';
    document.getElementById('create-poll-thread').value = '';
    document.getElementById('create-poll-image').value = '';
    document.getElementById('create-poll-image-file').value = '';
    document.getElementById('create-poll-color').value = '';
    document.getElementById('create-poll-duration-amount').value = '1';
    document.getElementById('create-poll-duration-unit').value = '86400';
    const urlRadio = document.querySelector('input[name="create-poll-image-source"][value="url"]');
    if (urlRadio) urlRadio.checked = true;
    updateImageSourceUI();
    buildCreateOptionInputs();
    document.getElementById('poll-create-modal').hidden = false;
}

function closeCreatePoll() {
    document.getElementById('poll-create-modal').hidden = true;
}

// Uploads the chosen banner file to the image host and returns its public URL.
async function uploadPollBanner(file) {
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetch(`/dashboard/guild/${GUILD_ID}/polls/image`, {
        method: 'POST',
        headers: { 'Accept': 'application/json' },
        body: fd,
    });
    const data = await resp.json();
    if (!data.ok || !data.url) throw new Error(data.error || 'Image upload failed.');
    return data.url;
}

document.getElementById('poll-create-btn').addEventListener('click', async () => {
    const question = document.getElementById('create-poll-question').value.trim();
    const description = document.getElementById('create-poll-description').value.trim();
    const channel_id = document.getElementById('create-poll-channel').value;
    const thread_question = document.getElementById('create-poll-thread').value.trim();
    const color = document.getElementById('create-poll-color').value.trim();
    const amount = parseInt(document.getElementById('create-poll-duration-amount').value, 10);
    const unit = parseInt(document.getElementById('create-poll-duration-unit').value, 10);

    const useFile = selectedImageSource() === 'file';
    const urlValue = document.getElementById('create-poll-image').value.trim();
    const fileInput = document.getElementById('create-poll-image-file');
    const bannerFile = fileInput.files && fileInput.files[0];

    const options = [];
    document.getElementById('create-poll-options').querySelectorAll('input').forEach(input => {
        const v = input.value.trim();
        if (v) options.push(v);
    });

    if (!question) { showToast('error', 'Question is required.'); return; }
    if (options.length < 2) { showToast('error', 'At least 2 options are required.'); return; }
    if (!Number.isFinite(amount) || amount < 1) { showToast('error', 'Enter a valid duration.'); return; }
    if (color && !/^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/.test(color)) {
        showToast('error', 'Enter a valid hex color (e.g. #ff5733).');
        return;
    }

    const btn = document.getElementById('poll-create-btn');
    btn.disabled = true;
    try {
        // Resolve the banner: either the uploaded file (turned into a hosted URL)
        // or the pasted URL — never both.
        let image_url = '';
        if (useFile && bannerFile) {
            showToast('success', 'Uploading banner…');
            image_url = await uploadPollBanner(bannerFile);
        } else if (!useFile) {
            image_url = urlValue;
        }

        const body = {
            question,
            description,
            options,
            image_url,
            color,
            duration_seconds: amount * unit,
        };
        if (channel_id) body.channel_id = channel_id;
        if (thread_question) body.thread_question = thread_question;

        const resp = await fetch(`/dashboard/guild/${GUILD_ID}/polls`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.ok) {
            showToast('success', 'Poll created successfully.');
            closeCreatePoll();
            setTimeout(() => location.reload(), 500);
        } else {
            showToast('error', data.error || 'Failed to create poll.');
        }
    } catch (e) {
        showToast('error', (e && e.message) || 'Network error.');
    } finally {
        btn.disabled = false;
    }
});

document.getElementById('poll-create-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeCreatePoll();
});

// ─── Poll Settings form (async submit) ────────────────────────────────
(function () {
    const form = document.getElementById('poll-config-form');
    if (!form) return;
    const snapshot = new FormData(form);

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = form.querySelector('button[type="submit"]');
        if (btn) btn.disabled = true;
        try {
            const resp = await fetch(form.action, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: new URLSearchParams(new FormData(form)),
            });
            const data = await resp.json();
            if (data.ok) {
                showToast('success', data.message || 'Settings saved.');
            } else {
                showToast('error', data.error || 'Failed to save settings.');
            }
        } catch {
            showToast('error', 'Network error. Please try again.');
        } finally {
            if (btn) btn.disabled = false;
        }
    });

    const cancel = document.getElementById('poll-config-cancel');
    if (cancel) {
        cancel.addEventListener('click', () => {
            for (const el of form.elements) {
                if (!el.name || el.name.startsWith('_')) continue;
                el.value = snapshot.get(el.name) || '';
            }
            showToast('success', 'Changes discarded.');
        });
    }
})();
