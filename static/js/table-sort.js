// Click-to-sort for any `<table class="sortable">`.
//
// Mark sortable headers with data-sort="text|number|date"; headers without it
// stay inert (e.g. Actions, Roles). Clicking a header sorts the first <tbody>
// in place and toggles ascending/descending, reflected via `aria-sort` (+ CSS
// arrows). Date columns prefer a nested `<time datetime>`. Idempotent — call
// `window.enableTableSort(root)` after injecting tables/rows.
(function () {
    "use strict";

    function cellValue(row, index, type) {
        var cell = row.children[index];
        if (!cell) return type === "number" ? -Infinity : "";
        if (type === "number") {
            var n = parseFloat((cell.textContent || "").replace(/[^0-9.\-]/g, ""));
            return isNaN(n) ? -Infinity : n;
        }
        if (type === "date") {
            var t = cell.querySelector("time");
            var raw = t ? (t.getAttribute("datetime") || t.textContent) : cell.textContent;
            var ms = Date.parse((raw || "").trim());
            return isNaN(ms) ? -Infinity : ms;
        }
        return (cell.textContent || "").trim().toLowerCase();
    }

    function sortBy(table, th) {
        var headers = Array.prototype.slice.call(th.parentNode.children);
        var index = headers.indexOf(th);
        var type = th.getAttribute("data-sort") || "text";
        var tbody = table.tBodies[0];
        if (!tbody) return;

        var asc = th.getAttribute("aria-sort") !== "ascending"; // toggle, default asc
        var dir = asc ? 1 : -1;

        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) {
            var va = cellValue(a, index, type);
            var vb = cellValue(b, index, type);
            if (va < vb) return -dir;
            if (va > vb) return dir;
            return 0;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });

        headers.forEach(function (h) {
            if (h.hasAttribute("data-sort")) h.removeAttribute("aria-sort");
        });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
    }

    function enable(table) {
        var head = table.tHead;
        if (!head) return;
        Array.prototype.forEach.call(head.querySelectorAll("th[data-sort]"), function (th) {
            th.classList.add("th-sortable");
            th.tabIndex = 0;
            th.addEventListener("click", function () { sortBy(table, th); });
            th.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(table, th); }
            });
        });
    }

    function run(root) {
        var scope = root && root.querySelectorAll ? root : document;
        scope.querySelectorAll("table.sortable:not([data-sortable-ready])").forEach(function (t) {
            t.setAttribute("data-sortable-ready", "1");
            enable(t);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { run(); });
    } else {
        run();
    }
    window.enableTableSort = run;
})();
