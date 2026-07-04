/* Percy Dashboard — shared helpers loaded on every dashboard page.
   Provides the global toast notifier and the nav "Features"/"Browse"
   dropdown behaviour that every page's header relies on. */

(function() {
    // -- Toast notifications ---------------------------------------------------
    // Single shared implementation; page scripts call window.showToast(...).
    if (typeof window.showToast === 'undefined') {
        window.showToast = function(level, message) {
            const toast = document.createElement('div');
            toast.className = 'toast toast-' + level;
            toast.textContent = message;
            document.body.appendChild(toast);
            requestAnimationFrame(() => toast.classList.add('visible'));
            setTimeout(() => {
                toast.classList.remove('visible');
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        };
    }

    // -- Sidebar collapse toggle (mobile) --------------------------------------
    // The left sidebar is always visible on desktop; on narrow viewports it
    // collapses behind a "Menu" button (.dash-sidebar-toggle) that flips the
    // .open class on the sidebar.
    const sidebar = document.getElementById('dash-sidebar');
    const sidebarToggle = sidebar && sidebar.querySelector('.dash-sidebar-toggle');
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function() {
            const open = sidebar.classList.toggle('open');
            sidebarToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    }
})();
