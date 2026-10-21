// Discord-flavoured markdown -> HTML for message previews (level-up message, tag preview).
// Follows Discord's "Markdown Text 101". Input is escaped first; only generated tags reach the output,
// so user text never reaches innerHTML raw.
const PercyDiscordMarkdown = (function () {
    const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const LINK_REL = 'rel="noopener nofollow ugc" target="_blank"';

    function timestamp(unix, fmt) {
        const d = new Date(Number(unix) * 1000);
        if (isNaN(d.getTime())) return null;
        const o = {
            t: { timeStyle: 'short' }, T: { timeStyle: 'medium' }, d: { dateStyle: 'short' }, D: { dateStyle: 'long' },
            f: { dateStyle: 'long', timeStyle: 'short' }, F: { dateStyle: 'full', timeStyle: 'short' },
        }[fmt || 'f'];
        if (fmt === 'R') {
            const sec = Math.round((d.getTime() - Date.now()) / 1000);
            const units = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60], ['second', 1]];
            const [u, n] = units.find(([, n]) => Math.abs(sec) >= n) || units[5];
            return new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(Math.round(sec / n), u);
        }
        return d.toLocaleString(undefined, o);
    }

    function render(text, ctx) {
        const stash = [];
        // Generated HTML is parked behind a token so later formatting passes cannot touch it.
        const park = (html) => '\u0000' + (stash.push(html) - 1) + '\u0000';
        let s = esc(String(text).replace(/\u0000/g, '').replace(/\r\n?/g, '\n'));

        s = s.replace(/```(?:[\w+-]*\n)?([\s\S]*?)```/g, (_, c) => park('<pre class="dc-pre"><code>' + c.replace(/\n$/, '') + '</code></pre>'));
        s = s.replace(/`([^`\n]+)`/g, (_, c) => park('<code>' + c + '</code>'));
        s = s.replace(/[\\]([*_~|`>#\-\[\]()\\])/g, (_, c) => park(c));
        s = s.replace(/&lt;(a?):(\w+):(\d+)&gt;/g, (_, a, name, id) =>
            park('<img class="dc-emoji" src="https://cdn.discordapp.com/emojis/' + id + '.' + (a ? 'gif' : 'webp') + '" alt=":' + name + ':">'));
        s = s.replace(/&lt;t:(-?\d{1,12})(?::([tTdDfFR]))?&gt;/g, (m, n, f) => {
            const t = timestamp(n, f);
            return t === null ? m : park('<span class="dc-timestamp">' + esc(t) + '</span>');
        });
        s = s.replace(/&lt;(#|@&amp;|@!?)(\d+)&gt;/g, (_, k) =>
            park('<span class="dc-mention">' + (k === '#' ? '#channel' : k === '@&amp;' ? '@role' : '@user') + '</span>'));
        for (const [key, label] of Object.entries((ctx && ctx.mentions) || {})) {
            s = s.split(esc(key)).join(park('<span class="dc-mention">' + esc(key.charAt(0) === '@' ? '@' + label : label) + '</span>'));
        }

        const inline = (t) => {
            t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/(?:(?!&quot;|&lt;|&gt;)[^\s)])+)\)/g, (_, label, url) =>
                park('<a class="dc-link" href="' + url + '" ' + LINK_REL + '>' + inline(label) + '</a>'));
            t = t.replace(/https?:\/\/(?:(?!&quot;|&lt;|&gt;)[^\s\u0000])+/g, (url) => {
                const tail = (url.match(/[.,!?;:)]+$/) || [''])[0];
                const u = url.slice(0, url.length - tail.length);
                return park('<a class="dc-link" href="' + u + '" ' + LINK_REL + '>' + u + '</a>') + tail;
            });
            return t.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .replace(/__(.+?)__/g, '<u>$1</u>')
                .replace(/\*(?!\s)(.+?)\*/g, '<em>$1</em>')
                .replace(/(^|[^\w])_(?!\s)(.+?)_(?!\w)/g, '$1<em>$2</em>')
                .replace(/~~(.+?)~~/g, '<s>$1</s>')
                .replace(/\|\|(.+?)\|\|/g, '<span class="dc-spoiler">$1</span>');
        };

        const out = []; // { html, block }
        const lines = s.split('\n');
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            let m;
            if ((m = line.match(/^&gt;&gt;&gt; ?([\s\S]*)$/))) {
                const rest = [m[1]].concat(lines.slice(i + 1)).map(inline).join('<br>');
                out.push({ html: '<blockquote class="dc-quote">' + rest + '</blockquote>', block: true });
                break;
            } else if (/^&gt; ?/.test(line)) {
                const q = [];
                while (i < lines.length && /^&gt; ?/.test(lines[i])) q.push(inline(lines[i++].replace(/^&gt; ?/, '')));
                i--;
                out.push({ html: '<blockquote class="dc-quote">' + q.join('<br>') + '</blockquote>', block: true });
            } else if ((m = line.match(/^(#{1,3}) (.+)$/))) {
                out.push({ html: '<div class="dc-h dc-h' + m[1].length + '">' + inline(m[2]) + '</div>', block: true });
            } else if ((m = line.match(/^-# (.+)$/))) {
                out.push({ html: '<div class="dc-sub">' + inline(m[1]) + '</div>', block: true });
            } else if (/^ *(?:[-*]|\d+\.) \S/.test(line)) {
                const items = []; // top-level { tag, text, kids: [{ tag, text }] }
                while (i < lines.length && (m = lines[i].match(/^( *)([-*]|\d+\.) (\S.*)$/))) {
                    const it = { tag: /\d/.test(m[2]) ? 'ol' : 'ul', text: inline(m[3]) };
                    const last = items[items.length - 1];
                    if (m[1].length >= 2 && last) (last.kids = last.kids || []).push(it);
                    else if (last && last.tag === it.tag) items.push(it);
                    else if (last) break;
                    else items.push(it);
                    i++;
                }
                i--;
                const li = (x) => '<li>' + x.text + (x.kids ? '<' + x.kids[0].tag + ' class="dc-list">' + x.kids.map(li).join('') + '</' + x.kids[0].tag + '>' : '') + '</li>';
                out.push({ html: '<' + items[0].tag + ' class="dc-list">' + items.map(li).join('') + '</' + items[0].tag + '>', block: true });
            } else {
                const html = inline(line);
                out.push({ html, block: /^\u0000\d+\u0000$/.test(line) && stash[+line.slice(1, -1)].startsWith('<pre') });
            }
        }
        s = out.map((o, k) => (k && !o.block && !out[k - 1].block ? '<br>' : '') + o.html).join('');

        for (let n = 0; n < 5 && s.includes('\u0000'); n++) s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => stash[+i]);
        return s;
    }
    return { render };
})();
if (typeof module !== 'undefined') module.exports = PercyDiscordMarkdown;
