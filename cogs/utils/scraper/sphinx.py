from __future__ import annotations

import enum
import inspect
import io
import os
import re
import traceback
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Generator, NamedTuple, TYPE_CHECKING, Union
from urllib.parse import ParseResult, urljoin, urlparse

import aiohttp
import discord
from bs4 import BeautifulSoup, SoupStrainer, Tag, PageElement
from discord.utils import MISSING
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cogs.utils import fuzzy
from cogs.utils.context import Context
from cogs.utils.helpers import TimeMesh
from cogs.utils.async_utils import executor, AsyncPartialCache, block_if_task_running

if TYPE_CHECKING:
    from bot import Percy


# Storage Classes


class MetaSpec(enum.Enum):
    METHOD = 0
    ATTRIBUTE = 1
    EMPTY = 2


class MethObject(NamedTuple):
    meta: MetaSpec
    name: str
    url: str
    description: str


class RTFMItem(NamedTuple):
    name: str
    url: str
    role: str
    original_name: str | None


@dataclass(frozen=True)
class Documentation:
    name: str
    full_name: str
    description: str
    examples: List[str]
    url: str
    fields: Dict[str, str]
    attributes: List[List[MethObject]]

    def __str__(self):
        return self.name

    def to_embed(self, library: str, color: int | Any):
        description = f"```py\n{self.full_name}\n```\n**Description**\n{self.description}".strip()

        embed = discord.Embed(title=self.name, url=self.url, description=description, color=color)
        embed.set_author(
            name=f"{library} Documentation",
            icon_url="https://cdn.discordapp.com/icons/336642139381301249/3aa641b21acded468308a37eef43d7b3.png",
        )

        for name, field in self.fields.items():
            if len(field) > 1024:  # Embed field limit
                field = field[:1021] + "..."
            embed.add_field(name=name, value=field, inline=False)

        return embed


class SearchResults(NamedTuple):
    results: List[RTFMItem]
    query_time: float

    def to_embed(self, title: str = None, url: str = None, color: int | Any = None):
        embed = discord.Embed(
            title=title,
            url=url,
            description="\n".join(f"**{item.role}** [`{item.name}`]({item.url})" for item in self.results),
            colour=color
        )
        embed.set_footer(text=f"Fetched in {self.query_time * 1000:.2f}ms")

        return embed


# Convertion Functions


class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFFSIZE = 16 * 1024  # 16KB

    def __init__(self, buffer: bytes):
        self.stream = io.BytesIO(buffer)

    def readline(self) -> str:
        return self.stream.readline().decode('utf-8')

    def skipline(self) -> None:
        self.stream.readline()

    def read_compressed_chunks(self) -> Generator[bytes, None, None]:
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self) -> Generator[str, None, None]:
        buf = b''
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b'\n')
            while pos != -1:
                yield buf[:pos].decode('utf-8')
                buf = buf[pos + 1:]
                pos = buf.find(b'\n')


@dataclass(frozen=True)
class _utils:
    scraper: SphinxScraper

    @staticmethod
    def strip_lines(text: str) -> str:
        return re.sub(r"\n+", " ", text)

    @staticmethod
    def convert_method_text(base_info, info_text: str) -> str:
        item_desc = base_info.find("span", class_="pre").text.strip()
        prefix_map = {
            "await": "async",
            "event": "async",
            "async": "async",
            "for": "async",
            "coroutine": "async",
            "classmethod": "async",
            "@": "@",
        }
        info_text = prefix_map.get(item_desc, "def") + " " + info_text
        return info_text

    @staticmethod
    def format_desc(text: List[str]) -> str:
        text = re.sub(r"Example(?: Usage)?:", "", "\n".join(text)).strip()
        return inspect.cleandoc(text)

    def format_attributes(
            self, item: Tag, desc_items: List[Tag], full_url: str, method: str = "ATTRIBUTES"
    ) -> List[MethObject]:
        results: List[MethObject] = []  # type: ignore
        items: List[Tag] = item.find_all("li", class_="py-attribute-table-entry")
        for item_tag in items:
            name = " ".join(x.text for x in item_tag.contents).strip()
            ref = item_tag.find("a", class_="reference internal")
            url = urljoin(full_url, ref.get("href"))

            desc = []
            for index, base_info in enumerate(desc_items):
                info_text = base_info.find("span", class_="descname").text.strip()

                if method == "METHODS":
                    info_text = self.convert_method_text(base_info, info_text)

                if info_text.__eq__(name):
                    desc_item = desc_items.pop(index)

                    for line in desc_item.find("dd").findChildren("p", recursive=False):
                        text = self.strip_lines(self.scraper.parse_text(line, urlparse(url)))
                        desc.append(text)

            meta = MetaSpec.ATTRIBUTE if method == "ATTRIBUTES" else MetaSpec.METHOD

            results.append(MethObject(meta, name, url, self.format_desc(desc)))
        return results

    @staticmethod
    def parse_element(elem: Tag, parsed_url: ParseResult, template: str, only: str | None = None):
        def is_valid(item, name):
            if only is not None:
                return item.name == only and item.name == name
            return item.name == name

        if is_valid(elem, "a"):
            tag_name = elem.text
            tag_href = elem["href"]

            if parsed_url:
                parsed_href = urlparse(tag_href)
                if not parsed_href.netloc:
                    raw_url = parsed_url._replace(params="", fragment="").geturl()
                    tag_href = urljoin(raw_url, tag_href)

            return template.format(tag_name, tag_href)

        if is_valid(elem, "strong"):
            return f"**{elem.text}**"

        if is_valid(elem, "code"):
            return f"`{elem.text}`"


