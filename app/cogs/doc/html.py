import logging
import re
from collections.abc import Callable, Container, Iterable
from functools import partial
from typing import Any
from urllib.parse import urljoin

import markdownify
from bs4 import BeautifulSoup
from bs4.element import NavigableString, PageElement, SoupStrainer, Tag

log = logging.getLogger(__name__)

# Because markdownify is outdated, we need to update the whitespace regex
# Also See https://github.com/matthewwithanm/python-markdownify/issues/31
markdownify.whitespace_re = re.compile(r"[\r\n\s\t ]+")

#: Tags/classes that terminate a sibling-walk for the *general* (sectionless) description.
_SEARCH_END_TAG_ATTRS = (
    "data",
    "function",
    "class",
    "attribute",
    "exception",
    "seealso",
    "section",
    "rubric",
    "sphinxsidebar",
)

#: Wrapper ``div`` classes that hold a Sphinx version directive.
VERSION_DIV_CLASSES = ("versionadded", "versionchanged", "deprecated", "versionremoved")
#: ``div`` classes that should be lifted out of the body and shown as a callout banner.
_ADMONITION_CLASSES = ("admonition", "seealso")
_WHITESPACE_RE = re.compile(r"[ \t]*\n[ \t]*")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class Strainer(SoupStrainer):
    """Subclass of SoupStrainer to allow matching of both `Tag`s and `NavigableString`s."""

    def __init__(self, *, include_strings: bool, **kwargs: Any) -> None:
        self.include_strings = include_strings
        passed_text = kwargs.pop("text", None)
        if passed_text is not None:
            log.warning("`text` is not a supported kwarg in the custom strainer.")
        super().__init__(**kwargs)

    Markup = PageElement | list[PageElement] | str

    def search(self, markup: Markup) -> PageElement | str | None:
        """Extend default SoupStrainer behaviour to allow matching both `Tag`s` and `NavigableString`s."""
        if isinstance(markup, str):
            if not self.name and not self.attrs and self.include_strings:  # type: ignore
                return markup
            return None
        return super().search(markup)  # type: ignore


def _find_elements_until_tag(
    start_element: PageElement,
    end_tag_filter: Container[str] | Callable[[Tag], bool],
    *,
    func: Callable,
    include_strings: bool = False,
    limit: int | None = None,
) -> list[Tag | NavigableString]:
    """
    Get all elements up to `limit` or until a tag matching `end_tag_filter` is found.

    `end_tag_filter` can be either a container of string names to check against,
    or a filtering callable that's applied to tags.

    When `include_strings` is True, `NavigableString`s from the document will be included in the result along `Tag`s.

    `func` takes in a BeautifulSoup unbound method for finding multiple elements, such as `BeautifulSoup.find_all`.
    The method is then iterated over and all elements until the matching tag or the limit are added to the return list.
    """
    use_container_filter = not callable(end_tag_filter)
    elements = []

    for element in func(start_element, name=Strainer(include_strings=include_strings), limit=limit):
        if isinstance(element, Tag):
            if use_container_filter:
                if element.name in end_tag_filter:
                    break
            elif end_tag_filter(element):
                break
        elements.append(element)

    return elements


_find_recursive_children_until_tag = partial(_find_elements_until_tag, func=BeautifulSoup.find_all)
_find_next_siblings_until_tag = partial(_find_elements_until_tag, func=BeautifulSoup.find_next_siblings)
_find_previous_siblings_until_tag = partial(_find_elements_until_tag, func=BeautifulSoup.find_previous_siblings)


def _class_filter_factory(class_names: Iterable[str]) -> Callable[[Tag], bool]:
    """Create callable that returns True when the passed in tag's class is in `class_names` or when it's a table."""

    def match_tag(tag: Tag) -> bool:
        for attr in class_names:
            if attr in tag.get("class", []):  # type: ignore
                return True
        return tag.name == "table"

    return match_tag


