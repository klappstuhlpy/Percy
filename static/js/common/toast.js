/* PercyToast.show(kind, text): one shared toast stack. kind = success | info | warning | error.
   Repeats of the same text within 1.5 s collapse, at most four stay on screen. */
(function () {
    'use strict';
    var SVG = 'http://www.w3.org/2000/svg';
    var ICONS = { success: 'check', error: 'alert', info: 'info', warning: 'alert' };
    var box = null, last = { text: '', at: 0 };

    function icon(name) {
        var s = document.createElementNS(SVG, 'svg');
        s.setAttribute('aria-hidden', 'true');
        var u = document.createElementNS(SVG, 'use');
        u.setAttribute('href', '/static/icons/sprite.svg#i-' + name);
        s.appendChild(u);
        return s;
    }

    function show(kind, text) {
        if (!text) return;
        if (text === last.text && Date.now() - last.at < 1500) return;
        last = { text: text, at: Date.now() };
        kind = ICONS[kind] ? kind : 'info';
        if (!box || !box.isConnected) {
            box = document.createElement('div');
            box.className = 'toast-stack';
            document.body.appendChild(box);
        }
        var el = document.createElement('div');
        el.className = 'toast-card is-' + kind;
        el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        var msg = document.createElement('span');
        msg.className = 'toast-msg';
        msg.textContent = text;
        var x = document.createElement('button');
        x.type = 'button';
        x.className = 'btn btn--icon';
        x.setAttribute('aria-label', 'Dismiss notification');
        x.appendChild(icon('x'));
        el.append(icon(ICONS[kind]), msg, x);
        var timer;
        var ms = kind === 'error' ? 6500 : kind === 'warning' ? 8000 : 3800;
        function arm() { clearTimeout(timer); timer = setTimeout(close, ms); }
        function close() {
            clearTimeout(timer);
            el.classList.add('is-leaving');
            setTimeout(function () { el.remove(); }, 200);
        }
        x.addEventListener('click', close);
        arm();
        // reading time: hovering or focusing the card holds it open
        el.addEventListener('mouseenter', function () { clearTimeout(timer); });
        el.addEventListener('mouseleave', arm);
        el.addEventListener('focusin', function () { clearTimeout(timer); });
        el.addEventListener('focusout', arm);
        box.appendChild(el);
        while (box.children.length > 4) box.firstElementChild.remove();
    }

    window.PercyToast = { show: show };
})();
