// Public server overview: mounts the shared Apple-Music-style live player.
// The player owns status polling + playback controls; every mutation is
// re-authorized server-side (voice-presence + DJ-mode) by Percy.
(function () {
    'use strict';

    const root = document.getElementById('percy-player');
    if (!root || !window.PercyPlayer) return;

    const base = `/dashboard/guild/${GUILD_ID}/overview/music`;
    window.PercyPlayer.mount({
        root: root,
        statusUrl: base,
        controlUrl: `${base}/control`,
        lyricsUrl: `${base}/lyrics`,
    });
})();
