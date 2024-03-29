from typing import TYPE_CHECKING

from cogs.doc._cache import DocCache

if TYPE_CHECKING:
    from bot import Percy
else:
    Percy = type('Percy', (), {})

MAX_SIGNATURE_AMOUNT = 3
PRIORITY_PACKAGES = (
    'python',
)

doc_cache = DocCache('doc')


async def setup(bot: Percy) -> None:
    from ._cog import Documentation
    await bot.add_cog(Documentation(bot))