class SphinxScraper(AsyncPartialCache):
    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self._utils: _utils = _utils(self)
        super().__init__(
            input_msg=f"~~ Fetching Documentations ~~", output_msg=f"~~ Finished Fetching Documentations ~~")

        self.RTFM_PAGE_TYPES = {
            "discord.py": "https://discordpy.readthedocs.io/en/stable/",
            "wavelink": "https://wavelink.readthedocs.io/en/stable/",
            "python": "https://docs.python.org/3/",
            "discord4py": "https://docs.discord4py.dev/en/developer/",
            "aiohttp": "https://docs.aiohttp.org/en/stable/"
        }

        self.DOCS_PAGE_TYPES = {  # Only Sphinx
            "discord.py": "https://discordpy.readthedocs.io/en/stable/",
            "wavelink": "https://wavelink.readthedocs.io/en/stable/",
            # "discord4py": "https://docs.discord4py.dev/en/developer/",
        }

        self._docs_cache: Dict[str, List[Documentation]] = {}
        self._rtfm_cache: Dict[str, List[RTFMItem]] = {}

        self.add_task(self.build_rtfm_lookup_table)
        self.add_task(self.build_docs_lookup_cache)

    # +++ Caching Tasks +++

    async def build_docs_lookup_cache(self, recache: bool = False) -> Dict[str, List[Documentation]] | None:
        """Builds the detailed documentation lookup cache."""
        if self._docs_cache and not recache:
            return

        if recache:
            self._docs_cache.clear()

        cache: Dict[str, List[Documentation]] = {}

        for library, lib_url in self.DOCS_PAGE_TYPES.items():
            self.logger.debug(f"Fetching {library}...")
            cache[library] = []

            to_parse = await self.get_raw_html(lib_url)
            soup = BeautifulSoup(to_parse, "lxml")

            manual_section = [
                soup.find("div", class_="index-apis-section"),
                soup.find("section", id="manuals")
            ]

            manual_list = []
            for item in manual_section:
                if item is None:
                    continue
                manual_list.extend(item.find_all("ul", class_="index-featuring-list") or [])
                manual_list.extend(item.find_all("li", class_="toctree-l1") or [])

            manual_as = [manual_li.find("a") for manual_li in manual_list]
            manuals = [(manual.text, urljoin(lib_url, manual.get("href"))) for manual in manual_as]

            for name, manual in manuals:
                try:
                    documentations = await self.soup_manuals(manual)
                    cache[library].extend(documentations)
                except:  # noqa
                    self.logger.error(
                        f'"{library}": Error occurred while trying to cache "`{name}`":\n{traceback.format_exc()}'
                    )
                finally:
                    self._docs_cache[library] = cache[library]

        return self._docs_cache

    async def build_rtfm_lookup_table(self, recache: bool = False):
        """Builds the RTFM lookup table."""
        if self._rtfm_cache and not recache:
            return

        if recache:
            self._rtfm_cache.clear()

        cache: dict[str, List[RTFMItem]] = {}
        for key, page in self.RTFM_PAGE_TYPES.items():
            cache[key] = []
            try:
                async with self.bot.session.get(urljoin(page, "objects.inv")) as resp:
                    if resp.status != 200:
                        raise RuntimeError('Cannot build rtfm lookup table, try again later.')

                    stream = SphinxObjectFileReader(await resp.read())
                    cache[key] = self.parse_object_inv(stream, page)
            except aiohttp.ClientConnectorError:
                continue

        self._rtfm_cache = cache
        await self.load_ddocs_table()

        self.logger.debug(f"RTFM cache built {len(cache)}/{len(self.RTFM_PAGE_TYPES)}")

    async def load_ddocs_table(self):
        """Loads the Discord Developers docs table."""
        DDOCS_URL = 'https://discord.com/developers/docs'

        @executor
        def bs4(content: str) -> List[RTFMItem]:
            strainer = SoupStrainer("li")
            soup = BeautifulSoup(content, "lxml", parse_only=strainer)
            items = soup.find_all("a")

            res: List[RTFMItem] = []
            for item in items:
                res.append(RTFMItem(item.text, urljoin('https://discord.com', item.get("href")), 'label', None))
            return res

        results = await bs4(await self.get_raw_html(DDOCS_URL))
        self._rtfm_cache["ddocs"] = results

    # +++ Search +++

    @block_if_task_running(build_rtfm_lookup_table)
    async def search(self, obj: Optional[str], library: str, limit: int) -> SearchResults:
        """Searches the documentation cache of the given ``library`` for the given ``query``."""
        with TimeMesh() as timer:
            obj = re.sub(r'^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)', r'\1', obj)

            if library == "discord.py":
                q = obj.lower()
                for name in dir(discord.abc.Messageable):
                    if name[0] == '_':
                        continue
                    if q == name:
                        obj = f'abc.Messageable.{name}'
                        break

            matches = fuzzy.finder(obj, self._rtfm_cache[library], key=lambda x: x.name)

        return SearchResults(matches[:limit], timer.time)

    @block_if_task_running(build_rtfm_lookup_table)
    async def do_rtfm(self, ctx: Context, obj: Optional[str], library: str = "discord.py", limit: int = None):
        """Searches the documentation for the specified ``library`` and ``query``."""
        results = await self.search(obj, library, limit=limit)

        if len(results.results) == 0:
            await ctx.reply("<:redTick:1079249771975413910> No results matching that query found.",
                            mention_author=False)
            return

        await ctx.reply(
            embed=results.to_embed(title=f'{library}: "{obj}"',
                                   url=urljoin(self.RTFM_PAGE_TYPES[library], f"search.html?q={obj}"),
                                   color=self.bot.colour.darker_red()), mention_author=False
        )

    @block_if_task_running(build_docs_lookup_cache)
    async def search_doc(self, obj: str, library: str) -> Documentation | None:
        """Retrieves the documentation for an object from the cache."""
        result = discord.utils.get(self._docs_cache[library], name=obj)
        if result is False:
            return

        return result

    # +++ Raw Parsing +++

    @staticmethod
    def parse_object_inv(stream: SphinxObjectFileReader, url: str) -> List[RTFMItem]:
        """Parses a Sphinx inventory stream."""

        result: List[RTFMItem] = []
        inv_version = stream.readline().rstrip()

        if inv_version != '# Sphinx inventory version 2':
            return result
            # raise RuntimeError('Invalid objects.inv file version.')

        projname = stream.readline().rstrip()[11:]
        version = stream.readline().rstrip()[11:]  # noqa: F841

        line = stream.readline()
        if 'zlib' not in line:
            raise RuntimeError('Invalid objects.inv file, not z-lib compatible.')

        entry_regex = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)')
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(':')
            if directive == 'py:module' and name in result:
                continue

            if directive == 'std:doc':
                subdirective = 'label'

            if subdirective == 'label':
                continue

            if location.endswith('$'):
                location = location[:-1] + name

            key = name if dispname == '-' else dispname
            original_name = key

            if projname == 'discord.py':
                key = key.replace('discord.ext.commands.', '').replace('discord.', '')

            result.append(RTFMItem(key, os.path.join(url, location), subdirective, original_name))

        return result

    async def get_raw_html(self, url: str, wait_for: bool = False) -> Optional[str]:
        """Get the HTML content of a page."""
        page = await self.bot.browser.new_page()

        try:
            page.set_default_timeout(0)
            await page.goto(url)

            if wait_for:
                try:
                    await page.wait_for_load_state("networkidle", timeout=0)
                except PlaywrightTimeoutError:
                    pass

            return await page.content()
        finally:
            await page.close()

    def parse_text(
            self, element: Union[Tag, PageElement], parsed_url: ParseResult, template: str = "[`{}`]({})"
    ) -> str:
        """Recursively parse an element and its children into a markdown string."""

        if not hasattr(element, "contents"):
            element.contents = [element]

        text = []
        for child in element.contents:
            if isinstance(child, Tag):
                result = self._utils.parse_element(child, parsed_url, template)
                if result:
                    text.append(result)
                else:
                    text.append(child.text)
            else:
                text.append(child)

        return " ".join(text)

    @executor
    def soup_documentation(self, element: Tag, page_url: str) -> Documentation | None:
        """Scrapes the documentation from the given element."""

        try:
            url = element.find("a", class_="headerlink").get("href")
        except AttributeError:
            return Documentation(*[MISSING for _ in range(7)])

        full_url = urljoin(page_url, url)
        parsed_url = urlparse(full_url)

        parent = element.parent
        full_name = element.text.replace("coroutine", "async def").replace("classmethod ", "").rstrip("#")

        name = element.attrs.get("id")
        documentation = parent.find("dd")
        description = []
        examples = []

        attributes: List[List[MethObject]] = []
        attribute_list: Tag = parent.find("div", class_="py-attribute-table")
        desc_attribute_list: List[Tag] = documentation.find_all("dl", class_="py")
        if attribute_list:
            items: List[Tag] = attribute_list.findChildren("div", class_="py-attribute-table-column", recursive=False)
            if items:
                attributes.append(self._utils.format_attributes(items[0], desc_attribute_list, full_url))
                if len(items) >= 2:
                    attributes.append(
                        self._utils.format_attributes(items[1], desc_attribute_list, full_url, method="METHODS"))

        fields = {}

        if supported_operations := documentation.find("div", class_="operations", recursive=False):
            items: List[tuple[str, str]] = []
            for supported_operation in supported_operations.find_all("dl", class_="describe", recursive=False):
                operation = supported_operation.find("span", class_="descname").text.strip()
                text = self.parse_text(supported_operation.find("dd", recursive=False), parsed_url).strip()
                items.append((operation, text))

            if items:
                fields["Supported Operations"] = "\n".join(
                    f"`{operation}` - {self._utils.strip_lines(desc)}" for operation, desc in items)

        field_list = documentation.find("dl", class_="field-list", recursive=False)
        if field_list:
            for field in field_list.find_all("dt", recursive=False):
                key = field.text
                values: List[Tag] = [x for x in field.next_siblings if isinstance(x, Tag)][0].find_all("p")

                elements: List[List[str]] = []
                for value in values:
                    texts = [self.parse_text(element, parsed_url) for element in value.contents]
                    elements.append(texts)

                fields[key] = "\n".join("".join(element) for element in elements)

        for child in documentation.find_all("p", recursive=False):
            if child.attrs.get("class"):
                break

            elements: list[str] = [self.parse_text(element, parsed_url) for element in child.contents]
            description.append("".join(elements))

        for child in documentation.find_all("div", class_=["highlight-python3", "highlight-default"], recursive=False):
            examples.append(child.find("pre").text)

        if version_modified := documentation.find("div", class_="versionchanged"):
            for line in version_modified.find_all("p", recursive=False):
                text = self._utils.strip_lines(self.parse_text(line, parsed_url))
                description.append(text)

        description = "\n\n".join(description).replace("Example:", "").strip()
        full_name = full_name.replace("¶", "").strip()

        return Documentation(
            name=name,
            full_name=full_name,
            description=description,
            examples=examples,
            url=parsed_url.geturl(),
            fields=fields,
            attributes=attributes,
        )

    async def soup_manuals(self, url: str) -> List[Documentation]:
        """Get all manual documentations from the given url.
        URL needs to derive from a Sphinx documentation."""

        @executor
        def bs4(content: str):
            strainer = SoupStrainer("dl")
            soup = BeautifulSoup(content, "lxml", parse_only=strainer)
            return soup.find_all("dt")

        elements = await bs4(await self.get_raw_html(url))
        results = []
        for element in elements:
            result = await self.soup_documentation(element, url)  # type: ignore
            results.append(result)

        return results
