"""Tests for the pure formatting helpers in :mod:`app.utils.formats`."""

import pytest

from app.utils import formats


class TestPluralize:
    def test_singular(self) -> None:
        assert f'{formats.pluralize(1):member}' == '1 member'

    def test_plural_default_suffix(self) -> None:
        assert f'{formats.pluralize(3):member}' == '3 members'

    def test_zero_is_plural(self) -> None:
        assert f'{formats.pluralize(0):member}' == '0 members'

    def test_custom_plural_form(self) -> None:
        assert f'{formats.pluralize(2):entry|entries}' == '2 entries'
        assert f'{formats.pluralize(1):entry|entries}' == '1 entry'

    def test_negative_one_treated_as_singular(self) -> None:
        assert f'{formats.pluralize(-1):life}' == '-1 life'

    def test_pass_content_returns_word_only(self) -> None:
        assert f'{formats.pluralize(1, pass_content=True):member}' == 'member'
        assert f'{formats.pluralize(5, pass_content=True):member}' == 'members'


class TestHumanJoin:
    def test_empty(self) -> None:
        assert formats.human_join([]) == ''

    def test_single(self) -> None:
        assert formats.human_join(['a']) == 'a'

    def test_two(self) -> None:
        assert formats.human_join(['a', 'b']) == 'a or b'

    def test_three_default(self) -> None:
        assert formats.human_join(['a', 'b', 'c']) == 'a, b or c'

    def test_custom_final_and_delim(self) -> None:
        assert formats.human_join(['a', 'b', 'c'], delim='; ', final='and') == 'a; b and c'


class TestHumanizeList:
    def test_one(self) -> None:
        assert formats.humanize_list(['a']) == 'a'

    def test_two(self) -> None:
        assert formats.humanize_list(['a', 'b']) == 'a and b'

    def test_three(self) -> None:
        assert formats.humanize_list(['a', 'b', 'c']) == 'a, b, and c'


class TestTruncate:
    def test_no_truncation_when_short(self) -> None:
        assert formats.truncate('hello', 10) == 'hello'

    def test_truncates_with_ellipsis(self) -> None:
        result = formats.truncate('hello world', 5)
        assert result == 'hell…'
        assert len(result) == 5

    def test_exact_length_not_truncated(self) -> None:
        assert formats.truncate('hello', 5) == 'hello'


class TestToBool:
    @pytest.mark.parametrize('value', ['true', 'yes', 'on', '1', 'TRUE', 'Yes'])
    def test_truthy(self, value: str) -> None:
        assert formats.to_bool(value) is True

    @pytest.mark.parametrize('value', ['false', 'no', 'off', '0', 'FALSE'])
    def test_falsy(self, value: str) -> None:
        assert formats.to_bool(value) is False

    def test_integer_input(self) -> None:
        assert formats.to_bool(1) is True
        assert formats.to_bool(0) is False

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            formats.to_bool('maybe')


class TestNumberSuffix:
    @pytest.mark.parametrize(('n', 'expected'), [
        (1, '1st'), (2, '2nd'), (3, '3rd'), (4, '4th'),
        (11, '11th'), (12, '12th'), (13, '13th'),
        (21, '21st'), (22, '22nd'), (23, '23rd'),
        (100, '100th'), (101, '101st'), (111, '111th'),
    ])
    def test_suffix(self, n: int, expected: str) -> None:
        assert formats.number_suffix(n) == expected


class TestShortenNumber:
    @pytest.mark.parametrize(('value', 'expected'), [
        (500, '500'),
        (1000, '1K'),
        (1500, '1.5K'),
        (1_000_000, '1M'),
        (2_500_000, '2.5M'),
        (1_000_000_000, '1B'),
        (1_000_000_000_000, '1T'),
    ])
    def test_shorten(self, value: int, expected: str) -> None:
        assert formats.shorten_number(value) == expected


class TestHumanizeBool:
    def test_true(self) -> None:
        assert formats.humanize_bool(True) == 'Yes'

    def test_false(self) -> None:
        assert formats.humanize_bool(False) == 'No'


class TestFindNthOccurrence:
    def test_first(self) -> None:
        assert formats.find_nth_occurrence('a.b.c.d', '.', 1) == 1

    def test_third(self) -> None:
        assert formats.find_nth_occurrence('a.b.c.d', '.', 3) == 5

    def test_not_found(self) -> None:
        assert formats.find_nth_occurrence('abc', '.', 1) is None

    def test_beyond_count(self) -> None:
        assert formats.find_nth_occurrence('a.b', '.', 5) is None


