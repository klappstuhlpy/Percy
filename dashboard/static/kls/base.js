/* This file is licensed under AGPL-3.0 */
const main = document.querySelector('main');
const rtf = new Intl.RelativeTimeFormat(undefined, {
    style: 'long', numberic: 'auto',
});

function __parseQuery(query) {
    let chunks = query.split(/([.#])/);
    let classList = [];
    let id = null;
    for(let i = 1; i < chunks.length; i += 2) {
        if(chunks[i] === '.') {
            classList.push(chunks[i + 1]);
        } else if(chunks[i] === '#') {
            id = chunks[i + 1];
        }
    }
    return {classList, tag: chunks[0] || 'div', id};
}

function __create(query) {
    const {classList, tag, id} = __parseQuery(query);
    let el = document.createElement(tag);
    if(id !== null) el.id = id;
    if(classList.length !== 0) el.classList.add(...classList);
    return el;
}

function __setData(el, key, value) {
    if(typeof key === 'object') {
        for(const k in key) {
            __setData(el, k, key[k]);
        }
        return;
    }
    if(value == null) {
        delete el.dataset[key];
    } else{
        el.dataset[key] = value;
    }
}

function __setAttr(el, key, value) {
    if(typeof key === 'object') {
        for(const k in key) {
            __setAttr(el, k, key[k]);
        }
        return;
    }
    const isFunc = typeof value === 'function';
    if(key === 'dataset') {
        __setData(el, value);
    } else if((key in el || isFunc) && (key !== 'list')) {
        el[key] = value;
    } else {
        if(el.className && key === 'class') {
            value = `${el.className} ${value}`;
        }
        if(value == null) {
            el.removeAttribute(key);
        } else {
            el.setAttribute(key, value);
        }
    }
}

function __expandArgs(el, args) {
    for(const arg of args) {
        const type = typeof arg;
        if(type === 'function') {
            arg(el);
        } else if(type === 'string' || type === 'number') {
            el.appendChild(new Text(arg ?? ''));
        } else if(arg && arg.nodeType) {
            el.appendChild(arg);
        } else if(Array.isArray(arg)) {
            __expandArgs(el, arg);
        } else if(type === 'object') {
            __setAttr(el, arg, null);
        }
    }
}

function html(query, ...args) {
    let el = __create(query);
    __expandArgs(el, args);
    return el;
}

function debounced(func, timeout = 300) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => {
            func.apply(this, args)
        }, timeout)
    }
}

function formatRelative(seconds) {
    // Ensure 'seconds' is a valid finite number
    if (!Number.isFinite(seconds)) {
        throw new Error("Invalid input: 'seconds' must be a finite number, not " + seconds);
    }

    // Calculate the relative time difference in seconds
    const dt = Math.round(seconds - Date.now() / 1000);

    // Define cutoff points and units
    const cutoffs = [60, 3600, 86400, 86400 * 7, 86400 * 28, 86400 * 365, Infinity];
    const units = ['second', 'minute', 'hour', 'day', 'week', 'month', 'year'];

    // Find the appropriate unit
    const index = cutoffs.findIndex(v => v > Math.abs(dt));
    if (index === -1) {
        throw new Error("Failed to calculate relative time.");
    }

    // Avoid division by zero
    const divisor = index > 0 ? cutoffs[index - 1] : 1;

    // Format the relative time
    const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
    return rtf.format(Math.floor(dt / divisor), units[index]);
}

function createAlert({content, level = 'info'}) {
    return html('div.alert-flash', {role: 'alert', class: level},
        content ? html('p', content) : null,
        html('button.close', {
            'aria-hidden': 'true',
            type: 'button',
            onclick: closeAlert,
        }));
}

function closeAlert(e) {
    e.preventDefault();
    let d = e.target.parentElement;
    let p = d?.parentElement;
    p?.removeChild(d);
}

function showAlert({ level, content }) {
    const alert = createAlert({ level, content });
    main.insertBefore(alert, main.firstChild);
}