def get_general_description(start_element: Tag) -> list[Tag | NavigableString]:
    """Get page content to a table or a tag with its class in `SEARCH_END_TAG_ATTRS`.

    A headerlink tag is attempted to be found to skip repeating the symbol information in the description.
    If it's found it's used as the tag to start the search from instead of the `start_element`.
    """
    child_tags = _find_recursive_children_until_tag(start_element, _class_filter_factory(["section"]), limit=100)
    header = next(filter(_class_filter_factory(["headerlink"]), child_tags), None)
    start_tag = header.parent if header is not None else start_element
    return _find_next_siblings_until_tag(start_tag, _class_filter_factory(_SEARCH_END_TAG_ATTRS), include_strings=True)


def get_signatures(start_signature: PageElement) -> list[str]:
    """Collect up to `MAX_SIGNATURE_AMOUNT` signatures from `dt` tags around the `start_signature` `dt` tag.

    First the signatures under the `start_signature` are included;
    if less than 2 are found, tags above the start signature are added to the result if any are present.
    """
    # Imported lazily to keep this module free of intra-package import cycles.
    from .models import MAX_SIGNATURE_AMOUNT

    signatures = []
    for element in (
        *reversed(_find_previous_siblings_until_tag(start_signature, ("dd",), limit=2)),
        start_signature,
        *_find_next_siblings_until_tag(start_signature, ("dd",), limit=2),
    )[-MAX_SIGNATURE_AMOUNT:]:
        for tag in element.find_all(_filter_signature_links, recursive=False):
            tag.decompose()

        signature = _collapse_whitespace(element.text)
        if signature:
            signatures.append(signature)

    return signatures


def _filter_signature_links(tag: Tag) -> bool:
    """Return True if `tag` is a headerlink, or a link to source code; False otherwise."""
    if tag.name == "a":
        if "headerlink" in tag.get("class", []):  # type: ignore
            return True

        if tag.find(class_="viewcode-link"):
            return True

    return False


def is_member_definition(tag: Tag) -> bool:
    """Return True if `tag` is a `dl` documenting a *nested* member (its own symbol), not a field list."""
    if tag.name != "dl":
        return False
    classes = tag.get("class", [])  # type: ignore
    if "field-list" in classes:
        return False
    # A documented Python member carries a ``py`` domain class and an id'd ``dt``.
    if "py" in classes:
        return True
    first_dt = tag.find("dt", recursive=False)
    return bool(first_dt and first_dt.get("id"))


def is_version_div(tag: Tag) -> bool:
    """Return True if `tag` is a Sphinx ``versionadded`` / ``versionchanged`` / ``deprecated`` directive."""
    if tag.name != "div":
        return False
    return any(cls in tag.get("class", []) for cls in VERSION_DIV_CLASSES)  # type: ignore


def is_admonition(tag: Tag) -> bool:
    """Return True if `tag` is an admonition / see-also callout block."""
    if tag.name != "div":
        return False
    return any(cls in tag.get("class", []) for cls in _ADMONITION_CLASSES)  # type: ignore


def admonition_kind(tag: Tag) -> str:
    """Return the lowercase kind of an admonition tag (e.g. ``note``, ``warning``, ``see also``)."""
    classes: list[str] = tag.get("class", [])  # type: ignore
    for cls in classes:
        if cls not in ("admonition",):
            return cls.replace("seealso", "see also").replace("-", " ").lower()
    return "note"


def clean_version_text(tag: Tag) -> str:
    """Collapse a version directive into a single tidy line such as ``New in version 2.0``."""
    text = _collapse_whitespace(tag.get_text(" ", strip=True))
    return text.rstrip(".") if text else ""


def format_version_note(note: str) -> str:
    """Render a version note (``New in version 2.0``) as a tabbed subtext line on its own row."""
    return f"\n-# \N{DOWNWARDS ARROW WITH TIP RIGHTWARDS} {note}\n"


#: Maps a Sphinx domain to the Discord code-fence language that highlights it best.
_FENCE_LANGUAGES = {
    "py": "py",
    "python": "py",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "js": "js",
    "javascript": "js",
    "ts": "ts",
    "rust": "rust",
    "go": "go",
}


def fence_language(domain: str) -> str:
    """Return the code-fence language for a Sphinx `domain` (``""`` when none fits)."""
    return _FENCE_LANGUAGES.get(domain.lower(), "")


