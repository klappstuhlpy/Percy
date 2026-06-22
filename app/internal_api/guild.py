"""InternalAPI guild endpoints."""

from __future__ import annotations

import re

from aiohttp import web

from .models import InternalAPIHandlers


class GuildHandlers(InternalAPIHandlers):
    """Guild-related internal API handlers."""

    async def _get_guild_config(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        guild_config = await self.bot.db.get_guild_config(guild_id)

        payload = {
            "id": guild_config.id,
            "name": guild.name,
            "icon_url": guild.icon.url if guild.icon else None,
            "member_count": guild.member_count,
            "flags": {
                "audit_log": guild_config.flags.audit_log,
                "raid": guild_config.flags.raid,
                "alerts": guild_config.flags.alerts,
                "sentinel": guild_config.flags.sentinel,
                "mentions": guild_config.flags.mentions,
            },
            "audit_log_channel": self._resolve_channel(guild, guild_config.audit_log_channel_id),
            "poll_channel": self._resolve_channel(guild, guild_config.poll_channel_id),
            "poll_ping_role": self._resolve_role(guild, guild_config.poll_ping_role_id),
            "poll_reason_channel": self._resolve_channel(guild, guild_config.poll_reason_channel_id),
            "mention_count": guild_config.mention_count,
            "ignored_entities": [self._resolve_entity(guild, eid) for eid in guild_config.safe_automod_entity_ids],
            "mute_role": self._resolve_role(guild, guild_config.mute_role_id),
            "alert_channel": self._resolve_channel(guild, guild_config.alert_channel_id),
            "mod_log_channel": self._resolve_channel(guild, getattr(guild_config, "mod_log_channel_id", None)),
            "message_log_channel": self._resolve_channel(guild, getattr(guild_config, "message_log_channel_id", None)),
            "voice_log_channel": self._resolve_channel(guild, getattr(guild_config, "voice_log_channel_id", None)),
            "audit_log_flags": guild_config.audit_log_flags or {},
            "music_panel_channel": self._resolve_channel(guild, guild_config.music_panel_channel_id),
            "use_music_panel": guild_config.use_music_panel,
            "prefixes": list(guild_config.prefixes),
            "is_new_config": guild_config.flags.value == 0 and guild_config.audit_log_channel_id is None,
        }
        return web.json_response(payload)

    async def _patch_guild_config(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON body")

        if not isinstance(body, dict) or not body:
            raise web.HTTPBadRequest(text="body must be a non-empty object")

        guild_config = await self.bot.db.get_guild_config(guild_id)

        # Build SET clauses from allowed fields.
        allowed_fields = {
            "audit_log_channel_id",
            "poll_channel_id",
            "poll_ping_role_id",
            "poll_reason_channel_id",
            "mention_count",
            "mute_role_id",
            "alert_channel_id",
            "music_panel_channel_id",
            "use_music_panel",
            "mod_log_channel_id",
            "message_log_channel_id",
            "voice_log_channel_id",
        }
        updates: dict[str, object] = {}
        for key, value in body.items():
            if key in allowed_fields:
                updates[key] = value
            elif key == "flags" and isinstance(value, dict):
                # Flags are a bitmask — compute the new value.
                new_flags = guild_config.flags.value
                flag_map = {"audit_log": 1, "raid": 2, "alerts": 4, "sentinel": 8, "mentions": 16}
                for flag_name, bit in flag_map.items():
                    if flag_name in value:
                        if value[flag_name]:
                            new_flags |= bit
                        else:
                            new_flags &= ~bit
                updates["flags"] = new_flags
            elif key == "prefixes" and isinstance(value, list):
                updates["prefixes"] = value

        if not updates:
            raise web.HTTPBadRequest(text="no valid fields to update")

        # Persist via the record helper: it builds the UPDATE and invalidates the
        # get_guild_config cache, keeping the bot's view consistent with the DB.
        await guild_config.update(**updates)

        return web.json_response({"ok": True})

    async def _batch_guild_config(self, request: web.Request) -> web.Response:
        """Apply multiple config mutations in one request.

        Body: {"operations": [{"type": "config", "data": {...}}, {"type": "sentinel", "data": {...}}, ...]}
        Supported types: "config" (same as PATCH /config), "sentinel" (same as PATCH /gatekeeper),
        "sentinel_toggle" (same as POST /gatekeeper/toggle), "audit_log_flags" (same as PATCH /audit-log-flags).
        """
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON body")

        operations = body.get("operations")
        if not isinstance(operations, list) or not operations:
            raise web.HTTPBadRequest(text="operations must be a non-empty array")

        results: list[dict] = []
        for op in operations:
            op_type = op.get("type")
            data = op.get("data", {})

            if op_type == "config":
                guild_config = await self.bot.db.get_guild_config(guild_id)
                allowed_fields = {
                    "audit_log_channel_id", "poll_channel_id", "poll_ping_role_id",
                    "poll_reason_channel_id", "mention_count", "mute_role_id",
                    "alert_channel_id", "music_panel_channel_id", "use_music_panel",
                    "mod_log_channel_id", "message_log_channel_id", "voice_log_channel_id",
                }
                updates: dict[str, object] = {}
                for key, value in data.items():
                    if key in allowed_fields:
                        updates[key] = value
                    elif key == "flags" and isinstance(value, dict):
                        new_flags = guild_config.flags.value
                        flag_map = {"audit_log": 1, "raid": 2, "alerts": 4, "sentinel": 8, "mentions": 16}
                        for flag_name, bit in flag_map.items():
                            if flag_name in value:
                                if value[flag_name]:
                                    new_flags |= bit
                                else:
                                    new_flags &= ~bit
                        updates["flags"] = new_flags
                    elif key == "prefixes" and isinstance(value, list):
                        updates["prefixes"] = value
                if updates:
                    await guild_config.update(**updates)
                results.append({"type": "config", "ok": True})

            elif op_type == "sentinel":
                allowed = {"channel_id", "role_id", "starter_role_id", "bypass_action", "rate"}
                updates = {k: v for k, v in data.items() if k in allowed}
                if updates:
                    await self.bot.db.guilds.upsert_sentinel(guild_id, updates)
                results.append({"type": "sentinel", "ok": True})

            elif op_type == "sentinel_toggle":
                enabled = data.get("enabled")
                sentinel = await self.bot.db.get_guild_sentinel(guild_id)
                if enabled and sentinel and not sentinel.requires_setup and sentinel.started_at is None:
                    await sentinel.enable()
                elif not enabled and sentinel and sentinel.started_at is not None:
                    await sentinel.disable()
                results.append({"type": "sentinel_toggle", "ok": True})

            elif op_type == "audit_log_flags":
                config = await self.bot.db.get_guild_config(guild_id)
                current_flags = config.audit_log_flags or {}
                for key, value in data.items():
                    if key in current_flags:
                        current_flags[key] = bool(value)
                await self.bot.db.moderation.set_audit_log_flags(guild_id, current_flags)
                results.append({"type": "audit_log_flags", "ok": True})

            else:
                results.append({"type": op_type, "ok": False, "error": f"unknown operation type: {op_type}"})

        return web.json_response({"ok": True, "results": results})

    async def _manage_moderation_ignore(self, request: web.Request) -> web.Response:
        """Adds or removes roles/members/channels from the moderation ignore list."""
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON body")

        action = body.get("action")
        entity_id = body.get("entity_id")
        if action not in ("add", "remove"):
            raise web.HTTPBadRequest(text="action must be add or remove")
        if not entity_id:
            raise web.HTTPBadRequest(text="entity_id is required")

        entity_id = int(entity_id)
        if action == "add":
            await self.bot.db.moderation.add_safe_entities(guild_id, [entity_id])
        else:
            await self.bot.db.moderation.remove_safe_entities(guild_id, [entity_id])

        return web.json_response({"ok": True})

    async def _patch_audit_log_flags(self, request: web.Request) -> web.Response:
        """Updates the audit log event flags for a guild."""
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON body")

        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="body must be an object mapping flag names to booleans")

        config = await self.bot.db.get_guild_config(guild_id)
        current_flags = config.audit_log_flags or {}

        for key, value in body.items():
            if key in current_flags:
                current_flags[key] = bool(value)

        await self.bot.db.moderation.set_audit_log_flags(guild_id, current_flags)
        return web.json_response({"ok": True, "flags": current_flags})

    async def _get_guild_roles(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        roles = [
            {
                "id": str(role.id),
                "name": role.name,
                "color": role.color.value,
                "position": role.position,
                "permissions": role.permissions.value,
                "mentionable": role.mentionable,
                "managed": role.managed,
                "hoist": role.hoist,
                "icon_url": role.icon.url if role.icon else None,
            }
            for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
        ]
        return web.json_response(roles)

    async def _get_guild_channels(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        channels = [
            {
                "id": str(ch.id),
                "name": ch.name,
                "type": str(ch.type),
                "position": ch.position,
                "category_id": str(ch.category_id) if ch.category_id else None,
            }
            for ch in sorted(guild.channels, key=lambda c: (c.position, c.name))
        ]
        return web.json_response(channels)

    async def _get_sentinel(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        sentinel = await self.bot.db.get_guild_sentinel(guild_id)

        if sentinel is None:
            return web.json_response(None)

        payload = {
            "channel": self._resolve_channel(guild, sentinel.channel_id),
            "role": self._resolve_role(guild, sentinel.role_id),
            "message": sentinel.message_id,
            "starter_role": self._resolve_role(guild, sentinel.starter_role_id),
            "bypass_action": sentinel.bypass_action,
            "rate": sentinel.rate
            if isinstance(sentinel.rate, str)
            else (f"{sentinel.rate[0]}/{sentinel.rate[1]}" if sentinel.rate else None),
            "started_at": sentinel.started_at.isoformat() if sentinel.started_at else None,
            "member_count": len(sentinel.members),
            "needs_setup": sentinel.requires_setup,
        }
        return web.json_response(payload)

    async def _patch_sentinel(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON body")

        if not isinstance(body, dict) or not body:
            raise web.HTTPBadRequest(text="body must be a non-empty object")

        allowed = {"channel_id", "role_id", "starter_role_id", "bypass_action", "rate"}
        updates: dict[str, object] = {}
        for key, value in body.items():
            if key not in allowed:
                continue
            if key == "bypass_action" and value not in ("ban", "kick"):
                raise web.HTTPBadRequest(text="bypass_action must be ban or kick")
            if key == "rate" and not re.match(r"^\d+\/\d+$", value):
                raise web.HTTPBadRequest(text="rate must be in the format X/Y")
            updates[key] = value

        if not updates:
            raise web.HTTPBadRequest(text="no valid fields to update")

        # Repository upsert ensures the row exists, updates it, and invalidates the cache.
        await self.bot.db.guilds.upsert_sentinel(guild_id, updates)

        return web.json_response({"ok": True})

    async def _send_sentinel_message(self, request: web.Request) -> web.Response:
        """Send a sentinel verification embed to a channel and store the message_id."""
        import discord

        from app.cogs.moderation.sentinel import (
            SENTINEL_DEFAULT_MESSAGE_BODY,
            SENTINEL_DEFAULT_MESSAGE_TITLE,
            SentinelVerifyView,
        )

        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON body")

        channel_id = body.get("channel_id")
        title = body.get("title", SENTINEL_DEFAULT_MESSAGE_TITLE)
        content = body.get("content", SENTINEL_DEFAULT_MESSAGE_BODY)

        if not channel_id:
            raise web.HTTPBadRequest(text="channel_id is required")

        channel = guild.get_channel(int(channel_id))
        if channel is None:
            raise web.HTTPBadRequest(text="channel not found")

        if not isinstance(channel, discord.TextChannel):
            raise web.HTTPBadRequest(text="channel must be a text channel")

        config = await self.bot.db.get_guild_config(guild_id)
        sentinel = await self.bot.db.get_guild_sentinel(guild_id)

        view = SentinelVerifyView(config, sentinel, title=title, body=content)
        try:
            message = await channel.send(view=view)
        except discord.HTTPException as e:
            raise web.HTTPServiceUnavailable(text=f"failed to send message: {e}")

        await self.bot.db.guilds.upsert_sentinel(guild_id, {"message_id": message.id, "channel_id": int(channel_id)})

        return web.json_response({"ok": True, "message_id": message.id})

    async def _toggle_sentinel(self, request: web.Request) -> web.Response:
        """Enable or disable the sentinel."""
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="guild not found")

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON body")

        enabled = body.get("enabled")
        if enabled is None:
            raise web.HTTPBadRequest(text="enabled field is required")

        sentinel = await self.bot.db.get_guild_sentinel(guild_id)

        if enabled:
            if sentinel is None:
                raise web.HTTPBadRequest(text="sentinel has not been configured")
            if sentinel.requires_setup:
                raise web.HTTPBadRequest(text="sentinel requires setup (channel, role, and message must be set)")
            if sentinel.started_at is not None:
                return web.json_response({"ok": True, "status": "already_enabled"})
            await sentinel.enable()
        else:
            if sentinel is None:
                return web.json_response({"ok": True, "status": "not_configured"})
            if sentinel.started_at is None:
                return web.json_response({"ok": True, "status": "already_disabled"})
            await sentinel.disable()

        # ``enable``/``disable`` mutate the cached sentinel in place; firing the
        # invalidation signal here would cancel the role loop that ``disable`` needs to
        # drain its pending-removal queue, so we intentionally don't re-invalidate.
        return web.json_response({"ok": True, "status": "enabled" if enabled else "disabled"})

    async def _get_user_guilds(self, request: web.Request) -> web.Response:
        """Return every guild the user shares with Percy.

        Each entry carries a ``manageable`` flag (admin or Manage Server). The
        dashboard splits these into managed servers and read-only public
        overviews; servers the user can manage but Percy is *not* in come from
        the dashboard's stored Discord OAuth guild list, not this endpoint.
        """
        discord_id = int(request.match_info["discord_id"])

        guilds = []
        for guild in self.bot.guilds:
            member = guild.get_member(discord_id)
            if member is None:
                continue
            perms = member.guild_permissions
            guilds.append(
                {
                    "id": str(guild.id),
                    "name": guild.name,
                    "icon_url": guild.icon.url if guild.icon else None,
                    "member_count": guild.member_count,
                    "owner": guild.owner_id == discord_id,
                    "manageable": perms.administrator or perms.manage_guild,
                }
            )

        return web.json_response(guilds)

    async def _get_user_avatar(self, request: web.Request) -> web.Response:
        """Resolve a user's *current* avatar URL straight from Discord.

        The dashboard calls this live instead of persisting the avatar hash, so a
        profile-picture change is reflected immediately (no stale stored avatars).
        """
        import discord

        discord_id = int(request.match_info["discord_id"])
        user = self.bot.get_user(discord_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(discord_id)
            except discord.HTTPException:
                raise web.HTTPNotFound(text="user not found")

        return web.json_response(
            {
                "avatar_url": user.display_avatar.url,
                "username": user.name,
            }
        )
