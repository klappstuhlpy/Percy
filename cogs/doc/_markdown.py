import re
from urllib.parse import urljoin

import markdownify
from bs4.element import PageElement, Tag

# See https://github.com/matthewwithanm/python-markdownify/issues/31
markdownify.whitespace_re = re.compile(r"[\r\n\s\t ]+")


class DocMarkdownConverter(markdownify.MarkdownConverter):
    """Subclass markdownify's MarkdownCoverter to provide custom conversion methods."""

    def __init__(self, *, page_url: str, **options):
        super().__init__(**options)
        self.page_url = page_url

    def convert_li(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Fix markdownify's erroneous indexing in ol tags."""
        parent = el.parent
        if parent is not None and parent.name == "ol":
            li_tags = parent.find_all("li")
            bullet = f"{li_tags.index(el)+1}."
        else:
            depth = -1
            while el:
                if el.name == "ul":
                    depth += 1
                el = el.parent
            bullets = self.options["bullets"]
            bullet = bullets[depth % len(bullets)]
        return f"{bullet} {text}\n"

    def convert_hn(self, _n: int, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Convert h tags to bold text with ** instead of adding #."""
        if convert_as_inline:
            return text
        return f"**{text}**\n\n"

    def convert_code(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Undo `markdownify`s underscore escaping."""
        return f"`{text}`".replace("\\", "")

    def convert_pre(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Wrap any codeblocks in `py` for syntax highlighting."""
        code = "".join(el.strings)
        return f"```py\n{code}```"

    def convert_a(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Resolve relative URLs to `self.page_url`."""
        el["href"] = urljoin(self.page_url, el.get("href", ""))
        return super().convert_a(el, text, convert_as_inline)

    def convert_p(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Include only one newline instead of two when the parent is a li tag."""
        if convert_as_inline:
            return text

        parent = el.parent
        if parent is not None and parent.name == "li":
            return f"{text}\n"

        if parent is not None and "admonition" in parent.get("class", []):
            ADMONITION_REGEX = re.compile(r"^(Note|Warning|Tip|Danger|Error|Info|Hint|Success)")
            match = ADMONITION_REGEX.match(text)
            if match:
                text = text.replace(match.group(1), f"## {match.group(1)}")
            return f"{text}\n"

        return super().convert_p(el, text, convert_as_inline)

    def convert_hr(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Ignore `hr` tag."""
        return ""
