"""Tests for the documentation scraper (``app/cogs/doc``).

These exercise the structured HTML parsing in isolation (no bot, no network) using representative
Sphinx markup in the shape discord.py / CPython emit.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from app.cogs.doc.engine import parse_symbol
from app.cogs.doc.html import clean_signature, fence_language
from app.cogs.doc.models import DocItem

CLASS_HTML = """
<dl class="py class">
<dt id="discord.Embed">
<em class="property"><span class="pre">class </span></em>
<span class="sig-prename descclassname"><span class="pre">discord.</span></span>
<span class="sig-name descname"><span class="pre">Embed</span></span>
<span class="sig-paren">(</span><em class="sig-param">*</em>, <em class="sig-param">title=None</em><span class="sig-paren">)</span>
<a class="headerlink" href="#discord.Embed">¶</a>
</dt>
<dd>
<p>Represents a Discord embed.</p>
<div class="operations">
<p class="rubric">Supported Operations</p>
<dl class="describe">
<dt><span class="pre">x</span> <span class="pre">==</span> <span class="pre">y</span></dt>
<dd><p>Checks if two embeds are equal.</p></dd>
<dt><span class="pre">len(x)</span></dt>
<dd><p>Returns the total size of the embed.</p>
<div class="versionadded"><p><span class="versionmodified added">New in version 2.0.</span></p></div>
</dd>
</dl>
</div>
<div class="versionadded"><p><span class="versionmodified added">New in version 1.5.</span></p></div>
<dl class="field-list simple">
<dt class="field-odd">Parameters</dt>
<dd class="field-odd"><ul class="simple">
<li><p><strong>title</strong> (<a class="reference internal" href="https://docs.python.org/3/library/stdtypes.html#str"><code class="xref"><span class="pre">str</span></code></a>) – The title of the embed.</p></li>
</ul></dd>
<dt class="field-even">Raises</dt>
<dd class="field-even"><ul class="simple"><li><p><strong>TypeError</strong> – Something went wrong.</p></li></ul></dd>
</dl>
<dl class="py method">
<dt id="discord.Embed.copy"><span class="sig-name descname"><span class="pre">copy</span></span></dt>
<dd><p>This is a nested member and must NOT leak into the class description.</p></dd>
</dl>
</dd>
</dl>
"""

METHOD_HTML = """
<dl class="py method">
<dt id="discord.Client.start">
<span class="sig-name descname"><span class="pre">start</span></span>
<span class="sig-paren">(</span><em class="sig-param">token</em><span class="sig-paren">)</span>
<a class="headerlink" href="#discord.Client.start">¶</a>
</dt>
<dd>
<p>A shorthand coroutine for connecting.</p>
<div class="admonition note">
<p class="admonition-title">Note</p>
<p>This function must be the last one called.</p>
</div>
<dl class="field-list simple">
<dt class="field-odd">Parameters</dt>
<dd class="field-odd"><ul class="simple"><li><p><strong>token</strong> – the authentication token.</p></li></ul></dd>
<dt class="field-even">Returns</dt>
<dd class="field-even"><p>Nothing useful.</p></dd>
<dt class="field-odd">Return type</dt>
<dd class="field-odd"><p>None</p></dd>
</dl>
</dd>
</dl>
"""


# discord.py emits each ``.. describe::`` as its own ``dl.describe`` block within ``div.operations``.
MULTI_DL_OPERATIONS_HTML = """
<dl class="py class">
<dt id="discord.Colour"><span class="sig-name descname"><span class="pre">Colour</span></span></dt>
<dd>
<p>Represents a colour.</p>
<div class="operations">
<p class="rubric">Supported Operations</p>
<dl class="describe"><dt><span class="pre">x</span> <span class="pre">==</span> <span class="pre">y</span></dt><dd><p>Checks equality.</p></dd></dl>
<dl class="describe"><dt><span class="pre">hash(x)</span></dt><dd><p>Returns the colour's hash.</p></dd></dl>
<dl class="describe"><dt><span class="pre">str(x)</span></dt><dd><p>Returns the hex format.</p>
<div class="versionchanged"><p><span class="versionmodified changed">Changed in version 2.0.</span></p></div>
</dd></dl>
</div>
</dd>
</dl>
"""


# A ``New in version`` directive nested inside a single Parameters entry.
FIELD_VERSION_HTML = """
<dl class="py method">
<dt id="discord.Guild.create_text_channel"><span class="sig-name descname"><span class="pre">create_text_channel</span></span></dt>
<dd>
<p>Creates a text channel.</p>
<dl class="field-list simple">
<dt class="field-odd">Parameters</dt>
<dd class="field-odd"><ul class="simple">
<li><p><strong>name</strong> – the channel name.</p></li>
<li><p><strong>reason</strong> – the audit log reason.</p>
<div class="versionadded"><p><span class="versionmodified added">New in version 1.3.</span></p></div>
</li>
</ul></dd>
</dl>
</dd>
</dl>
"""


# CPython C-API category page ("section" lookup) with member structs/functions, mirroring the real
# DOM: explicit ``<span class="w"> </span>`` whitespace spans, a trailing ``<br/>`` and ¶ headerlinks.
C_SECTION_HTML = (
    '<section id="create-config">'
    '<h3>Create Config<a class="headerlink" href="#create-config">¶</a></h3>'
    '<dl class="c struct">'
    '<dt class="sig sig-object c" id="c.PyInitConfig">'
    '<span class="k"><span class="pre">struct</span></span><span class="w"> </span>'
    '<span class="sig-name descname"><span class="n"><span class="pre">PyInitConfig</span></span></span>'
    '<a class="headerlink" href="#c.PyInitConfig">¶</a><br/></dt>'
    "<dd><p>Opaque structure to configure the Python initialization.</p></dd>"
    "</dl>"
    '<dl class="c function">'
    '<dt class="sig sig-object c" id="c.PyInitConfig_Free">'
    '<span class="kt"><span class="pre">void</span></span><span class="w"> </span>'
    '<span class="sig-name descname"><span class="n"><span class="pre">PyInitConfig_Free</span></span></span>'
    '<span class="sig-paren">(</span>'
    '<a class="reference internal" href="#c.PyInitConfig"><span class="n"><span class="pre">PyInitConfig</span></span></a>'
    '<span class="w"> </span><span class="p"><span class="pre">*</span></span><span class="n"><span class="pre">config</span></span>'
    '<span class="sig-paren">)</span><a class="headerlink" href="#c.PyInitConfig_Free">¶</a><br/></dt>'
    "<dd><p>Free memory of the configuration <em>config</em>.</p>"
    '<div class="versionadded"><p><span class="versionmodified added">Added in version 3.14.</span></p></div>'
    "</dd>"
    "</dl>"
    "</section>"
)

# A landing section that contains only sub-sections (no prose, no members).
LANDING_SECTION_HTML = """
<section id="python-initialization-configuration">
<h1>Python Initialization Configuration<a class="headerlink" href="#python-initialization-configuration">¶</a></h1>
<section id="pyconfig-c-api"><h2>PyConfig C API<a class="headerlink" href="#pyconfig-c-api">¶</a></h2><p>x</p></section>
<section id="get-options"><h2>Get Options<a class="headerlink" href="#get-options">¶</a></h2><p>y</p></section>
</section>
"""


# A whole-page (anchorless) entry: ``role="main"`` wrapping the page's primary section.
ANCHORLESS_PAGE_HTML = """
<article role="main">
<section id="image-module">
<h1>Image module<a class="headerlink" href="#image-module">¶</a></h1>
<p>The Image module provides a class with the same name.</p>
<dl class="py function">
<dt class="sig sig-object py" id="PIL.Image.open"><span class="sig-name descname"><span class="pre">open</span></span></dt>
<dd><p>Opens and identifies the given image file.</p></dd>
</dl>
</section>
</article>
"""


# numpydoc renders Parameters/Returns as a nested ``name : type`` definition list whose ``:`` is
# CSS-only (a ``<span class="classifier">``), and decorates the field name with a ``<span class="colon">``.
NUMPYDOC_HTML = """
<dl class="py function">
<dt id="numpy.emath.arccos"><span class="sig-name descname"><span class="pre">arccos</span></span></dt>
<dd>
<p>Compute the inverse cosine of x.</p>
<dl class="field-list simple">
<dt class="field-odd">Parameters<span class="colon">:</span></dt>
<dd class="field-odd"><dl class="simple">
<dt><strong>x</strong><span class="classifier">array_like or scalar</span></dt>
<dd><p>The value(s) whose arccos is (are) required.</p></dd>
</dl></dd>
<dt class="field-even">Returns<span class="colon">:</span></dt>
<dd class="field-even"><dl class="simple">
<dt><strong>out</strong><span class="classifier">ndarray or scalar</span></dt>
<dd><p>The inverse cosine(s) of the x value(s).</p></dd>
</dl></dd>
</dl>
</dd>
</dl>
"""


# A section whose member documents a whole method (lead paragraph + a prose list), like pygit2's
# commit-log tutorial — the member summary must stay a single line, not inline the whole body.
RICH_MEMBER_SECTION_HTML = """
<section id="commit-log">
<h1>Commit log<a class="headerlink" href="#commit-log">¶</a></h1>
<dl class="py method">
<dt class="sig sig-object py" id="Repository.walk"><span class="sig-name descname"><span class="pre">Repository.walk</span></span></dt>
<dd>
<p>Start traversing the history from the given commit.</p>
<ul class="simple">
<li><p>NONE. Sort the output with the same default method from git.</p></li>
<li><p>TOPOLOGICAL. Sort the parents before children.</p></li>
</ul>
</dd>
</dl>
</section>
"""


def _item(symbol_id: str) -> DocItem:
    return DocItem(
        package="python",
        group="class",
        base_url="https://example.com/",
        relative_url_path="api.html",
        symbol_id=symbol_id,
    )


async def test_parse_class_extracts_all_sections() -> None:
    soup = BeautifulSoup(CLASS_HTML, "lxml")
    result = await parse_symbol(soup, _item("discord.Embed"))

    assert result is not None
    assert "Represents a Discord embed." in result.description
    # The nested member must not bleed into the class description.
    assert "nested member" not in result.description

    # Signature is captured and class keyword retained.
    assert result.signatures
    assert "Embed" in result.signatures[0]


async def test_supported_operations_tab_version_under_entry() -> None:
    soup = BeautifulSoup(CLASS_HTML, "lxml")
    result = await parse_symbol(soup, _item("discord.Embed"))
    assert result is not None

    names = [op.name for op in result.operations]
    assert names == ["x == y", "len(x)"]

    eq, length = result.operations
    assert eq.version is None
    assert "Checks if two embeds are equal" in eq.description
    # The "New in version 2.0" note belongs *under* the len(x) operation, not the class body.
    assert length.version == "New in version 2.0"


async def test_class_version_change_and_fields() -> None:
    soup = BeautifulSoup(CLASS_HTML, "lxml")
    result = await parse_symbol(soup, _item("discord.Embed"))
    assert result is not None

    assert "New in version 1.5" in result.version_changes

    field_names = [f.name for f in result.fields]
    assert "Parameters" in field_names
    assert "Raises" in field_names
    # The supported-operations rubric must not be duplicated as a field.
    assert not any("operation" in name.lower() for name in field_names)

    params = next(f for f in result.fields if f.name == "Parameters")
    assert "**title**" in params.value
    # The relative type link is resolved to an absolute URL.
    assert "https://docs.python.org/3/library/stdtypes.html#str" in params.value


async def test_method_note_banner_and_return_fields() -> None:
    soup = BeautifulSoup(METHOD_HTML, "lxml")
    item = _item("discord.Client.start")
    item.group = "method"
    result = await parse_symbol(soup, item)
    assert result is not None

    assert len(result.admonitions) == 1
    note = result.admonitions[0]
    assert note.kind == "note"
    assert note.title == "Note"
    assert "last one called" in note.body
    # The note is lifted out of the running description.
    assert "last one called" not in result.description

    field_names = [f.name for f in result.fields]
    assert field_names == ["Parameters", "Returns", "Return type"]


async def test_operations_across_multiple_describe_blocks() -> None:
    soup = BeautifulSoup(MULTI_DL_OPERATIONS_HTML, "lxml")
    result = await parse_symbol(soup, _item("discord.Colour"))
    assert result is not None

    names = [op.name for op in result.operations]
    assert names == ["x == y", "hash(x)", "str(x)"]
    # The version note nests under only the operation it belongs to.
    assert result.operations[-1].version == "Changed in version 2.0"
    assert all(op.version is None for op in result.operations[:-1])


async def test_version_note_inside_parameter_is_tabbed() -> None:
    soup = BeautifulSoup(FIELD_VERSION_HTML, "lxml")
    item = _item("discord.Guild.create_text_channel")
    item.group = "method"
    result = await parse_symbol(soup, item)
    assert result is not None

    params = next(f for f in result.fields if f.name == "Parameters")
    # The version note is rendered as a tabbed subtext line, not inlined into the description text.
    assert "-# \N{DOWNWARDS ARROW WITH TIP RIGHTWARDS} New in version 1.3" in params.value
    assert "New in version 1.3." not in params.value.replace("-# \N{DOWNWARDS ARROW WITH TIP RIGHTWARDS} ", "")
    # The subtext marker must start its own line so Discord renders it as subtext.
    assert "\n-# " in params.value


async def test_section_lookup_renders_members_as_list() -> None:
    soup = BeautifulSoup(C_SECTION_HTML, "lxml")
    item = _item("create-config")
    item.group = "label"
    result = await parse_symbol(soup, item)
    assert result is not None

    # The raw section id is upgraded to the human title.
    assert result.title == "Create Config"

    signatures = [m.signature for m in result.members]
    assert signatures == ["struct PyInitConfig", "void PyInitConfig_Free(PyInitConfig *config)"]
    # Members carry their Sphinx domain (so the renderer can highlight them correctly).
    assert {m.domain for m in result.members} == {"c"}

    free = result.members[1]
    assert "Free memory of the configuration" in free.description
    # A version note nested under a member is tabbed onto that member, not the section.
    assert free.version == "Added in version 3.14"
    assert not result.version_changes


async def test_landing_section_falls_back_to_table_of_contents() -> None:
    soup = BeautifulSoup(LANDING_SECTION_HTML, "lxml")
    item = _item("python-initialization-configuration")
    item.group = "label"
    result = await parse_symbol(soup, item)
    assert result is not None

    assert result.title == "Python Initialization Configuration"
    assert not result.members
    # A pure landing section offers a clickable table of contents instead of an empty card.
    assert not result.is_empty()
    assert "**In this section**" in result.description
    assert "[PyConfig C API](" in result.description
    assert "#pyconfig-c-api" in result.description


def test_clean_signature_collapses_whitespace_spans() -> None:
    dt = BeautifulSoup(C_SECTION_HTML, "lxml").find(id="c.PyInitConfig_Free")
    # No stray spaces around parens/asterisks, and the ¶ permalink + <br> are gone.
    assert clean_signature(dt) == "void PyInitConfig_Free(PyInitConfig *config)"


def test_fence_language_maps_domains() -> None:
    assert fence_language("c") == "c"
    assert fence_language("py") == "py"
    assert fence_language("cpp") == "cpp"
    # Unknown / prose domains get no language (a plain code block).
    assert fence_language("std") == ""


async def test_anchorless_page_entry_parses_main_section() -> None:
    soup = BeautifulSoup(ANCHORLESS_PAGE_HTML, "lxml")
    # std:doc / module entries have an empty symbol_id (no #anchor in the inventory location).
    item = DocItem("pillow", "doc", "https://x/", "reference/Image.html", "", domain="std", name="reference/Image")
    result = await parse_symbol(soup, item)
    assert result is not None

    assert result.title == "Image module"
    assert "provides a class" in result.description
    assert [m.signature for m in result.members] == ["open"]
    assert not result.is_empty()


def test_docitem_display_name_and_urls_for_anchorless_entry() -> None:
    item = DocItem("pillow", "doc", "https://x/", "reference/Image.html", "", name="reference/Image")
    # The label must never be empty (Discord rejects empty select-option labels).
    assert item.display_name == "reference/Image"
    # No anchor → the button/link url is the bare page (no dangling '#').
    assert item.anchor_url == "https://x/reference/Image.html"
    assert "#" not in item.url


def test_docitem_display_name_prefers_inventory_name() -> None:
    # A C function whose anchor carries a domain prefix renders under the cleaner inventory name.
    item = DocItem("python", "function", "https://x/", "api.html", "c.PyConfig_Read", name="PyConfig_Read")
    assert item.display_name == "PyConfig_Read"
    assert item.anchor_url == "https://x/api.html#c.PyConfig_Read"


async def test_numpydoc_parameters_separate_name_and_type() -> None:
    soup = BeautifulSoup(NUMPYDOC_HTML, "lxml")
    item = _item("numpy.emath.arccos")
    item.group = "function"
    result = await parse_symbol(soup, item)
    assert result is not None

    # The field name's CSS colon span is stripped.
    field_names = [f.name for f in result.fields]
    assert field_names == ["Parameters", "Returns"]

    params = next(f for f in result.fields if f.name == "Parameters")
    # Name and type must be separated, never glued into "xarray_like or scalar".
    assert "xarray_like" not in params.value
    assert "**x** (*array_like or scalar*)" in params.value
    assert "The value(s) whose arccos is (are) required." in params.value

    returns = next(f for f in result.fields if f.name == "Returns")
    assert "**out** (*ndarray or scalar*)" in returns.value


async def test_section_member_summary_is_lead_paragraph_only() -> None:
    soup = BeautifulSoup(RICH_MEMBER_SECTION_HTML, "lxml")
    item = _item("commit-log")
    item.group = "label"
    result = await parse_symbol(soup, item)
    assert result is not None

    assert len(result.members) == 1
    member = result.members[0]
    assert member.signature == "Repository.walk"
    # Only the lead sentence is kept; the prose list is not inlined into the member summary.
    assert member.description == "Start traversing the history from the given commit."
    assert "TOPOLOGICAL" not in member.description
    assert "NONE" not in member.description


def test_format_member_has_no_blockquote_artifacts() -> None:
    from app.cogs.doc.models import Member
    from app.cogs.doc.ui import _format_member

    rendered = _format_member(Member(signature="Walker.reset()", description="Reset the walking machinery."))
    # Regression: the old blockquote rendering emitted stray lone ``>`` lines.
    assert ">" not in rendered
    assert rendered == "**`Walker.reset()`**\nReset the walking machinery."


async def test_unknown_symbol_returns_none() -> None:
    soup = BeautifulSoup(CLASS_HTML, "lxml")
    assert await parse_symbol(soup, _item("discord.DoesNotExist")) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
