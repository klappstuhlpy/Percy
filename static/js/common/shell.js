/* App shell behaviour: collapsible nav (remembered per browser), mobile drawer, and dismissal
   of the details-based menus. Everything here is progressive: without it the nav is still a
   plain list of links and the menus open natively. */
(function () {
    'use strict';
    var shell = document.getElementById('shell');
    if (!shell) return;
    var KEY = 'percy:nav-collapsed';
    var collapseBtn = document.getElementById('shell-collapse');
    var burger = document.getElementById('shell-burger');
    var scrim = document.getElementById('shell-scrim');

    function setCollapsed(on) {
        shell.classList.toggle('is-collapsed', on);
        if (collapseBtn) collapseBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
        try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) { /* private window */ }
    }
    try { if (localStorage.getItem(KEY) === '1') setCollapsed(true); } catch (e) { /* ignore */ }
    if (collapseBtn) collapseBtn.addEventListener('click', function () { setCollapsed(!shell.classList.contains('is-collapsed')); });

    function setDrawer(on) {
        shell.classList.toggle('is-drawer', on);
        if (burger) burger.setAttribute('aria-expanded', on ? 'true' : 'false');
    }
    if (burger) burger.addEventListener('click', function () { setDrawer(!shell.classList.contains('is-drawer')); });
    if (scrim) scrim.addEventListener('click', function () { setDrawer(false); });

    document.addEventListener('click', function (e) {
        document.querySelectorAll('details.shell-picker[open], details.shell-me[open]').forEach(function (d) {
            if (!d.contains(e.target)) d.open = false;
        });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        setDrawer(false);
        document.querySelectorAll('details.shell-picker[open], details.shell-me[open]').forEach(function (d) { d.open = false; });
    });
})();
