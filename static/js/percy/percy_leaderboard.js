(function () {
    // Vanity form (JSON submit)
    var form = document.getElementById('vanity-form');
    if (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var slug = document.getElementById('vanity-slug').value.trim();
            if (!slug) return;
            fetch('/lb/' + GUILD_ID + '/vanity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ slug: slug })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.ok) {
                        window.showToast && window.showToast('success', data.message);
                        setTimeout(function () { location.reload(); }, 800);
                    } else {
                        window.showToast && window.showToast('error', data.error || 'Failed');
                    }
                })
                .catch(function () {
                    window.showToast && window.showToast('error', 'Network error');
                });
        });
    }

    // Vanity delete button
    var deleteBtn = document.getElementById('vanity-delete-btn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', function () {
            if (!confirm('Remove your vanity URL?')) return;
            fetch('/lb/' + GUILD_ID + '/vanity', {
                method: 'DELETE',
                headers: { 'Accept': 'application/json' }
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.ok) {
                        window.showToast && window.showToast('success', data.message);
                        setTimeout(function () { location.reload(); }, 800);
                    } else {
                        window.showToast && window.showToast('error', data.error || 'Failed');
                    }
                })
                .catch(function () {
                    window.showToast && window.showToast('error', 'Network error');
                });
        });
    }
})();
