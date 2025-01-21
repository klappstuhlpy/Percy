from app.cogs.anilist._cog import AniList


async def setup(bot) -> None:
    await bot.add_cog(AniList(bot))
