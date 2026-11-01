/* Click-to-load trailer (the plan's §6.2 item 7).
 *
 * The iframe is never in the server-rendered HTML: YouTube's embed pulls a few hundred KB
 * and sets cookies, and a page that does that on every visit has decided for the reader.
 * So the control is a real <a href> to the trailer on its own site, and this upgrades a
 * click into an inline embed. With JS off, blocked, or simply not loaded yet, the same
 * click still works — it just opens the trailer where it lives.
 *
 * `allow` deliberately omits `autoplay`: §6.2 forbids it, and a trailer that starts
 * talking because someone scrolled is the behaviour the rule exists to prevent. The reader
 * pressed play once to get the iframe and presses it again to start it.
 */
(function () {
    document.querySelectorAll(".wl-trailer__play[data-embed]").forEach(function (link) {
        link.addEventListener("click", function (event) {
            var src = link.dataset.embed;
            if (!src || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
                return; // let a modifier-click open the original, as any link would
            }
            event.preventDefault();

            var frame = document.createElement("iframe");
            frame.className = "wl-trailer__frame";
            frame.src = src;
            frame.title = link.dataset.embedTitle || "Trailer";
            frame.loading = "lazy";
            frame.allow = "encrypted-media; picture-in-picture";
            frame.allowFullscreen = true;
            frame.referrerPolicy = "strict-origin-when-cross-origin";

            link.replaceWith(frame);
            frame.focus();
        });
    });
})();
