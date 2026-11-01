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
            // No action button: the shared PercyToast stack (toast.js) renders it.
            if (!action && window.PercyToast) {
                window.PercyToast.show(level, message);
                return;
            }
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
                    btn.disabled = true; // no second fire during the fade-out
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
})();
