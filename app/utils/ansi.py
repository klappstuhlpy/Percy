from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, NamedTuple, TypeAlias, Generator

from discord import User

from app.utils.formats import sentinel

# Code obtained form: https://github.com/jay3332/Lambda-v3/blob/main/app/util/ansi.py

if TYPE_CHECKING:
    from app.core import Context

    AnsiIdentifierKwargs: TypeAlias = 'AnsiColor | AnsiBackgroundColor | bool'

INHERIT = sentinel('INHERIT', repr='INHERIT')


class AnsiColor(IntEnum):
    """An enumeration of ANSI colors."""
    default = 39
    gray = 30
    red = 31
    green = 32
    yellow = 33
    blue = 34
    magenta = 35
    cyan = 36
    white = 37


class AnsiStyle(IntEnum):
    """An enumeration of ANSI styles."""
    default = 0
    bold = 1
    underline = 4


class AnsiBackgroundColor(IntEnum):
    """An enumeration of ANSI background colors."""
    default = 49
    black = 40
    red = 41
    gray = 42
    light_gray = 43
    lighter_gray = 44
    blurple = 45
    lightest_gray = 46
    white = 47


class AnsiChunkSpecs:
    """A collection of ANSI chunk formatting."""

    if TYPE_CHECKING:
        color: AnsiColor
        background_color: AnsiBackgroundColor
        bold: bool
        underline: bool

    def __init__(
            self,
            *,
            color: AnsiColor,
            background_color: AnsiBackgroundColor,
            bold: bool,
            underline: bool,
    ) -> None:
        self.color = color
        self.background_color = background_color
        self.bold = bold
        self.underline = underline


class AnsiChunk(NamedTuple):
    """A chunk of text with ANSI formatting."""

    text: str
    color: AnsiColor = INHERIT
    background_color: AnsiBackgroundColor = INHERIT
    bold: bool = INHERIT
    underline: bool = INHERIT

    def to_text(self, previous_specs: AnsiChunkSpecs, /) -> Generator[str, AnsiChunkSpecs, None]:
        """Returns a version of the text with ANSI formatting.

        This method is used to format the text inline with other text.

        Parameters
        ----------
        previous_specs: :class:`AnsiChunkSpecs`
            The previous formatting.

        Returns
        -------
        :class:`Generator`
            A generator of the formatted text.
        """
        specs = []

        if self.color is not INHERIT and self.color != previous_specs.color:
            previous_specs.color = self.color
            specs.append(self.color)

        if self.background_color is not INHERIT and self.background_color != previous_specs.background_color:
            previous_specs.background_color = self.background_color
            specs.append(self.background_color)

        reset = False

        if self.bold is not INHERIT and self.bold != previous_specs.bold:
            previous_specs.bold = self.bold
            if previous_specs.bold:
                specs.append(AnsiStyle.bold)
            else:
                reset = True

        if self.underline is not INHERIT and self.underline is not previous_specs.underline:
            previous_specs.underline = self.underline
            if self.underline:
                specs.append(AnsiStyle.underline)
            else:
                reset = True

        if reset:
            # this is to reset the previous bold/underline state
            # to avoid double bold/underline
            for entity in (
                    previous_specs.color,
                    previous_specs.background_color,
                    AnsiStyle.bold if previous_specs.bold else None,
                    AnsiStyle.underline if previous_specs.underline else None,
            ):
                if entity is not None and entity not in specs:
                    specs.append(entity)

            specs = [entity for entity in specs if entity.name != 'default']
            specs.insert(0, AnsiStyle.default)

        if specs:
            specs = ';'.join(str(spec.value) for spec in specs)
            yield f'\x1b[{specs}m'

        yield self.text
        yield previous_specs

    def with_text(self, text: str, /) -> AnsiChunk:
        """Returns a new chunk with the given text."""
        return AnsiChunk(
            text=text,
            color=self.color,
            background_color=self.background_color,
            bold=self.bold,
            underline=self.underline,
        )

    def to_dict(self) -> dict[str, AnsiIdentifierKwargs]:
        """Returns a dictionary of the chunk's formatting."""
        result = {}
        if self.color is not INHERIT:
            result['color'] = self.color
        if self.background_color is not INHERIT:
            result['background_color'] = self.background_color
        if self.bold is not INHERIT:
            result['bold'] = self.bold
        if self.underline is not INHERIT:
            result['underline'] = self.underline

        return result


