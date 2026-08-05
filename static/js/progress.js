/* Percy Dashboard — read/watched marks and order-page keyboard nav (§6.2).

   Enhancement only. Every toggle on the page is a real <form> that posts to
   /account/progress[/remove] and redirects back, so with JS off the mark still
   sticks (D9). All this adds is §6.2's interaction budget:

     * the click writes localStorage *instantly* and flips the control;
     * the PUT/DELETE is debounced 800 ms and coalesced per item, so hammering a
       row costs one request;
     * the server's returned `entries` is adopted wholesale afterwards — see
       `reconcile` for why that is the whole conflict rule;
     * a failed write keeps the mark locally, says so on the control, and is
       retried on the next page load.

   The pure half (reconcile / plan / merge) is exported and self-checked: run
   `node static/js/progress.js` to execute the asserts at the bottom. */

(function (root) {
    'use strict';

    var DEBOUNCE_MS = 800;
    var STORE = 'percy.progress.v1';
    var PENDING = 'percy.progress.pending.v1';
    var MEDIA = ['comic', 'title'];

    // ---- pure logic (self-checked below) --------------------------------

    /** An empty progress map with both media present, the shape Percy always sends. */
    function emptyMap() {
        return { comic: {}, title: {} };
    }

    /** Percy's payload, defended against a missing medium. */
    function normalise(map) {
        var out = emptyMap();
        MEDIA.forEach(function (media) {
            var marks = (map && map[media]) || {};
            Object.keys(marks).forEach(function (key) { out[media][key] = marks[key]; });
        });
        return out;
    }

    /* The conflict rule, in one function.

       Percy merges last-write-wins on `updated_at` and answers with the user's WHOLE
       set plus `merged`, the count of rows that actually stuck. `merged < sent` means
       the server was already newer — a successful merge, not an error.

       Rather than branching on that, the local set is *replaced* by the server's on
       every successful write. It is the same answer in both cases (the server is the
       truth), it needs no comparison to get right, and it is idempotent — which is what
       keeps a control from flickering between two states while two writes race. The
       only thing kept from the local set is entries still queued in `pending`, which
       the server has not been told about yet. */
    function reconcile(server, pending) {
        var next = normalise(server);
        Object.keys(pending || {}).forEach(function (id) {
            var item = pending[id];
            if (item.status) {
                next[item.media][item.release_key] = { status: item.status, updated_at: item.updated_at };
            } else {
                delete next[item.media][item.release_key];
            }
        });
        return next;
    }

    /** Splits the pending queue into one PUT batch and the individual DELETEs. */
    function plan(pending) {
        var marks = [];
        var removals = [];
        Object.keys(pending || {}).forEach(function (id) {
            var item = pending[id];
            if (item.status) {
                marks.push({
                    media: item.media,
                    release_key: item.release_key,
                    status: item.status,
                    updated_at: item.updated_at
                });
            } else {
                removals.push({ media: item.media, release_key: item.release_key });
            }
        });
        return { marks: marks, removals: removals };
    }

    /** The queue id for one item. `media` first: a release key is only unique within one. */
    function id(media, key) {
        return media + '\u0000' + key;
    }

    var api = {
        DEBOUNCE_MS: DEBOUNCE_MS,
        emptyMap: emptyMap,
        normalise: normalise,
        reconcile: reconcile,
        plan: plan,
        id: id
    };
    root.PercyProgress = api;
    if (typeof module === 'object' && module.exports) { module.exports = api; }

    if (typeof document === 'undefined') { selfCheck(); return; }

    // ---- storage --------------------------------------------------------

    function read(name, fallback) {
        try {
            return JSON.parse(localStorage.getItem(name)) || fallback;
        } catch (error) {
            return fallback;
        }
    }

    function write(name, value) {
        try {
            localStorage.setItem(name, JSON.stringify(value));
        } catch (error) {
            /* Private mode, or a full quota. The network write still happens. */
        }
    }

    var store = normalise(read(STORE, null));
    var pending = read(PENDING, {});
    var timer = null;

    // ---- rendering ------------------------------------------------------

    function forms() {
        return Array.prototype.slice.call(document.querySelectorAll('form[data-progress]'));
    }

    function paint(form) {
        var media = form.dataset.media;
        var key = form.dataset.key;
        var marked = Object.prototype.hasOwnProperty.call(store[media] || {}, key);
        var button = form.querySelector('.pg__button');
        var label = form.querySelector('.pg__label');

        form.classList.toggle('pg--on', marked);
        form.action = marked ? '/account/progress/remove' : '/account/progress';
        if (button) button.setAttribute('aria-pressed', marked ? 'true' : 'false');
        if (label) label.textContent = marked ? form.dataset.markedLabel : form.dataset.label;

        var note = form.querySelector('.pg__note');
        if (note) {
            note.textContent = Object.prototype.hasOwnProperty.call(pending, id(media, key))
                ? 'Not saved yet — kept on this device.'
                : '';
        }
    }

    function repaint() {
        forms().forEach(paint);
    }

    // ---- writing --------------------------------------------------------

    function queue(media, key, status) {
        var stamp = new Date().toISOString();
        pending[id(media, key)] = { media: media, release_key: key, status: status, updated_at: stamp };
        if (status) {
            store[media][key] = { status: status, updated_at: stamp };
        } else {
            delete store[media][key];
        }
        write(STORE, store);
        write(PENDING, pending);
        repaint();

        clearTimeout(timer);
        timer = setTimeout(flush, DEBOUNCE_MS);
    }

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams(body).toString()
        }).then(function (response) {
            if (!response.ok) throw new Error('write failed');
            return response.json().catch(function () { return {}; });
        }).then(function (data) {
            if (data && data.ok === false) throw new Error(data.error || 'write failed');
            return data;
        });
    }

    /* One request per queued item, then the whole set re-read from the server.

       The per-item POST is deliberate: these are the same two endpoints the JS-off form
       posts to, so there is exactly one write path to keep correct. The debounce is what
       keeps that cheap — a burst of clicks on one row collapses into its last state. */
    function flush() {
        var work = plan(pending);
        var sent = Object.keys(pending);
        if (!sent.length) return;

        var calls = work.marks.map(function (mark) {
            return post('/account/progress', {
                media: mark.media,
                release_key: mark.release_key,
                status: mark.status,
                redirect: location.pathname
            });
        }).concat(work.removals.map(function (removal) {
            return post('/account/progress/remove', {
                media: removal.media,
                release_key: removal.release_key,
                redirect: location.pathname
            });
        }));

        Promise.all(calls).then(function () {
            sent.forEach(function (key) { delete pending[key]; });
            write(PENDING, pending);
            return fetch('/account/progress', { headers: { 'Accept': 'application/json' } });
        }).then(function (response) {
            return response && response.ok ? response.json() : null;
        }).then(function (data) {
            if (data && data.entries) {
                // The server's set is the truth; anything still queued rides on top.
                store = reconcile(data.entries, pending);
                write(STORE, store);
            }
            repaint();
        }).catch(function () {
            // The mark stays in `pending` and the control says so, rather than the
            // click quietly evaporating. Retried on the next page load.
            repaint();
        });
    }

    // ---- wiring ---------------------------------------------------------

    document.addEventListener('submit', function (event) {
        var form = event.target.closest ? event.target.closest('form[data-progress]') : null;
        if (!form) return;
        event.preventDefault();
        var media = form.dataset.media;
        var key = form.dataset.key;
        var marked = Object.prototype.hasOwnProperty.call(store[media] || {}, key);
        queue(media, key, marked ? null : form.dataset.status);
    });

    /* Keyboard nav on order pages (§6.2): j/k move, w toggles the focused entry.

       Three rules make it safe rather than hostile: never while typing, never with a
       modifier held (Cmd+J is a browser shortcut), and the move is *real focus*, so the
       change is announced by a screen reader and visible as a focus ring. */
    function entries() {
        return Array.prototype.slice.call(document.querySelectorAll('[data-entry]'));
    }

    function typing(target) {
        if (!target) return false;
        var tag = (target.tagName || '').toLowerCase();
        return target.isContentEditable || tag === 'input' || tag === 'textarea' || tag === 'select';
    }

    function move(step) {
        var rows = entries();
        if (!rows.length) return;
        var at = rows.indexOf(document.activeElement.closest ? document.activeElement.closest('[data-entry]') : null);
        var next = rows[Math.min(Math.max(at + step, 0), rows.length - 1)] || rows[0];
        next.focus();
        next.scrollIntoView({ block: 'nearest' });
    }

    document.addEventListener('keydown', function (event) {
        if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return;
        if (event.key === 'j') { event.preventDefault(); move(1); }
        else if (event.key === 'k') { event.preventDefault(); move(-1); }
        else if (event.key === 'w') {
            var row = event.target.closest ? event.target.closest('[data-entry]') : null;
            var form = row && row.querySelector('form[data-progress]');
            if (!form) return;
            event.preventDefault();
            form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit', { cancelable: true }));
        }
    });

    // The hint ships `hidden` so the page never promises shortcuts a JS-off visitor
    // does not have; it is only discoverable once they exist.
    Array.prototype.forEach.call(document.querySelectorAll('.pg-keys'), function (hint) {
        if (entries().length) hint.hidden = false;
    });

    repaint();
    // Anything left over from a failed write in a previous visit goes out now.
    if (Object.keys(pending).length) flush();

    // ---- self-check (node static/js/progress.js) ------------------------

    function selfCheck() {
        var assert = function (ok, what) {
            if (!ok) { throw new Error('FAILED: ' + what); }
        };

        // The server's set replaces the local one — that is the whole `merged < sent` rule.
        var server = { comic: { 'MARVEL/asm-12': { status: 'read', updated_at: 'B' } }, title: {} };
        var settled = reconcile(server, {});
        assert(settled.comic['MARVEL/asm-12'].updated_at === 'B', 'server value wins');
        assert(Object.keys(settled.title).length === 0, 'both media present');

        // ...even when the local set said something else about the same key.
        var stale = reconcile(server, {});
        assert(!stale.comic['MARVEL/asm-13'], 'a local-only key the server dropped is gone');

        // Anything still queued rides on top: it has not reached the server yet.
        var queued = {};
        queued[id('title', 'mcu/iron-man')] = {
            media: 'title', release_key: 'mcu/iron-man', status: 'watched', updated_at: 'C'
        };
        queued[id('comic', 'MARVEL/asm-12')] = {
            media: 'comic', release_key: 'MARVEL/asm-12', status: null, updated_at: 'C'
        };
        var merged = reconcile(server, queued);
        assert(merged.title['mcu/iron-man'].status === 'watched', 'a pending mark survives the adopt');
        assert(!merged.comic['MARVEL/asm-12'], 'a pending unmark survives the adopt');

        // A payload from an older Percy (or none at all) is still both media.
        assert(Object.keys(normalise(null)).sort().join() === 'comic,title', 'normalise fills both media');

        // The queue splits into one batch of marks and individual removals.
        var work = plan(queued);
        assert(work.marks.length === 1 && work.marks[0].release_key === 'mcu/iron-man', 'marks planned');
        assert(work.removals.length === 1 && work.removals[0].media === 'comic', 'removals planned');

        // Coalescing: the id is per (media, key), so a burst on one row is one entry...
        var burst = {};
        burst[id('comic', 'a/b')] = { media: 'comic', release_key: 'a/b', status: 'read' };
        burst[id('comic', 'a/b')] = { media: 'comic', release_key: 'a/b', status: null };
        assert(Object.keys(burst).length === 1, 'one row coalesces to one queued write');
        assert(plan(burst).marks.length === 0, 'the last intent wins');
        // ...and the same key in the other medium is a different write.
        assert(id('comic', 'a/b') !== id('title', 'a/b'), 'media namespaces the queue id');

        assert(DEBOUNCE_MS === 800, '§6.2 fixes the debounce at 800 ms');

        // eslint-disable-next-line no-console
        console.log('progress.js self-check passed');
    }
})(typeof window !== 'undefined' ? window : globalThis);
