"""
MIT License

Copyright (c) 2018 Python Discord

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
"""

from app.cogs.doc._cache import DocCache

MAX_SIGNATURE_AMOUNT = 3
PRIORITY_PACKAGES = (
    'python',
)

doc_cache = DocCache()


async def setup(bot) -> None:
    from ._cog import Documentation
    await bot.add_cog(Documentation(bot))
