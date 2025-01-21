from app.cogs.comic._cog import Comics


async def setup(bot) -> None:
    await bot.add_cog(Comics(bot))