function detectOS(ua) {
    const userAgent = ua || window.navigator.userAgent,
        windowsPlatforms = ['Win32', 'Win64', 'Windows'],
        iosPlatforms = ['iPhone', 'iPad', 'iPod'];

    if (userAgent.indexOf('Macintosh') !== -1) {
        if(navigator.standalone && navigator.maxTouchPoints > 2) {
            return 'iPadOS';
        }
        return 'macOS';
    } else if (iosPlatforms.some(p => userAgent.indexOf(p) !== -1)) {
        return 'iOS';
    } else if (windowsPlatforms.some(p => userAgent.indexOf(p) !== -1)) {
        return 'Windows';
    } else if (userAgent.indexOf('Android') !== -1) {
        return 'Android';
    } else if (userAgent.indexOf('Linux') !== -1) {
        return 'Linux';
    } else {
        return null;
    }
}

function detectBrowser(ua) {
    const userAgent = ua || window.navigator.userAgent;
    let match = userAgent.match(/(opera|chrome|safari|firefox|msie|trident(?=\/))\/?\s*(\d+)/i) || [];
    if(/trident/i.test(match[1])) {
        return 'Internet Explorer';
    }
    if(match[1] === 'Chrome') {
        let inner = userAgent.match(/\b(OPR|Edge)\/(\d+)/);
        if(inner !== null) {
            return inner[1].replace('OPR', 'Opera');
        }
        if(/\bEdg\/\d+/.test(userAgent)) {
            return 'Edge';
        }
        return 'Chrome';
    }
    return match[1];
}

function deviceDescription() {
    const ua = window.navigator.userAgent;
    let browser = detectBrowser(ua);
    let os = detectOS(ua);
    if(!browser && !os) {
        return '';
    }
    browser = browser ?? 'Unknown Browser';
    return !!os ? `${browser} on ${os}` : browser;
}

const defaultAlertHook = (alert) => main.insertBefore(alert, main.firstChild);

async function callApi(url, options, alertHook, blob = false) {
    let resp = await fetch(url, options);
    let hook = alertHook ?? defaultAlertHook;

    if(!resp.ok) {
        let content = `Server responded with status code ${resp.status}`;
        if(resp.headers.get('content-type') === 'application/json') {
            let js = await resp.json();
            content = js.error;
        }
        let alert = createAlert({level: 'error', content});
        hook(alert);
        return null;
    } else {
        if(resp.headers.get('content-type') === 'application/json') {
            return await resp.json();
        } else {
            return blob ? await resp.blob() : await resp.text();
        }
    }
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

const initialSortBy = 'name';
const initialSortOrder = 'ascending';

/* ── Mobile table labels ─────────────────────────────────────────
   On narrow viewports each <td> is stacked and prefixed with its
   column header via CSS `attr(data-th)`. This helper sets data-th
   from the corresponding <th> in the table head so we don't have
   to repeat the label in every template. Re-runs on DOM mutations
   so dynamically-populated tables (e.g. metrics, audit) work too. */
function __labelTableCells(table) {
    const headers = Array.from(table.querySelectorAll(':scope > thead > tr > th'))
        .map(th => th.textContent.trim());
    if (headers.length === 0) return;
    for (const row of table.querySelectorAll(':scope > tbody > tr')) {
        const cells = row.children;
        for (let i = 0; i < cells.length; i++) {
            const cell = cells[i];
            if (cell.tagName !== 'TD') continue;
            if (cell.hasAttribute('data-th')) continue;
            if (cell.hasAttribute('colspan')) continue;
            const label = headers[i];
            if (label) cell.setAttribute('data-th', label);
        }
    }
}

const __tableLabelObservers = new WeakSet();

function __setupTableLabels() {
    const tables = document.querySelectorAll('table');
    for (const table of tables) {
        __labelTableCells(table);
        const tbody = table.tBodies[0];
        if (!tbody || __tableLabelObservers.has(tbody)) continue;
        __tableLabelObservers.add(tbody);
        new MutationObserver(() => __labelTableCells(table))
            .observe(tbody, { childList: true, subtree: true });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', __setupTableLabels);
} else {
    __setupTableLabels();
}

/* ── Nav dropdowns (header) ───────────────────────────────────────────────
   The account button (and, for logged-out visitors, the "Menu" button) in the
   main nav are expandable triggers rather than links; clicking one reveals a
   small menu of site pages. Closes on outside click or Escape. */
function __setupAccountMenu() {
    const menus = document.querySelectorAll('.account-menu');
    if (!menus.length) return;

    menus.forEach((menu) => {
        const trigger = menu.querySelector('.account-trigger');
        if (!trigger || menu.dataset.menuReady) return;
        menu.dataset.menuReady = '1';

        const setOpen = (open) => {
            menu.classList.toggle('open', open);
            trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        };

        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            setOpen(!menu.classList.contains('open'));
        });
        document.addEventListener('click', (e) => {
            if (!menu.contains(e.target)) setOpen(false);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') setOpen(false);
        });
    });
}

