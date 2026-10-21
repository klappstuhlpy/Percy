/* Percy Dashboard — shared helpers loaded on every dashboard page.
   Provides the global toast notifier and the nav "Features"/"Browse"
   dropdown behaviour that every page's header relies on. */

(function() {
    // -- Toast notifications ---------------------------------------------------
    // Single shared implementation; page scripts call window.showToast(...).
    if (typeof window.showToast === 'undefined') {
        // options.action = { label, onClick } adds a button (e.g. Undo); clicking it runs
        // onClick and dismisses the toast. A toast with an action stays 8 s.
        window.showToast = function(level, message, options) {
            const action = options && options.action;
            const toast = document.createElement('div');
            toast.className = 'toast toast-' + level;
            toast.textContent = message;
            let timer;
            const dismiss = () => {
                clearTimeout(timer);
                toast.classList.remove('visible');
                setTimeout(() => toast.remove(), 300);
            };
            if (action) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'toast-action';
                btn.textContent = action.label;
                btn.addEventListener('click', () => {
                    dismiss();
                    action.onClick();
                });
                toast.appendChild(btn);
            }
            document.body.appendChild(toast);
            requestAnimationFrame(() => toast.classList.add('visible'));
            // advice needs reading time, and so does a chance to undo
            timer = setTimeout(dismiss, level === 'warning' || action ? 8000 : 3000);
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
