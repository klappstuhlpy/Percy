import datetime
import re
from typing import ClassVar
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup, NavigableString, PageElement, Tag

from app.cogs.comic._client import Comic as MarvelComic
from app.cogs.comic._client import Marvel
from app.cogs.comic._data import Brand, GenericComic
from app.utils import executor, utcparse


def extract_authors(text: str) -> dict[str, list[str]]:
    author_pattern = re.compile(r'(?P<position>[^,]+) by (?P<name>[^,]+)')
    authors = author_pattern.findall(text)

    author_dict = {}
    for position, name in authors:
        author_dict[position] = [name.strip()]

    return author_dict


def from_destination(c: str, details: dict[str, list]) -> str:
    return str(details[c][0]) if c in details else None


def remove_html_tags(content: str) -> str:
    """Removes HTML tags from a string."""
    clean_text = re.sub('<.*?>', '', content)  # Remove HTML tags
    clean_text = re.sub(r'\s+', ' ', clean_text)  # Remove extra whitespace
    return clean_text


class Parser:
    """Parser object for comic fetching."""

    DC_ENDPOINT: ClassVar[str] = 'https://www.dc.com'
    VIZ_ENDPOINT: ClassVar[str] = 'https://www.viz.com/calendar/{year}/{month}'
    RAW_VIZ_ENDPOINT: ClassVar[str] = 'https://www.viz.com'

    @classmethod
    @executor
    def _bs4_parse_dc(cls, text: str, *, index: int, element: str) -> GenericComic | None:
        soup = BeautifulSoup(text, 'html.parser')
        base_prd = soup.find('h2', class_='type-lg type-xl--md line-solid weight-bold mar-b-md mar-b-lg--md')
        if base_prd is None:
            return None

        title = remove_html_tags(base_prd.text)
        image_url = soup.find('div', class_='product-image mar-x-auto mar-b-lg pad-x-md').find('img').get('src')

        store_table = soup.find('table', class_='purchase-table')
        try:
            price = store_table.find('span').text[1:]
        except (KeyError, AttributeError):
            price = 'N/A'
        if price.endswith('*'):
            price = price[:-1]

        if isinstance(price, str):
            price = 0.0

        base_obj = soup.find('div', class_='o_geo-block')
        if base_obj:
            price_note: str | None = base_obj.find(
                'p', class_='mar-t-rg').text if base_obj.find('p', class_='mar-t-rg') else None
        else:
            price_note = 'N/A'

        info_table = soup.find('div', class_='row pad-b-xl')
        desc = remove_html_tags(info_table.find('p').text.strip())
        authors = extract_authors(info_table.find('div', class_='mar-b-md').text)
        release_date = info_table.find('div', class_='o_release-date mar-b-md').text.replace('Release', '')
        isbn = info_table.find('div', class_='o_isbn13 mar-b-md').text if info_table.find(
            'div', class_='o_isbn13 mar-b-md') else None
        trim_size = info_table.find('div', class_='o_trimsize mar-b-md').text if info_table.find(
            'div', class_='o_trimsize mar-b-md') else None

        spec_table = soup.find('div', class_='g-6--md g-omega--md')
        result = {}
        for i in spec_table.find_all('div', class_='mar-b-md'):
            clean_text = remove_html_tags(i.text.strip())
            spec_map = ['Length', 'Series', 'Category', 'Age Rating']
            for spec in spec_map:
                if clean_text.startswith(spec):
                    value = clean_text.replace(spec, "").strip()
                    result[spec] = value

        if result.get('Category') in ['TV Series', 'Movie']:
            return None

        page_info = result.get('Length', 'N/A Pages')[0]
        page_count = int(page_info) if not isinstance(page_info, str) else None

        return GenericComic(
            brand=Brand.MANGA,
            _id=index,
            title=title,
            description=desc,
            creators=authors,
            image_url=image_url,
            url=element,
            page_count=page_count,
            price=float(price),
            _copyright=f'© {datetime.datetime.now().year} VIZ Media, LLC. All rights reserved.',
            date=utcparse(release_date),
            # kwargs
            isbn=isbn,
            trim_size=trim_size,
            price_note=price_note,
            category=result.get('Category'),
            age_rating=result.get('Age Rating')
        )

    @classmethod
    async def bs4_viz(cls) -> list[GenericComic]:
        ref = cls.VIZ_ENDPOINT.format(year=datetime.datetime.now().year, month=datetime.datetime.now().month)

        elements = []
        async with aiohttp.ClientSession() as session, session.get(ref) as resp:
            soup = BeautifulSoup(await resp.text(), 'html.parser')
            for i in soup.find_all('a', class_='product-thumb ar-inner type-center'):
                href = i.get('href')
                elements.append(urljoin(cls.RAW_VIZ_ENDPOINT, href))

        mangas = []
        async with aiohttp.ClientSession() as session:
            for index, element in enumerate(elements):
                async with session.get(element) as resp:
                    _cs_manga = await cls._bs4_parse_dc(await resp.text(), index=index, element=element)
                    if _cs_manga is not None:
                        mangas.append(_cs_manga)

        return mangas

    @classmethod
    async def bs4_dc(cls) -> list[GenericComic]:
        async with aiohttp.ClientSession() as session:
            async with session.get(cls.DC_ENDPOINT + '/comics') as resp:
                if resp.status != 200:
                    resp.raise_for_status()

                page = await resp.text()

            soup = BeautifulSoup(page, 'html.parser')
            comics = []
            links: Tag = soup.find('ul', class_='react-multi-carousel-track content-tray-slider')

            for item in links.contents:
                branch = item.findNext(class_='card-button usePointer').get('href')
                link = cls.DC_ENDPOINT + branch

                async with session.get(link) as resp:
                    if resp.status != 200:
                        resp.raise_for_status()

                    page = await resp.text()

                if page is None:
                    continue

                soup = BeautifulSoup(page, 'html.parser')
                txt = soup.find_all(class_='sc-g8nqnn-0')
                if not txt:
                    continue

                c_type = ''.join(txt[0].find('p', class_='text-left').contents).strip()
                if c_type != 'COMIC BOOK':
                    continue

                title = ''.join(txt[0].find('h1', class_='text-left').contents).strip()

                desc = None
                if len(txt) > 1 and txt[1].find('p'):
                    desc_list = cls._get_desc(txt[1])
                    desc = '\n'.join(i.strip() for i in ''.join(desc_list).split('\n') if i.strip())

                details_list = [i.contents for i in soup.find_all('div', class_='sc-b3fnpg-3')]
                details = {}
                for d in details_list:
                    for dd in d:
                        d_id = dd['id'][len('page151-band11690-Subitem2847'):]
                        x = None
                        if '-' in d_id:
                            d_id, x = d_id.split('-')
                            x = None if d_id not in ['24', '12'] else x
                        if d_id not in details:
                            details[d_id] = []
                        details[d_id] += [
                            i.contents[0].contents[0] if x else i.contents[0] for i in dd.contents if type(i) is Tag]

                creators = {}
                if '24' in details:
                    creators['Writer'] = [str(i) for i in details['24']]
                if '12' in details:
                    creators['Artist'] = [str(i) for i in details['12']]

                price = from_destination('33', details)
                price = 0.0 if price == 'FREE' else float(price) if price else None

                date = from_destination('36', details)
                date = utcparse(date) if date else None

                page_count = from_destination('48', details)

                img = soup.find('img', id='page151-band11672-Card11673-img')
                image = img['src'].split('?')[0]
                image = image if image else None

                _copyright = str(
                    soup.find('div', class_='small legal d-inline-block').contents[0].contents[0]  # type: ignore
                )

                _cs_comic = GenericComic(
                    brand=Brand.DC,
                    _id=''.join(i for i in title if i.isalnum()),
                    title=title,
                    description=desc,
                    creators=creators,
                    image_url=image,
                    url=link,
                    page_count=int(page_count),
                    price=price,
                    _copyright=_copyright,
                    date=date
                )
                comics.append(_cs_comic)

        return comics

    @classmethod
    async def bs4_marvel(cls) -> dict[int, str]:
        async with aiohttp.ClientSession() as session, session.get('https://marvel.com/comics/calendar/') as resp:
            if resp.status != 200:
                resp.raise_for_status()

            page = await resp.text()

        soup = BeautifulSoup(page, 'html.parser')
        descs = {}

        for link in soup.find_all('a', class_='meta-title'):
            plink = 'https:' + link.get('href').strip()
            _id = int(plink.strip('https://www.marvel.com/comics/issue/').split('/')[0])

            page = None
            for i in range(10):
                try:
                    async with aiohttp.ClientSession() as session, session.get(plink) as resp:
                        if resp.status != 200:
                            resp.raise_for_status()

                        page = await resp.text()
                    break
                except aiohttp.ClientPayloadError:
                    pass
            if page is None:
                continue

            soup = BeautifulSoup(page, 'html.parser')
            try:
                desc = next(i for i in soup.find_all('p') if 'data-blurb' in i.attrs).get_text().strip()
            except StopIteration:
                continue

            descs[_id] = desc

        return descs

    @classmethod
    async def marvel_from_api(cls, client: Marvel) -> list[GenericComic]:
        raw = await client.get_comics(format='comic', noVariants='true', dateDescriptor='thisWeek', limit=100)
        m_copyright = raw.data['attributionText']

        comics = [cls._to_comic(c) for c in raw.ex_data.results]
        for c in comics:
            c.brand = Brand.MARVEL
            c.copyright = m_copyright

        return comics

    @classmethod
    def _to_comic(cls, data: MarvelComic) -> GenericComic:
        from ._cog import GenericComic

        _cs_comic = GenericComic(
            _id=data.id,
            title=data.title,
            page_count=data.pageCount,
            description=data.description,
        )

        _cs_comic.creators = {}
        _cs_comic.image_url = (
            data.images[0].path + '/clean.jpg'
            if data.images else 'https://klappstuhl.me/gallery/hrFFAQFMlL.jpeg')

        _cs_comic.url = next((i['url'] for i in data.urls if i['type'] == 'detail'), None)
        _cs_comic.price = next((i.price for i in data.prices if i.type == 'printPrice'), None)
        _cs_comic.date = next((i.date for i in data.dates if i.type == 'onsaleDate'), None)

        for cr in data.creators.items:
            role = cr.role.title()
            if role not in _cs_comic.creators:
                _cs_comic.creators[role] = [cr.name]
            else:
                _cs_comic.creators[role].append(cr.name)
        return _cs_comic

    @classmethod
    def _get_desc(cls, tag: Tag | PageElement) -> list[str]:
        strings = []
        for i in tag.contents:
            if isinstance(i, Tag):
                if i.name in ['p', 'em']:
                    strings += cls._get_desc(i)
            elif isinstance(i, NavigableString):
                s = str(i)
                if tag.name == 'em':
                    s = (' ' if s.startswith(' ') else "") + \
                        f'*{s.strip()}*' + \
                        (' ' if s.endswith(' ') else "")
                strings.append(s)
        return strings

    @classmethod
    async def fetch_marvel_lookup_table(cls, client: Marvel) -> list[GenericComic]:
        from ._cog import GenericComic

        comics: list[GenericComic] = await cls.marvel_from_api(client)
        descs: dict[int, str] = await cls.bs4_marvel()

        for c in comics:
            if description := descs.get(c.id, None):
                c.description = description

        return comics
