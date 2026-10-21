const test = require('node:test'); const assert = require('node:assert');
const { render } = require('../static/js/discord_markdown.js');
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
