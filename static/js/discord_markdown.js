// Discord-flavoured markdown -> HTML for message previews.
// Input is escaped first; only the allow-list below is re-introduced, so user text never reaches innerHTML raw.
const PercyDiscordMarkdown = (function () {
    const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function render(text, ctx) {
        const stash = [];
        // Generated HTML is parked behind a token so later formatting passes cannot touch it.
        const park = (html) => '\u0000' + (stash.push(html) - 1) + '\u0000';
        let s = esc(String(text));

        s = s.replace(/```(?:\w*\n)?([\s\S]*?)```/g, (_, c) => park('<pre>' + c.replace(/\n$/, '') + '</pre>'));
        s = s.replace(/`([^`\n]+)`/g, (_, c) => park('<code>' + c + '</code>'));
        s = s.replace(/&lt;(a?):(\w+):(\d+)&gt;/g, (_, a, name, id) =>
            park('<img class="dc-emoji" src="https://cdn.discordapp.com/emojis/' + id + '.' + (a ? 'gif' : 'webp') + '" alt=":' + name + ':">'));
        s = s.replace(/&lt;(#|@&amp;|@)(\d+)&gt;/g, (_, k, id) =>
            park('<span class="dc-mention">' + (k === '#' ? '#channel' : k === '@' ? '@user' : '@role') + '</span>'));
        for (const [key, label] of Object.entries((ctx && ctx.mentions) || {})) {
            s = s.split(esc(key)).join(park('<span class="dc-mention">' + esc(key.charAt(0) === '@' ? '@' + label : label) + '</span>'));
        }

        s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/__(.+?)__/g, '<u>$1</u>')
            .replace(/\*(?!\s)(.+?)\*/g, '<em>$1</em>')
            .replace(/(^|[^\w])_(?!\s)(.+?)_(?!\w)/g, '$1<em>$2</em>')
            .replace(/~~(.+?)~~/g, '<s>$1</s>')
            .replace(/\|\|(.+?)\|\|/g, '<span class="dc-spoiler">$1</span>');

        s = s.split('\n').map((l) => l.startsWith('&gt; ') ? '<blockquote class="dc-quote">' + l.slice(5) + '</blockquote>' : l)
            .join('\n').replace(/<\/blockquote>\n/g, '</blockquote>').replace(/\n/g, '<br>');

        return s.replace(/\u0000(\d+)\u0000/g, (_, i) => stash[+i]);
    }
    return { render };
})();
if (typeof module !== 'undefined') module.exports = PercyDiscordMarkdown;
