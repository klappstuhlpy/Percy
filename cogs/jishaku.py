import importlib
import sys
import typing
from importlib.metadata import distribution, packages_distributions
from types import ModuleType

import discord
import jishaku
import psutil
from discord.ext import commands
from jishaku.cog import OPTIONAL_FEATURES, STANDARD_FEATURES
from jishaku.features.baseclass import Feature
from jishaku.math import natural_size
from jishaku.modules import package_version

from cogs.utils import error_handling
from cogs.utils.converters import ModuleConverter
from cogs.utils.formats import plural

jishaku.Flags.NO_DM_TRACEBACK = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.HIDE = True


class Jishaku(*OPTIONAL_FEATURES, *STANDARD_FEATURES):

    # noinspection PyProtectedMember
    @Feature.Command(parent="jsk", name="sync")
    async def jsk_sync(self, ctx: commands.Context, *targets: str):
        """
        Sync global or guild application commands to Discord.
        """

        if not self.bot.application_id:
            await ctx.send("Cannot sync when application info not fetched")
            return

        before_commands = set(self.bot.tree._get_all_commands())

        guilds_set: set[typing.Optional[int]] = {None}
        for target in targets:
            if target == '$':  # Sync commands to global
                guilds_set.add(None)
            elif target == '*':  # Sync commands to all guilds
                guilds_set |= set(self.bot.tree._guild_commands.keys())
            elif target == '.':  # Sync commands to the current guild
                if ctx.guild:
                    guilds_set.add(ctx.guild.id)
                else:
                    await ctx.send("Can't sync guild commands without guild information")
                    return
            else:  # Sync commands to a specific guild
                try:
                    guilds_set.add(int(target))
                except ValueError as error:
                    raise commands.BadArgument(f"{target} is not a valid guild ID") from error

        translator = getattr(self.bot.tree, 'translator', None)
        if translator:
            payload = [await command.get_translated_payload(translator) for command in before_commands]
        else:
            payload = [command.to_dict() for command in before_commands]

        for guild in guilds_set:
            try:
                if guild is None:
                    data = await self.bot.http.bulk_upsert_global_commands(self.bot.application_id, payload=payload)
                else:
                    data = await self.bot.http.bulk_upsert_guild_commands(self.bot.application_id, guild,
                                                                          payload=payload)

                synced = [
                    discord.app_commands.AppCommand(data=d, state=ctx._state)
                    for d in data
                ]
            except discord.HTTPException as error:
                error_lines = [
                    line
                    for line in str(error).split("\n")
                ]
                for line in error_lines:
                    match = self.SLASH_COMMAND_ERROR.match(line)
                    if match:
                        parts = match.group(1).split('.')
                        assert len(parts) % 2 == 0

                        name = parts[-1]
                        error_lines.append(f"\N{MAGNET} This is likely caused by: `{name}`")

                error_text = '\n'.join(error_lines)

                embed = discord.Embed(
                    title="Slash command sync failed",
                    description=error_text
                )
                await ctx.send(embed=embed)
            else:
                after_commands = set(self.bot.tree._get_all_commands())

                if added := ", ".join(cmd.qualified_name for cmd in (after_commands - before_commands)):
                    added = "+ " + added

                if removed := ", ".join(cmd.qualified_name for cmd in (before_commands - after_commands)):
                    removed = "- " + removed

                embed = discord.Embed(
                    title=f"\N{SATELLITE ANTENNA} Command Tree {'Global' if guild else 'Guild'}Sync",
                    description=f"```diff\n{added}\n{removed}```" if added or removed else ""
                )
                embed.set_footer(text=f"Synced total {plural(len(synced)):command}")
                await ctx.send(embed=embed)

    @Feature.Command(
        parent="jsk",
        name="mrl",
        invoke_without_commad=True,
        ignore_extra=False
    )
    async def reload_module(self, ctx: commands.Context, *, module: ModuleConverter):
        """Reloads a module."""

        assert isinstance(module, ModuleType)
        icon = "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}"

        try:
            importlib.reload(module)
        except Exception as exc:
            await ctx.send(f"{icon}\N{WARNING SIGN} ``{module.__name__}`` was not reloaded.\n")
            return await error_handling.send_traceback(ctx.channel, 8, type(exc), exc, exc.__traceback__)
        await ctx.send(f"{icon} ``{module.__name__}`` was reloaded successfully.")

    @Feature.Command(
        parent="jsk",
        name="ml",
        invoke_without_commad=True,
        ignore_extra=False
    )
    async def load_module(self, ctx: commands.Context, *, module: str):
        """Reloads a module."""

        icon = "\N{INBOX TRAY}"
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            return await ctx.send(f"{icon}\N{WARNING SIGN} `{module!r}` is not a valid module.")
        except Exception as exc:
            await ctx.send(f"{icon}\N{WARNING SIGN} `{module}` was not loaded.")
            return await error_handling.send_traceback(ctx.channel, 8, type(exc), exc, exc.__traceback__)
        await ctx.send(f"{icon} ``{module}`` was loaded successfully.")

    @Feature.Command(
        parent="jsk",
        name="mul",
        invoke_without_commad=True,
        ignore_extra=False
    )
    async def unload_module(self, ctx: commands.Context, *, module: ModuleConverter):
        """Reloads a module."""

        assert isinstance(module, ModuleType)
        icon = "\N{OUTBOX TRAY}"

        try:
            del sys.modules[module.__name__]
        except KeyError:
            return await ctx.send(f"{icon}\N{WARNING SIGN} ``{module.__name__}`` was not found.")
        except Exception as exc:
            return await error_handling.send_traceback(ctx.channel, 8, type(exc), exc, exc.__traceback__)

        await ctx.send(f"{icon} ``{module.__name__}`` was unloaded successfully.")

    @Feature.Command(
        name="jishaku",
        aliases=["jsk"],
        invoke_without_command=True,
        ignore_extra=False
    )
    async def jsk(self, ctx: commands.Context):
        """
        The Jishaku debug and diagnostic commands.
        This command on its own gives a status brief.
        All other functionality is within its subcommands.
        """

        distributions: typing.List[str] = [
            dist
            for dist in packages_distributions()["discord"]
            if any(
                file.parts == ("discord", "__init__.py")
                for file in distribution(dist).files
            )
        ]

        if distributions:
            dist_version = f"{distributions[0]} `{package_version(distributions[0])}`"
        else:
            dist_version = f"unknown `{discord.__version__}`"

        summary = [
            f"Jishaku `v{package_version('jishaku')}`, {dist_version}, "
            f"Python `{sys.version}` on `{sys.platform}`".replace("\n", ""),
            f"Module was loaded <t:{self.load_time.timestamp():.0f}:R>, "
            f"cog was loaded <t:{self.start_time.timestamp():.0f}:R>.",
            "",
        ]

        if psutil:
            try:
                proc = psutil.Process()

                with proc.oneshot():
                    try:
                        mem = proc.memory_full_info()
                        summary.append(
                            f"Using `{natural_size(mem.rss)}` physical memory and "
                            f"`{natural_size(mem.vms)}` virtual memory, "
                            f"`{natural_size(mem.uss)}` of which unique to this process."
                        )
                    except psutil.AccessDenied:
                        pass

                    try:
                        name = proc.name()
                        pid = proc.pid
                        thread_count = proc.num_threads()

                        summary.append(
                            f"Running on PID `{pid}` (`{name}`) with `{thread_count}` thread(s)."
                        )
                    except psutil.AccessDenied:
                        pass

                    summary.append("")
            except psutil.AccessDenied:
                summary.append(
                    "psutil is installed, but this process does not have high enough access rights "
                    "to query process information."
                )
                summary.append("")  # blank line
        s_for_guilds = "" if len(self.bot.guilds) == 1 else "s"
        s_for_users = "" if len(self.bot.users) == 1 else "s"
        cache_summary = f"`{len(self.bot.guilds)}` guild{s_for_guilds} and `{len(self.bot.users)}` user{s_for_users}"

        if isinstance(self.bot, discord.AutoShardedClient):
            if len(self.bot.shards) > 20:
                summary.append(
                    f"This bot is automatically sharded (`{len(self.bot.shards)}` shards of `{self.bot.shard_count}`)"
                    f" and can see {cache_summary}."
                )
            else:
                shard_ids = ", ".join(str(i) for i in self.bot.shards.keys())
                summary.append(
                    f"This bot is automatically sharded (Shards `{shard_ids}` of `{self.bot.shard_count}`)"
                    f" and can see {cache_summary}."
                )
        elif self.bot.shard_count:
            summary.append(
                f"This bot is manually sharded (Shard `{self.bot.shard_id}` of `{self.bot.shard_count}`)"
                f" and can see {cache_summary}."
            )
        else:
            summary.append(f"This bot is not sharded and can see {cache_summary}.")

        if self.bot._connection.max_messages:  # type: ignore
            message_cache = f"Message cache capped at `{self.bot._connection.max_messages}`"
        else:
            message_cache = "Message cache is disabled"

        remarks = {True: "enabled", False: "disabled", None: "unknown"}

        *group, last = (
            f"{intent.replace('_', ' ')} intent is {remarks.get(getattr(self.bot.intents, intent, None))}"
            for intent in ("presences", "members", "message_content")
        )

        summary.append(f"{message_cache}, {', '.join(group)}, and {last}.")

        summary.append(
            f"Average websocket latency: `{round(self.bot.latency * 1000, 2)} ms`"
        )

        embed = discord.Embed(description="\n".join(summary), color=0x2b2d31)
        embed.set_author(name=ctx.bot.user.name, icon_url=ctx.bot.user.avatar.url)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Jishaku(bot=bot))
