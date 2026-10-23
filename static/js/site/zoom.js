// Click a homepage screenshot to see it large. One shared <dialog> (Esc, focus and the backdrop come
// for free); only transform and opacity animate, so the zoom stays on the compositor and never janks.
// Without JS the screenshots simply stay inline.
(() => {
    const shots = document.querySelectorAll('img.home-shot');
    if (!shots.length || typeof HTMLDialogElement !== 'function') return;

    const dialog = document.createElement('dialog');
    dialog.className = 'home-zoom';
    dialog.setAttribute('aria-label', 'Screenshot');
    const big = document.createElement('img');
    big.decoding = 'async';
    dialog.append(big);
    document.body.append(dialog);

    const close = () => {
        dialog.classList.remove('open');
        setTimeout(() => dialog.close(), 160);
    };

    shots.forEach((shot) => {
        shot.tabIndex = 0;
        shot.setAttribute('role', 'button');
        const open = () => {
            big.src = shot.currentSrc || shot.src;
            big.alt = shot.alt;
            // Small Discord crops get up to 2x, but never wider/taller than the viewport.
            const scale = Math.min(2, (innerWidth * 0.94) / shot.naturalWidth, (innerHeight * 0.9) / shot.naturalHeight);
            big.style.width = `${Math.round(shot.naturalWidth * Math.max(scale, 0.1))}px`;
            dialog.showModal();
            requestAnimationFrame(() => dialog.classList.add('open'));
        };
        shot.addEventListener('click', open);
        shot.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });
    });

    dialog.addEventListener('click', close);
    dialog.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
})();