/* ── Compact nav ──────────────────────────────────────────────────────────
   When the nav's items can't fit on one line (e.g. branding + Percy section
   badge crowd out the account name), collapse the account button to just its
   avatar. We measure by clearing the flag, then re-checking overflow. */
function __setupNavCompact() {
    const nav = document.querySelector('nav.main-navigation');
    if (!nav) return;

    const apply = () => {
        nav.classList.remove('nav-compact');
        if (nav.scrollWidth > nav.clientWidth + 1) {
            nav.classList.add('nav-compact');
        }
    };

    apply();

    // Wire the resize/observer listeners only once — `__setupNav` re-runs on
    // bfcache `pageshow`, but the measurement above already re-checks.
    if (nav.dataset.compactReady) return;
    nav.dataset.compactReady = '1';

    let raf = 0;
    const schedule = () => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(apply);
    };
    window.addEventListener('resize', schedule);
    window.addEventListener('load', schedule);
    if (window.ResizeObserver) {
        new ResizeObserver(schedule).observe(nav);
    }
}

/* ── Colour theme toggle ──────────────────────────────────────────────────
   The initial theme is applied pre-paint by the inline script in layout.html.
   Here we wire the nav toggle: flip the theme, persist it, update the browser
   chrome meta tags, and briefly add `.theme-transition` so the page cross-fades
   between palettes instead of snapping. */
let __themeTransitionTimer = 0;

function __applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.setAttribute('content', theme === 'light' ? '#f7f6f3' : '#09090b');
    const cs = document.querySelector('meta[name="color-scheme"]');
    if (cs) cs.setAttribute('content', theme === 'light' ? 'light' : 'dark');
}

function __setupThemeToggle() {
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;

    const reflect = () => {
        const isLight = document.documentElement.dataset.theme === 'light';
        toggle.setAttribute('aria-pressed', isLight ? 'true' : 'false');
    };
    reflect();

    if (toggle.dataset.themeReady) return;
    toggle.dataset.themeReady = '1';

    toggle.addEventListener('click', () => {
        const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
        const root = document.documentElement;
        root.classList.add('theme-transition');
        __applyTheme(next);
        try { localStorage.setItem('theme', next); } catch (e) { /* private mode */ }
        reflect();
        clearTimeout(__themeTransitionTimer);
        __themeTransitionTimer = setTimeout(() => root.classList.remove('theme-transition'), 450);
    });
}

function __setupNav() {
    __setupAccountMenu();
    __setupNavCompact();
    __setupThemeToggle();
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', __setupNav);
} else {
    __setupNav();
}
window.addEventListener('pageshow', __setupNav);
// Re-run on bfcache restores (back/forward / "soft reload") since
// DOMContentLoaded does not fire when the page is resurrected.
window.addEventListener('pageshow', __setupTableLabels);