class AnsiStringBuilder:
    """A dynamic builder for ANSI strings."""

    __slots__ = (
        '_chunks',
        '_prefix',
        '_fallback_prefix',
        '_suffix',
        '_default_color',
        '_default_background_color',
        '_default_bold',
        '_default_underline',
    )

    if TYPE_CHECKING:
        _chunks: list[AnsiChunk]
        _prefix: str
        _fallback_prefix: str
        _suffix: str
        _default_color: AnsiColor | None
        _default_background_color: AnsiBackgroundColor | None
        _default_bold: bool
        _default_underline: bool

    def __init__(self) -> None:
        # those should not be modified from outer scope
        self._chunks = []
        self._prefix = self._fallback_prefix = self._suffix = ''
        self._default_color = self._default_background_color = None
        self._default_bold = self._default_underline = False

    @property
    def previous(self) -> AnsiChunk:
        """:class:`AnsiChunk`: Returns the previous chunk."""
        return self._chunks[-1] if self._chunks else AnsiChunk('')

    @property
    def previous_color(self) -> AnsiColor:
        """:class:`AnsiColor`: Returns the previous color."""
        for chunk in reversed(self._chunks):
            if chunk.color is not INHERIT:
                return chunk.color
        return AnsiColor.default

    @property
    def previous_background_color(self) -> AnsiBackgroundColor:
        """:class:`AnsiBackgroundColor`: Returns the previous background color."""
        for chunk in reversed(self._chunks):
            if chunk.background_color is not INHERIT:
                return chunk.background_color
        return AnsiBackgroundColor.default

    @property
    def previous_bold(self) -> bool:
        """:class:`bool`: Returns the previous bold state."""
        for chunk in reversed(self._chunks):
            if chunk.bold is not INHERIT:
                return chunk.bold
        return self._default_bold

    @property
    def previous_underline(self) -> bool:
        """:class:`bool`: Returns the previous underline state."""
        for chunk in reversed(self._chunks):
            if chunk.underline is not INHERIT:
                return chunk.underline
        return self._default_underline

    @property
    def base_length(self) -> int:
        """:class:`int`: The content length of this string."""
        return sum(len(chunk.text) for chunk in self._chunks)

    @property
    def raw_length(self) -> int:
        """:class:`int`: The length of the raw, unformatted content of this string."""
        return self.base_length + len(self._fallback_prefix) + len(self._suffix)

    @property
    def raw(self) -> str:
        """:class:`str`: The raw, unformatted content of this string."""
        return self._fallback_prefix + ''.join(chunk.text for chunk in self._chunks) + self._suffix

    def append(
            self,
            text: str,
            /,
            *,
            inherit: bool = False,
            color: AnsiColor = None,
            background_color: AnsiBackgroundColor = None,
            bold: bool | None = None,
            underline: bool | None = None,
    ) -> AnsiStringBuilder:
        """Append a chunk of text to the builder.

        Creates a new chunk with the given formatting and appends it to the builder.
        This chunk can inherit the previous formatting if desired.

        Parameters
        ----------
        text: :class:`str`
            The text to append.
            This is a special positional-only argument.
        inherit: :class:`bool`
            Whether to inherit the previous formatting.
        color: :class:`AnsiColor`
            The color to use.
        bold: :class:`bool`
            Whether to use bold formatting.
        underline: :class:`bool`
            Whether to use underline formatting.
        background_color: :class:`AnsiBackgroundColor`
            The background color to use.

        Returns
        -------
        :class:`AnsiStringBuilder`
            The builder.
        """
        if color is None:
            if self._default_color is not None:
                color = self._default_color
            else:
                color = self.previous_color if inherit else AnsiColor.default

        if bold is None:
            bold = self._default_bold if self._default_bold is not None else self.previous_bold if inherit else False

        if underline is None:
            if self._default_underline is not None:
                underline = self._default_underline
            else:
                underline = self.previous_underline if inherit else False

        if background_color is None:
            if self._default_background_color is not None:
                background_color = self._default_background_color
            else:
                background_color = self.previous_background_color if inherit else AnsiBackgroundColor.default

        self._chunks.append(
            AnsiChunk(
                text,
                color=color,
                background_color=background_color,
                bold=bold,
                underline=underline
            )
        )
        return self

    def extend(self, other: AnsiStringBuilder, /) -> AnsiStringBuilder:
        """Extend the builder with another builder.

        Parameters
        ----------
        other: :class:`AnsiStringBuilder`
            The builder to extend with.
        """
        if not isinstance(other, AnsiStringBuilder):
            raise TypeError(f'Expected AnsiStringBuilder, got {other.__class__.__name__}')

        self._chunks.extend(other._chunks)
        return self

    def strip(self) -> AnsiStringBuilder:
        """Strips spaces and newlines from the beginning and end of the string."""
        if not self._chunks:
            return self

        elif len(self._chunks) == 1:
            chunk = self._chunks[0]
            self._chunks[0] = chunk.with_text(chunk.text.strip())
            return self

        first, *_, last = self._chunks
        self._chunks[0] = first.with_text(first.text.lstrip())
        self._chunks[-1] = last.with_text(last.text.rstrip())

        return self

    def newline(self, count: int = 1, /) -> AnsiStringBuilder:
        """Appends a newline to the string.

        Parameters
        ----------
        count: :class:`int`
            The number of newlines to append.
        """
        return self.append('\n' * count)

    def bold(self, text: str | None = None, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text in bold.

        Parameters
        ----------
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_bold = True
        if text:
            self.append(text, bold=True, **kwargs)

        return self

    def no_bold(self, text: str | None = None, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text without bold.

        Parameters
        ----------
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_bold = False
        if text:
            self.append(text, bold=False, **kwargs)

        return self

    def underline(self, text: str | None = None, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text in underline.

        Parameters
        ----------
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_underline = True
        if text:
            self.append(text, underline=True, **kwargs)

        return self

    def no_underline(self, text: str | None = None, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text without underline.

        Parameters
        ----------
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_underline = False
        if text:
            self.append(text, underline=False, **kwargs)

        return self

    def color(self, color: AnsiColor, /, text: str | None = None, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text in the given color.

        Parameters
        ----------
        color: :class:`AnsiColor`
            The color to use.
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_color = color
        if text:
            self.append(text, color=color, **kwargs)

        return self

    def no_color(self, text: str | None = None, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text without color.

        Parameters
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_color = None
        if text:
            self.append(text, color=None, **kwargs)

        return self

    def background_color(
            self,
            background_color: AnsiBackgroundColor,
            /,
            text: str | None = None,
            **kwargs: AnsiIdentifierKwargs,
    ) -> AnsiStringBuilder:
        """Appends text in the given background color.

        Parameters
        ----------
        background_color: :class:`AnsiBackgroundColor`
            The background color to use.
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_background_color = background_color
        if text:
            self.append(text, background_color=background_color, **kwargs)

        return self

    def no_background_color(self, text: str | None = None, /, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends text without a background color.

        Parameters
        ----------
        text: :class:`str`
            The text to append.
        **kwargs: :class:`AnsiIdentifierKwargs`
            The formatting to apply.
        """
        self._default_background_color = None
        if text:
            self.append(text, background_color=None, **kwargs)

        return self

    def ensure_codeblock(self, *, fallback: str = '') -> AnsiStringBuilder:
        """Ensures that the string is wrapped in a codeblock.

        Parameters
        ----------
        fallback: :class:`str`
            The fallback language to use.
        """
        raw = self.raw
        if raw.startswith('```') and raw.endswith('```'):
            return self

        self._prefix = '```ansi\n'
        self._fallback_prefix = f'```{fallback}\n'
        self._suffix = '```'

        return self

    def merge_chunks(self) -> AnsiStringBuilder:
        """Merges compatible chunks into one.

        Chunks are compatible if any of the following conditions are met:
        - the second chunk has everything set to INHERIT
        - the two do not have conflicting parts, e.g., one has a color, and the second one only has background_color

        If the first one is blank, overwrite second with first.
        """
        chunks: list[AnsiChunk | None] = self._chunks

        for i, chunk in enumerate(chunks, -1):
            previous = chunks[i] if i >= 0 else None

            if not previous:
                continue

            if all(entity is INHERIT for entity in (chunk.color, chunk.background_color, chunk.bold, chunk.underline)):
                chunks[i] = previous.with_text(previous.text + chunk.text)
                chunks[i + 1] = None
                continue

            chunk_dict = chunk.to_dict()
            previous_dict = previous.to_dict()

            # Equal keys cancel out
            for key in chunk_dict.copy():
                if key in previous_dict and chunk_dict[key] == previous_dict[key]:
                    del chunk_dict[key]
                    del previous_dict[key]

            # If the keys don't conflict, they are compatible
            if set(chunk_dict).isdisjoint(previous_dict):
                chunks[i] = previous.with_text(previous.text + chunk.text)
                chunks[i + 1] = None

            # Merge the two if the first one is blank
            if not previous.text:
                previous_dict.update(chunk_dict)
                chunks[i] = AnsiChunk(chunk.text, **previous_dict)
                chunks[i + 1] = None

        self._chunks = [chunk for chunk in chunks if chunk is not None]
        return self

    def build(self) -> str:
        """Builds the string.

        This method will merge the chunks and build the string with the ANSI formatting.
        """
        previous_specs = AnsiChunkSpecs(
            color=AnsiColor.default,
            background_color=AnsiBackgroundColor.default,
            bold=False,
            underline=False,
        )
        result = []

        self.merge_chunks()

        for chunk in self._chunks:
            for part in chunk.to_text(previous_specs):
                if isinstance(part, AnsiChunkSpecs):
                    previous_specs = part
                    continue
                result.append(part)

        return self._prefix + ''.join(result) + self._suffix

    def dynamic(self, ctx: Context) -> str:
        """Returns the built string only if the user of the given context is not on mobile."""
        if isinstance(ctx.author, User):
            if ctx.bot.user_on_mobile(ctx.author):
                return self.raw
        elif ctx.author.is_on_mobile():
            return self.raw
        return self.build()

    @classmethod
    def from_string(cls, string: str, /, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Creates an :class:`AnsiStringBuilder` from a string."""
        builder = cls()
        builder.append(string, **kwargs)
        return builder

    def __str__(self) -> str:
        return self.build()

    def __repr__(self) -> str:
        return f'<AnsiStringBuilder len={len(self)}>'

    def __len__(self) -> int:
        return len(self.build())

    def __iadd__(self, other: AnsiStringBuilder | str) -> AnsiStringBuilder:
        if isinstance(other, AnsiStringBuilder):
            return self.extend(other)

        return self.append(other)
