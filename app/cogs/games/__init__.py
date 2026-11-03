from app.core import Bot


async def setup(bot: Bot) -> None:
    # Imported lazily so that importing this package (e.g. an engine or ui submodule) does not load the cog.
    from app.cogs.games.cog import Games

    await bot.add_cog(Games(bot))
