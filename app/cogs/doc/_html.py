import logging
from collections.abc import Callable, Container, Iterable
from functools import partial
from typing import Any, Literal

from bs4 import BeautifulSoup
from bs4.element import NavigableString, PageElement, SoupStrainer, Tag

from app.cogs.doc import MAX_SIGNATURE_AMOUNT

log = logging.getLogger(__name__)

_SEARCH_END_TAG_ATTRS = (
    'data',
    'function',
    'class',
    'attribute',
    'exception',
    'seealso',
    'section',
    'rubric',
    'sphinxsidebar',
)


class Strainer(SoupStrainer):
    """Subclass of SoupStrainer to allow matching of both `Tag`s and `NavigableString`s."""

    def __init__(self, *, include_strings: bool, **kwargs: Any) -> None:
        self.include_strings = include_strings
        passed_text = kwargs.pop('text', None)
        if passed_text is not None:
            log.warning('`text` is not a supported kwarg in the custom strainer.')
        super().__init__(**kwargs)

    Markup = PageElement | list['Markup']

    def search(self, markup: Markup) -> PageElement | str | None:
        """Extend default SoupStrainer behaviour to allow matching both `Tag`s` and `NavigableString`s."""
        if isinstance(markup, str):
            if not self.name and not self.attrs and self.include_strings:
                return markup
            return None
        return super().search(markup)


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


_find_next_children_until_tag = partial(_find_elements_until_tag, func=partial(BeautifulSoup.find_all, recursive=False))
_find_recursive_children_until_tag = partial(_find_elements_until_tag, func=BeautifulSoup.find_all)
_find_next_siblings_until_tag = partial(_find_elements_until_tag, func=BeautifulSoup.find_next_siblings)
_find_previous_siblings_until_tag = partial(_find_elements_until_tag, func=BeautifulSoup.find_previous_siblings)


def _class_filter_factory(class_names: Iterable[str]) -> Callable[[Tag], bool]:
    """Create callable that returns True when the passed in tag's class is in `class_names` or when it's a table."""
    def match_tag(tag: Tag) -> bool:
        for attr in class_names:
            if attr in tag.get('class', []):
                return True
        return tag.name == 'table'

    return match_tag


def get_general_description(start_element: Tag) -> list[Tag | NavigableString]:
    """Get page content to a table or a tag with its class in `SEARCH_END_TAG_ATTRS`.

    A headerlink tag is attempted to be found to skip repeating the symbol information in the description.
    If it's found it's used as the tag to start the search from instead of the `start_element`.
    """
    child_tags = _find_recursive_children_until_tag(start_element, _class_filter_factory(['section']), limit=100)
    header = next(filter(_class_filter_factory(['headerlink']), child_tags), None)
    start_tag = header.parent if header is not None else start_element
    return _find_next_siblings_until_tag(start_tag, _class_filter_factory(_SEARCH_END_TAG_ATTRS), include_strings=True)


class FilterAttributes:
    """Class to hold attributes for filtering tags in the :func:`get_dd_description` function."""
    def __init__(
            self,
            group: str,
            action: Literal['extract', 'discard', 'ignore', 'return'],
            **kwargs: str
    ) -> None:
        self.group: str = group
        self.action: str = action
        self.kwargs: dict[str, str] = kwargs

    def unpack(self) -> tuple[str, str, dict]:
        return self.group, self.action, self.kwargs


def get_dd_description(
        symbol: PageElement, *, attributes: FilterAttributes = None
) -> list[Tag | NavigableString] | Tag | None:
    """Get the contents of the next dd tag, up to a dt or a dl tag.

    Parameters
    ----------
    symbol : PageElement
        The tag to start the search from.
    attributes : FilterAttributes
        The attributes to use for the search.
    """
    description_tag = symbol.find_next('dd')

    # For Supported Operations Category
    if attributes:
        group, action, kwargs = attributes.unpack()

        if action == 'extract':
            # Only use if you want to remove the tag from the document and return it
            # Remove the tag from the document and return it
            dvmop = description_tag.find(group, **kwargs)
            if dvmop:
                dvmop.extract()
        elif action == 'discard':
            # Only use if you want to remove the tag from the document recursively but not return it
            # Remove the tag from the document
            dvmop = description_tag.find(group, **kwargs)
            if dvmop:
                dvmop.decompose()
        elif action == 'return':
            # Only use if you want to return the tag without removing it from the document
            if not description_tag:
                return None

            # Escape the function early and return the tag
            # This is used for the `__init__` method of the `Symbol` class
            # We only want to get the corresponding tag and not the rest of the description
            dvmop = description_tag.find(group, **kwargs)
            if dvmop:
                return dvmop
        else:
            # Ignore the tag
            # Remove the tag from the document
            dvmop = description_tag.find(group, **kwargs)
            if dvmop:
                dvmop.clear()

    return _find_next_children_until_tag(description_tag, ('dt', 'dl'), include_strings=True)


def _create_markdown_for_element(elem: Tag, template: str = '[{}]({})') -> str:
    """Create a markdown string for a tag."""
    def is_valid(item: Tag, name: str) -> bool:
        return item.name == name

    if is_valid(elem, 'a'):
        tag_name = elem.text
        tag_href = elem['href']

        return template.format(tag_name, tag_href)

    if is_valid(elem, 'strong'):
        return f'**{elem.text}**'

    if is_valid(elem, 'code'):
        return f'`{elem.text}`'


def get_text(element: PageElement | Tag) -> str:
    """Recursively parse an element and its children into a markdown string."""

    if not hasattr(element, 'contents'):
        element.contents = [element]

    text = []
    for child in element.contents:
        if isinstance(child, Tag):
            result = _create_markdown_for_element(child)
            if result:
                text.append(result)
            else:
                text.append(child.text)
        else:
            text.append(child)

    return ' '.join(text)


def get_signatures(start_signature: PageElement, groups: list[str] = ['dd']) -> list[str]:  # type: ignore[no-untyped-def]
    """Collect up to `_MAX_SIGNATURE_AMOUNT` signatures from dt tags around the `start_signature` dt tag.

    First the signatures under the `start_signature` are included;
    if less than 2 are found, tags above the start signature are added to the result if any are present.
    """
    signatures = []
    for element in (
            *reversed(_find_previous_siblings_until_tag(start_signature, groups, limit=2)),
            start_signature,
            *_find_next_siblings_until_tag(start_signature, groups, limit=2),
    )[-MAX_SIGNATURE_AMOUNT:]:
        for tag in element.find_all(_filter_signature_links, recursive=False):
            tag.decompose()

        signature = element.text
        if signature:
            signatures.append(signature)

    return signatures


def _filter_signature_links(tag: Tag) -> bool:
    """Return True if `tag` is a headerlink, or a link to source code; False otherwise."""
    if tag.name == 'a':
        if 'headerlink' in tag.get('class', []):
            return True

        if tag.find(class_='viewcode-link'):
            return True

    return False
