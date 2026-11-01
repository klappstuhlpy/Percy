const test = require('node:test'); const assert = require('node:assert');
const { render } = require('../static/js/common/discord_markdown.js');
test('escapes html', () => assert.ok(!render('<img src=x onerror=1>').includes('<img src=x')));
test('bold italic underline strike', () => assert.equal(render('**a** *b* __c__ ~~d~~'),
  '<strong>a</strong> <em>b</em> <u>c</u> <s>d</s>'));
test('inline code is not formatted', () => assert.equal(render('`**x**`'), '<code>**x**</code>'));
test('spoiler', () => assert.match(render('||s||'), /class="dc-spoiler"/));
test('custom emoji', () => assert.match(render('<:pog:123>'), /cdn\.discordapp\.com\/emojis\/123\.webp/));
test('animated emoji', () => assert.match(render('<a:pog:123>'), /123\.gif/));
test('mention pill', () => assert.match(render('@percyfan', { mentions: { '@percyfan': 'percyfan' } }), /class="dc-mention"/));
test('role/channel mentions', () => assert.match(render('<#1> <@&2>'), /dc-mention/));
test('quote line', () => assert.match(render('> hi'), /<blockquote/));
test('newline', () => assert.equal(render('a\nb'), 'a<br>b'));
test('bold italic', () => assert.equal(render('***x***'), '<strong><em>x</em></strong>'));
test('underscore italic', () => assert.equal(render('_x_'), '<em>x</em>'));
test('code block drops the language label', () => assert.equal(render('```js\nlet **a**\n```'), '<pre class="dc-pre"><code>let **a**</code></pre>'));
test('headings only at line start', () => {
  assert.match(render('# a\n## b\n### c'), /dc-h1">a.*dc-h2">b.*dc-h3">c/);
  assert.ok(!render('x # a').includes('dc-h'));
});
test('subtext', () => assert.match(render('-# small'), /<div class="dc-sub">small<\/div>/));
test('lists, nested one level', () => {
  assert.equal(render('- a\n  - b\n- c'), '<ul class="dc-list"><li>a<ul class="dc-list"><li>b</li></ul></li><li>c</li></ul>');
  assert.match(render('1. a\n2. b'), /^<ol class="dc-list"><li>a<\/li><li>b<\/li><\/ol>$/);
  assert.match(render('* a'), /<ul/);
});
test('multi-line quote and >>> quote', () => {
  assert.equal(render('> a\n> b'), '<blockquote class="dc-quote">a<br>b</blockquote>');
  assert.equal(render('x\n>>> a\nb'), 'x<blockquote class="dc-quote">a<br>b</blockquote>');
});
test('masked and bare links', () => {
  assert.match(render('[hi](https://a.com/x?y=1&z=2)'), /<a class="dc-link" href="https:\/\/a\.com\/x\?y=1&amp;z=2" rel="noopener nofollow ugc" target="_blank">hi<\/a>/);
  assert.match(render('see https://a.com.'), /href="https:\/\/a\.com" [^>]*>https:\/\/a\.com<\/a>\.$/);
});
test('non-http links stay literal', () => {
  assert.equal(render('[x](javascript:alert(1))'), '[x](javascript:alert(1))');
  assert.ok(!render('[x](data:text/html,hi)').includes('<a'));
});
test('attribute breakout in url is escaped', () => assert.ok(!/href="[^"]*"[^>]*onmouse/.test(render('[x](https://a.com" onmouseover="1)'))));
test('backslash escapes', () => assert.equal(render('\x5c*a\x5c*'), '*a*'));
test('timestamps', () => {
  assert.match(render('<t:1700000000>'), /^<span class="dc-timestamp">.+<\/span>$/);
  assert.match(render('<t:1700000000:R>'), /dc-timestamp/);
  assert.equal(render('<t:99999999999999999999>'), '&lt;t:99999999999999999999&gt;');
});
test('NUL bytes are stripped and cannot forge stash tokens', () => assert.equal(render('a\u0000b\u00000\u0000'), 'ab0'));
test('script text stays escaped', () => assert.equal(render('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;'));
test('lone image link renders only the embed', () => assert.match(render('https://e.com/a.png'), /^<div class="dc-embeds"><a [^>]+><img class="dc-media" src="https:\/\/e\.com\/a\.png"/));
test('video link keeps text and adds a player', () => {
  const html = render('see https://e.com/v.mp4?x=1');
  assert.match(html, /^see <a class="dc-link"/);
  assert.match(html, /<video class="dc-media" src="https:\/\/e\.com\/v\.mp4\?x=1" controls/);
});
test('plain links get no embed', () => assert.ok(!render('https://e.com/page').includes('dc-embeds')));
