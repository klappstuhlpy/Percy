import re
from typing import Any
from urllib.parse import urljoin

import markdownify
from bs4.element import Tag

from app.cogs.doc._html import _class_filter_factory

# Because markdownify is outdated, we need to update the whitespace regex
# Also See https://github.com/matthewwithanm/python-markdownify/issues/31
markdownify.whitespace_re = re.compile(r'[\r\n\s\t ]+')


class DocMarkdownConverter(markdownify.MarkdownConverter):
    """Subclass markdownify's MarkdownCoverter to provide custom conversion methods."""

    def __init__(self, *, page_url: str, **options: Any) -> None:
        super().__init__(**options)
        self.page_url = page_url

    def convert_li(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Fix markdownify's erroneous indexing in ol tags."""
        parent = el.parent
        if parent is not None and parent.name == 'ol':
            li_tags = parent.find_all('li')
            bullet = f'{li_tags.index(el)+1}.'
        else:
            depth = -1
            while el:
                if el.name == 'ul':
                    depth += 1
                el = el.parent
            bullets = self.options['bullets']
            bullet = bullets[depth % len(bullets)]
        return f'{bullet} {text}\n'

    def convert_hn(self, _n: int, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Convert h tags to bold text with ** instead of adding #."""
        if convert_as_inline:
            return text
        return f'**{text}**\n\n'

    def convert_code(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Undo `markdownify`s underscore escaping."""
        return f'`{text}`'.replace('\\', "")

    def convert_pre(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Wrap any codeblocks in `py` for syntax highlighting."""
        code = "".join(el.strings)
        return f'```py\n{code}```'

    def convert_a(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Resolve relative URLs to `self.page_url`."""
        prefix, suffix, text = markdownify.chomp(text)
        if not text:
            return ''

        el['href'] = urljoin(self.page_url, el.get('href', ""))

        href = el.get('href')
        title = el.get('title')

        if (
                self.options['autolinks']
                and text.replace(r'\_', '_') == href
                and not title
                and not self.options['default_title']
        ):
            # We have a link that matches the text, and we're allowed to use
            # autolinks, and we don't have a title, and we don't want to use
            # the default title, so we just return the URL.
            return '<%s>' % href

        return f'{prefix}[{text}]({href}){suffix}' if href else text

    def convert_p(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Include only one newline instead of two when the parent is a li tag."""
        if convert_as_inline:
            return text

        parent = el.parent

        if parent is not None and parent.name == 'li':
            return f'{text}\n'

        if parent is not None and _class_filter_factory(['admonition'])(parent):
            # Now we search for possible admonition titles and convert them to h2s
            # (In Discord's markdown, it's ##)

            ADMONITION_REGEX = re.compile(r'^(Note|Warning|Tip|Danger|Error|Info|Hint|Success)')
            # Also ensure that the Title is at the start of the paragraph

            # Because admonition paragraphs also include the raw text of the admonition as a child,
            # We need to find the admonition titles and replace them with h2s,
            # so that the text is not duplicated in the final output and displayed normaly, because
            # only the headers should be h2s, not the text

            match = ADMONITION_REGEX.match(text)
            if match:
                # If the text matches the regex, we replace it with a h2
                text = text.replace(match.group(1), f'## {match.group(1)}')

            # If the parent is a blockquote, we add a newline to the end of the paragraph
            return f'{text}\n'
        return super().convert_p(el, text, convert_as_inline)

    def convert_hr(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Ignore `hr` tag."""
        return ""
