from app.core import Bot


async def setup(bot: Bot) -> None:
    # Imported lazily so that importing this package (e.g. via the pure
    # ``app.games`` engine, which depends on ``_classes``) does not pull in the
    # cog and create an import cycle through ``_poker``.
    from app.cogs.games.cog import Games

    await bot.add_cog(Games(bot))
