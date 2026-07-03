from app.core import Bot


async def setup(bot: Bot) -> None:
    # Imported lazily so importing the package doesn't pull in the cog (and
    # discord.ui views) for callers that only need the module namespace.
    from app.cogs.economy.cog import Economy

    await bot.add_cog(Economy(bot))