class TestFindWord:
    def test_found_on_first_line(self) -> None:
        assert formats.find_word('hello world', 'world') == (1, 7, 11)

    def test_found_on_later_line(self) -> None:
        assert formats.find_word('foo\nbar baz', 'baz') == (2, 5, 7)

    def test_not_found(self) -> None:
        assert formats.find_word('foo bar', 'qux') is None


class TestCensorInvite:
    @pytest.mark.parametrize('link', [
        'discord.gg/abc123',
        'discord.com/invite/abc123',
        'discordapp.com/invite/abc123',
        'https://discord.gg/abc123',
        'https://discord.com/invite/abc123',
        'https://discordapp.com/invite/abc123',
    ])
    def test_real_links_censored(self, link: str) -> None:
        assert formats.censor_invite(link) == '[censored-invite]'

    def test_link_within_text_censored(self) -> None:
        assert formats.censor_invite('join discord.gg/abc123 now') == 'join [censored-invite] now'

    @pytest.mark.parametrize('word', ['discordant', 'discord', 'discordABC', 'discord.com'])
    def test_plain_words_left_alone(self, word: str) -> None:
        assert formats.censor_invite(word) == word

    def test_plain_text_untouched(self) -> None:
        assert formats.censor_invite('just text here') == 'just text here'


class TestMedalEmoji:
    def test_top_three(self) -> None:
        assert formats.medal_emoji(1) == '\N{FIRST PLACE MEDAL}'
        assert formats.medal_emoji(2) == '\N{SECOND PLACE MEDAL}'
        assert formats.medal_emoji(3) == '\N{THIRD PLACE MEDAL}'

    def test_other_default(self) -> None:
        assert formats.medal_emoji(4) == '\N{SPORTS MEDAL}'

    def test_other_numerate(self) -> None:
        assert formats.medal_emoji(4, numerate=True) == '4.'


class TestWrapHelpers:
    def test_wrap_list(self) -> None:
        assert formats.WrapList([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]

    def test_wrap_list_empty(self) -> None:
        assert formats.WrapList([], 2) == []

    def test_wrap_dict(self) -> None:
        result = formats.WrapDict({'a': 1, 'b': 2, 'c': 3}, 2)
        assert result == [{'a': 1, 'b': 2}, {'c': 3}]

    def test_rev_dict(self) -> None:
        assert formats.RevDict({'a': 1, 'b': 2}) == {1: 'a', 2: 'b'}

    def test_sort_dict(self) -> None:
        assert formats.SortDict({'b': 2, 'a': 1}) == {'a': 1, 'b': 2}

    def test_sort_dict_reverse(self) -> None:
        assert list(formats.SortDict({'a': 1, 'b': 2}, reverse=True)) == ['b', 'a']


class TestMerge:
    def test_merge_iterables(self) -> None:
        assert list(formats.merge([1, 2], [3], [4, 5])) == [1, 2, 3, 4, 5]

    def test_merge_empty(self) -> None:
        assert list(formats.merge()) == []


class TestTabularData:
    def test_render_simple_table(self) -> None:
        table = formats.TabularData()
        table.set_columns(['Name', 'Age'])
        table.add_rows([['Alice', '24'], ['Bob', '19']])
        rendered = table.render()
        lines = rendered.split('\n')
        # Border + header + border + two rows + border
        assert len(lines) == 6
        assert lines[0] == lines[2] == lines[-1]
        assert 'Name' in lines[1] and 'Age' in lines[1]
        assert 'Alice' in rendered and 'Bob' in rendered

    def test_column_widens_to_fit_row(self) -> None:
        table = formats.TabularData()
        table.set_columns(['X'])
        table.add_row(['a-very-long-value'])
        rendered = table.render()
        assert 'a-very-long-value' in rendered


class TestPagify:
    def test_short_text_single_page(self) -> None:
        pages = list(formats.pagify('short text', escape_mass_mentions=False))
        assert pages == ['short text']

    def test_long_text_splits(self) -> None:
        text = 'x' * 5000
        pages = list(formats.pagify(text, escape_mass_mentions=False))
        assert len(pages) > 1
        assert ''.join(pages) == text

    def test_respects_page_length(self) -> None:
        text = '\n'.join('line' for _ in range(1000))
        pages = list(formats.pagify(text, page_length=500, escape_mass_mentions=False))
        assert all(len(p) <= 500 for p in pages)


class TestPlayerStamp:
    def test_basic_stamp(self) -> None:
        # 30s position into a 60s track (values in milliseconds).
        result = formats.PlayerStamp(60_000, 30_000)
        assert result.startswith('00:30')
        assert result.endswith('01:00')
        assert '🔘' in result
