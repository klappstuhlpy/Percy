(function () {
    var toggle = document.getElementById('percy-mobile-toggle');
    var links = document.querySelector('.percy-nav-links');
    if (toggle && links) {
        toggle.addEventListener('click', function () {
            var expanded = this.getAttribute('aria-expanded') === 'true';
            this.setAttribute('aria-expanded', String(!expanded));
            links.classList.toggle('open', !expanded);
        });
    }

    // On the percy subdomain, rewrite links that belong on the apex domain.
    if (window.location.hostname.indexOf('percy.') === 0) {
        var apex = window.location.protocol + '//' + window.location.hostname.replace('percy.', '') + (window.location.port ? ':' + window.location.port : '');

        // Account page always lives on the root site.
        document.querySelectorAll('a[href="/account"]').forEach(function (a) {
            a.href = apex + '/account';
        });

        // Auth links: in production, redirect through the apex (shared cookie).
        // On localhost, keep them local (independent sessions per host).
        if (window.location.hostname !== 'percy.localhost') {
            var percyDash = window.location.origin + '/dashboard';
            var nextParam = 'next=' + encodeURIComponent(percyDash);
            document.querySelectorAll('a[href*="/login"]').forEach(function (a) {
                a.href = apex + '/login?' + nextParam;
            });
            document.querySelectorAll('a[href*="/signup"]').forEach(function (a) {
                a.href = apex + '/signup?' + nextParam;
            });
            document.querySelectorAll('a[href*="/logout"]').forEach(function (a) {
                a.href = apex + '/logout?next=' + encodeURIComponent(window.location.origin + '/');
            });
        }
    }

    // Fetch bot version and display in the nav brand.
    var versionEl = document.getElementById('percy-version');
    if (versionEl) {
        fetch('/dashboard/bot-version')
            .then(function (r) { return r.json(); })
            .then(function (d) { if (d.version) versionEl.textContent = 'v' + d.version; })
            .catch(function () {});
    }

    var menu = document.getElementById('account-menu');
    if (menu) {
        var trigger = menu.querySelector('.percy-account-trigger');
        var dropdown = menu.querySelector('.percy-account-dropdown');
        if (trigger && dropdown) {
            trigger.addEventListener('click', function (e) {
                e.stopPropagation();
                var expanded = this.getAttribute('aria-expanded') === 'true';
                this.setAttribute('aria-expanded', String(!expanded));
                dropdown.classList.toggle('open', !expanded);
            });
            document.addEventListener('click', function () {
                trigger.setAttribute('aria-expanded', 'false');
                dropdown.classList.remove('open');
            });
        }
    }
})();
