/* Percy Dashboard — /account/subscriptions.

   Enhancement only: the page ships its suggestions server-rendered and every
   control is a real form submit, so with JS off searching just reloads the page
   (D9). All this does is debounce the same query and swap the list in place. */

(function () {
    const form = document.getElementById('sb-search-form');
    const input = document.getElementById('sb-search-input');
    const list = document.getElementById('sb-suggestions');
    if (!form || !input || !list) return;

    const DEBOUNCE_MS = 250;
    let timer = null;
    let inFlight = 0;

    // Built as DOM nodes, never as an HTML string: a catalogue label is somebody
    // else's data (TMDB, a comic publisher) and a double quote in one would break
    // straight out of a value="..." attribute.
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function hidden(name, value) {
        const node = document.createElement('input');
        node.type = 'hidden';
        node.name = name;
        node.value = value == null ? '' : String(value);
        return node;
    }

    function suggestion(s) {
        const item = el('li', 'sb-suggestion');
        const identity = el('span', 'sb-suggestion__id');
        identity.append(el('span', 'sb-row__label', s.label), el('span', 'sb-suggestion__hint', s.hint));
        item.append(identity);

        if (s.followed) {
            item.append(el('span', 'sb-suggestion__followed', 'Following'));
            return item;
        }

        const form = document.createElement('form');
        form.method = 'post';
        form.action = '/account/subscriptions';
        const button = el('button', 'button small primary', 'Subscribe');
        button.type = 'submit';
        form.append(
            hidden('media', s.media),
            hidden('subject_type', s.subject_type),
            // The opaque key, straight back as it came — never the label.
            hidden('subject_value', s.subject_value),
            hidden('subject_label', s.label),
            button
        );
        item.append(form);
        return item;
    }

    function render(subjects) {
        list.replaceChildren();
        if (!subjects.length) {
            const empty = el('li', 'sb-suggestion');
            empty.append(el('span', 'sb-suggestion__hint', 'Nothing matched that.'));
            list.append(empty);
            return;
        }
        subjects.forEach(function (s) { list.append(suggestion(s)); });
    }

    function search() {
        const query = input.value.trim();
        // Later responses win: a slow request for "iro" must not overwrite "iron".
        const ticket = ++inFlight;
        fetch('/account/subscriptions/search?q=' + encodeURIComponent(query), {
            headers: { 'Accept': 'application/json' }
        })
            .then(function (r) { return r.ok ? r.json() : { subjects: [] }; })
            .then(function (data) {
                if (ticket === inFlight) render(data.subjects || []);
            })
            .catch(function () { /* the form submit still works */ });
    }

    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(search, DEBOUNCE_MS);
    });

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        clearTimeout(timer);
        search();
    });
})();