def clean_signature(term: Tag) -> str:
    """Extract a single collapsed signature string from a ``dt`` term, dropping its permalink anchors.

    Operates on a copy so the shared page soup is left intact. Text nodes are joined without an extra
    separator — Sphinx emits explicit whitespace spans — so ``void f ( int )`` stays ``void f(int)``.
    """
    term = term.__copy__()
    for anchor in term.find_all(_filter_signature_links, recursive=False):
        anchor.decompose()
    for line_break in term.find_all("br"):
        line_break.replace_with(" ")
    return _collapse_whitespace(term.get_text())


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalise_markdown(markdown: str) -> str:
    """Tidy converter output: collapse trailing spaces and runs of blank lines."""
    markdown = _WHITESPACE_RE.sub("\n", markdown)
    markdown = _BLANK_LINES_RE.sub("\n\n", markdown)
    return markdown.strip()


class DocMarkdownConverter(markdownify.MarkdownConverter):
    """Subclass markdownify's MarkdownConverter to provide custom conversion methods."""

    def __init__(self, *, page_url: str, **options: Any) -> None:
        options.setdefault("bullets", "-")
        super().__init__(**options)
        self.page_url = page_url

    def convert_li(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Fix markdownify's erroneous indexing in ol tags."""
        parent = el.parent
        if parent is not None and parent.name == "ol":
            li_tags = parent.find_all("li", recursive=False)
            try:
                start = int(parent.get("start", 1))
            except (TypeError, ValueError):
                start = 1
            bullet = f"{li_tags.index(el) + start}."
        else:
            depth = -1
            cursor: Tag | None = el
            while cursor:
                if cursor.name == "ul":
                    depth += 1
                cursor = cursor.parent
            bullets = self.options["bullets"]
            bullet = bullets[depth % len(bullets)]
        return f"{bullet} {text.strip()}\n"

    def _convert_hn(self, n: int, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Convert ``h1`` to ``h6`` tags to bold text instead of `#` headings (Discord renders those huge)."""
        if convert_as_inline:
            return text
        return f"**{text.strip()}**\n\n"

    def convert_code(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Undo `markdownify`s underscore escaping and keep inline code as backticks."""
        if el.parent is not None and el.parent.name in ("pre",):
            return text
        text = text.replace("\\", "").strip("`")
        return f"`{text}`" if text else ""

    def convert_pre(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Wrap any codeblocks in `py` for syntax highlighting."""
        code = "".join(el.strings).rstrip()
        if not code:
            return ""
        return f"```py\n{code}\n```\n"

    def convert_a(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Resolve relative URLs to `self.page_url`."""
        prefix, suffix, text = markdownify.chomp(text)
        if not text:
            return ""

        el["href"] = urljoin(self.page_url, el.get("href", ""))

        href = el.get("href")
        title = el.get("title")

        if (
            self.options["autolinks"]
            and text.replace(r"\_", "_") == href
            and not title
            and not self.options["default_title"]
        ):
            # Bare self-link: render as an autolink instead of a redundant ``[url](url)``.
            return f"<{href}>"

        return f"{prefix}[{text}]({href}){suffix}" if href else text

    def convert_p(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Include only one newline instead of two when the parent is a `li` tag."""
        if convert_as_inline:
            return text

        parent = el.parent
        if parent is not None and parent.name == "li":
            return f"{text.strip()}\n"
        return super().convert_p(el, text, convert_as_inline)

    def convert_div(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Render Sphinx version directives as a tabbed subtext note; pass every other div through.

        This is what makes a ``New in version …`` note that lives *inside* a field value (e.g. a single
        ``Parameters`` entry) tab neatly under that entry, exactly like the *Supported Operations* notes,
        instead of being inlined into the running text.
        """
        if is_version_div(el):
            note = clean_version_text(el)
            return format_version_note(note) if note else ""
        return text

    def convert_hr(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        """Ignore `hr` tag."""
        return ""


def elements_to_markdown(
    elements: Iterable[Tag | NavigableString],
    converter: DocMarkdownConverter,
) -> str:
    """Convert a flat sequence of soup nodes into a single tidy markdown string."""
    parts: list[str] = []
    for element in elements:
        if isinstance(element, Tag):
            parts.append(converter.process_tag(element, convert_as_inline=False))
        elif isinstance(element, NavigableString):
            parts.append(converter.process_text(element))
    return normalise_markdown("".join(parts))
