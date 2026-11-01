// Humanises ISO-8601 timestamps marked with `.js-ts`.
//
// Each `.js-ts` element carries the raw value in its `datetime` attribute (or
// its text content). The visible text becomes a relative phrase ("2 hours ago",
// "in 3 days"); hovering shows the full local date in a floating tooltip. The
// tooltip is a fixed-position node on <body>, so it never reflows the table or
// gets clipped by a scroll container. Invalid / placeholder values (e.g. "—")
// are left untouched. Idempotent — safe to call again after injecting new DOM
// via `window.formatTimestamps(root)`.
(function () {
    "use strict";

    var tip = null;

    function tooltip() {
        if (!tip) {
            tip = document.createElement("div");
            tip.className = "ts-tooltip";
            tip.setAttribute("role", "tooltip");
            document.body.appendChild(tip);
        }
        return tip;
    }

    function showTip(el) {
        var t = tooltip();
        t.textContent = el.dataset.abs || "";
        t.style.display = "block";
        var r = el.getBoundingClientRect();
        var tr = t.getBoundingClientRect();
        var left = r.left + (r.width - tr.width) / 2;
        left = Math.max(6, Math.min(left, window.innerWidth - tr.width - 6));
        var top = r.top - tr.height - 8;
        if (top < 6) top = r.bottom + 8; // flip below when there's no room above
        t.style.left = left + "px";
        t.style.top = top + "px";
        t.classList.add("visible");
    }

    function hideTip() {
        if (tip) {
            tip.classList.remove("visible");
            tip.style.display = "none";
        }
    }

    var ABS_OPTS = {
        year: "numeric", month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit",
    };

    function formatAbsolute(d) {
        try {
            return d.toLocaleString(undefined, ABS_OPTS);
        } catch (e) {
            return d.toString();
        }
    }

    function relativeFormatter() {
        if (typeof Intl !== "undefined" && Intl.RelativeTimeFormat) {
            return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
        }
        return null;
    }

    var RTF = relativeFormatter();

    function rel(value, unit) {
        if (RTF) return RTF.format(value, unit);
        var n = Math.abs(value);
        var s = n + " " + unit + (n === 1 ? "" : "s");
        return value < 0 ? s + " ago" : "in " + s;
    }

    function formatRelative(d) {
        var diffMs = d.getTime() - Date.now(); // > 0 => future
        var sign = diffMs < 0 ? -1 : 1;
        var sec = Math.round(Math.abs(diffMs) / 1000);
        var min = Math.round(sec / 60);
        var hr = Math.round(min / 60);
        var day = Math.round(hr / 24);

        if (sec < 45) return rel(sign * sec, "second");
        if (min < 45) return rel(sign * min, "minute");
        if (hr < 22) return rel(sign * hr, "hour");
        if (day < 26) return rel(sign * day, "day");
        var month = Math.round(day / 30);
        if (month < 11) return rel(sign * month, "month");
        return rel(sign * Math.round(day / 365), "year");
    }

    function formatOne(el) {
        var raw = (el.getAttribute("datetime") || el.textContent || "").trim();
        if (!raw) return;
        var d = new Date(raw);
        if (isNaN(d.getTime())) return; // not a real date (placeholder dash, etc.)

        var relative = formatRelative(d);
        var absolute = formatAbsolute(d);

        el.setAttribute("datetime", raw);
        el.removeAttribute("title");
        el.dataset.abs = absolute;
        el.textContent = relative;
        el.classList.add("ts-formatted");

        // Hovering shows the full date in a floating tooltip (no layout shift).
        el.addEventListener("mouseenter", function () { showTip(el); });
        el.addEventListener("mouseleave", hideTip);
    }

    function run(root) {
        var scope = root && root.querySelectorAll ? root : document;
        if (scope.matches && scope.matches(".js-ts:not(.ts-formatted)")) formatOne(scope);
        scope.querySelectorAll(".js-ts:not(.ts-formatted)").forEach(formatOne);
    }

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    // Returns markup for a humanised timestamp. Use from any client renderer:
    //   row.innerHTML = `<td>${tsHtml(iso)}</td>`
    // Accepts an ISO string (or anything `Date` parses). The MutationObserver
    // below formats it once it lands in the DOM — no manual call required.
    function tsHtml(value) {
        var v = esc(value);
        return '<time class="js-ts" datetime="' + v + '">' + v + "</time>";
    }

    // Auto-format any `.js-ts` added to the DOM later (live tables, WS updates,
    // pagination), so callers only need to emit the markup. Scans are coalesced
    // into one rAF tick to stay cheap on frequently-updating admin pages.
    if (typeof MutationObserver !== "undefined") {
        var scheduled = false;
        function schedule() {
            if (scheduled) return;
            scheduled = true;
            var defer = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
            defer(function () { scheduled = false; run(document); });
        }
        var observer = new MutationObserver(function (mutations) {
            for (var i = 0; i < mutations.length; i++) {
                if (mutations[i].addedNodes.length) { schedule(); return; }
            }
        });
        function startObserver() { observer.observe(document.body, { childList: true, subtree: true }); }
        if (document.body) startObserver();
        else document.addEventListener("DOMContentLoaded", startObserver);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { run(); });
    } else {
        run();
    }

    window.formatTimestamps = run;
    window.tsHtml = tsHtml;
})();
