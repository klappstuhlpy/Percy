import asyncio
import aiohttp


async def get():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://github.com/python-discord/bot/blob/main/bot/exts/info/code_snippets.py') as response:
            print(await response.text())


if __name__ == '__main__':
    asyncio.run(get())
