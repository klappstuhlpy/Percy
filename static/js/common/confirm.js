/* Percy Dashboard — the one confirmation dialog.

   Replaces window.confirm everywhere: instead of "Are you sure?", every confirmation says
   what will happen, to what, and whether it can be undone.

     const ok = await PercyConfirm.ask({
         title: 'Delete tag "rules"?',          // the action, as a question
         consequence: 'The tag and its 3 aliases are removed for everyone.',
         undo: null,                             // a sentence on how to undo, or null = cannot be undone
         confirmLabel: 'Delete tag',             // a verb, never "OK"
         danger: true,                           // red confirm button, Cancel focused
         typeToConfirm: 'rules',                 // optional: the confirm button waits for this text
     });

   Loaded on every dashboard page (dashboard_layout.html) and the few standalone pages that
   confirm something. Falls back to window.confirm where <dialog> is unavailable. */

(function () {
    'use strict';

    const STYLE = `
.pc-dialog { border: 1px solid var(--box-border); border-radius: 10px; background: var(--box); color: var(--foreground);
  padding: 0; width: min(440px, calc(100vw - 32px)); box-shadow: 0 20px 50px rgba(0,0,0,.45); }
.pc-dialog::backdrop { background: rgba(0,0,0,.55); }
.pc-body { padding: 1.25rem 1.25rem 1rem; }
.pc-title { margin: 0 0 .6rem; font-size: 1.05rem; font-weight: 600; overflow-wrap: anywhere; }
.pc-consequence { margin: 0 0 .5rem; font-size: .9rem; line-height: 1.5; }
.pc-undo { margin: 0; font-size: .8rem; color: var(--text-muted); }
.pc-undo.pc-final { color: var(--error-text, #f87171); }
.pc-type { margin-top: .9rem; font-size: .8rem; color: var(--text-muted); }
.pc-type input { display: block; width: 100%; box-sizing: border-box; margin-top: .35rem; padding: .45rem .6rem;
  border-radius: 6px; border: 1px solid var(--box-border); background: var(--box-shade); color: var(--foreground); font: inherit; }
.pc-actions { display: flex; justify-content: flex-end; gap: .5rem; padding: .75rem 1.25rem; border-top: 1px solid var(--box-border); }
.pc-actions .pc-danger { background: var(--error-text, #f87171); border-color: transparent; color: #18181b; }
.pc-actions button:disabled { opacity: .5; cursor: not-allowed; }`;

    let dialog = null;

    function build() {
        const style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);

        dialog = document.createElement('dialog');
        dialog.className = 'pc-dialog';
        dialog.innerHTML = `
            <form method="dialog">
                <div class="pc-body">
                    <h2 class="pc-title" id="pc-title"></h2>
                    <p class="pc-consequence"></p>
                    <p class="pc-undo"></p>
                    <label class="pc-type" hidden><span></span><input type="text" autocomplete="off" spellcheck="false"></label>
                </div>
                <div class="pc-actions">
                    <button type="submit" value="cancel" class="button small">Cancel</button>
                    <button type="submit" value="confirm" class="button small primary pc-confirm"></button>
                </div>
            </form>`;
        dialog.setAttribute('aria-labelledby', 'pc-title');
        document.body.appendChild(dialog);
    }

    function ask(opts) {
        const o = Object.assign({ consequence: '', undo: null, confirmLabel: 'Confirm', danger: false, typeToConfirm: null }, opts);
        if (typeof HTMLDialogElement === 'undefined') {
            const undo = o.undo || 'This cannot be undone.';
            return Promise.resolve(window.confirm([o.title, o.consequence, undo].filter(Boolean).join('\n\n')));
        }
        if (!dialog) build();

        dialog.querySelector('.pc-title').textContent = o.title;
        const consequence = dialog.querySelector('.pc-consequence');
        consequence.textContent = o.consequence;
        consequence.hidden = !o.consequence;
        const undo = dialog.querySelector('.pc-undo');
        undo.textContent = o.undo || 'This cannot be undone.';
        undo.classList.toggle('pc-final', !o.undo);

        const confirmBtn = dialog.querySelector('.pc-confirm');
        const cancelBtn = dialog.querySelector('[value="cancel"]');
        confirmBtn.textContent = o.confirmLabel;
        confirmBtn.classList.toggle('pc-danger', !!o.danger);

        const typeRow = dialog.querySelector('.pc-type');
        const typeInput = typeRow.querySelector('input');
        typeRow.hidden = !o.typeToConfirm;
        typeInput.value = '';
        confirmBtn.disabled = !!o.typeToConfirm;
        if (o.typeToConfirm) {
            typeRow.querySelector('span').textContent = `Type “${o.typeToConfirm}” to confirm.`;
            typeInput.oninput = () => { confirmBtn.disabled = typeInput.value.trim() !== o.typeToConfirm; };
        }

        return new Promise((resolve) => {
            dialog.returnValue = 'cancel';
            dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
            dialog.showModal();
            (o.typeToConfirm ? typeInput : (o.danger ? cancelBtn : confirmBtn)).focus();
        });
    }

    window.PercyConfirm = { ask };
})();
