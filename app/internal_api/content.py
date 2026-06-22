"""InternalAPI content endpoints."""
from __future__ import annotations

import datetime
from contextlib import suppress

import discord
from aiohttp import web

from .models import InternalAPIHandlers


class ContentHandlers(InternalAPIHandlers):
    """Content-related internal API handlers."""

    async def _get_polls(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        limit = min(int(request.query.get('limit', '50')), 100)
        offset = int(request.query.get('offset', '0'))

        records = await self.bot.db.polls.get_for_guild(guild_id)

        polls = []
        for record in records:
            metadata = record.get('metadata') or {}
            _kwargs = metadata.get('kwargs', {})
            polls.append({
                'id': record['id'],
                'channel_id': str(record['channel_id']),
                'message_id': str(record['message_id']),
                'question': _kwargs.get('content', _kwargs.get('question', 'Untitled Poll')),
                'description': _kwargs.get('description') or '',
                'options': [opt['content'] for opt in _kwargs.get('options', [])],
                'image_url': _kwargs.get('image_url') or '',
                'color': _kwargs.get('color') or '',
                'published': record['published'].isoformat() if record.get('published') else None,
                'expires': record['expires'].isoformat() if record.get('expires') else None,
                'ended': _kwargs.get('running', False) is False,
                'total_votes': _kwargs.get('votes', 0),
            })

        total = len(polls)
        polls = polls[offset:offset + limit]
        return web.json_response({'polls': polls, 'total': total})

    async def _create_poll(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        cog = self.bot.get_cog('Polls')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='polls cog not loaded')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        question = (body.get('question') or '').strip()
        if not question:
            raise web.HTTPBadRequest(text='question is required')

        raw_options = body.get('options') or []
        options = [str(opt).strip() for opt in raw_options if str(opt).strip()]
        if len(options) < 2:
            raise web.HTTPBadRequest(text='at least 2 options are required')
        if len(options) > 8:
            raise web.HTTPBadRequest(text='a poll can have at most 8 options')

        try:
            duration = int(body.get('duration_seconds') or 0)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text='invalid duration')
        if duration <= 0:
            raise web.HTTPBadRequest(text='duration must be a positive number of seconds')

        config = await self.bot.db.get_guild_config(guild_id=guild_id)

        channel_id = body.get('channel_id')
        if channel_id:
            try:
                channel = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text='invalid channel')
        else:
            channel = config.poll_channel if config else None

        if not isinstance(channel, discord.TextChannel):
            raise web.HTTPBadRequest(text='a valid text channel is required (set a poll channel or pass channel_id)')

        if self.bot.timers is None:
            raise web.HTTPServiceUnavailable(text='the timers system is not available')

        expires = discord.utils.utcnow() + datetime.timedelta(seconds=duration)

        poll = await cog.create_poll_from_dashboard(
            guild,
            channel,
            author_id=self.bot.user.id,
            question=question,
            options=options,
            expires=expires,
            description=(body.get('description') or '').strip() or None,
            color=(body.get('color') or '').strip() or None,
            image_url=(body.get('image_url') or '').strip() or None,
            thread_question=(body.get('thread_question') or '').strip() or None,
        )

        return web.json_response({'ok': True, 'id': poll.id})

    async def _patch_poll(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        poll_id = int(request.match_info['poll_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        record = await self.bot.db.polls.get(poll_id, guild_id)
        if record is None:
            raise web.HTTPNotFound(text='poll not found')

        metadata = record.get('metadata') or {}
        kwargs = metadata.get('kwargs', {})

        if not kwargs.get('running', False):
            raise web.HTTPBadRequest(text='cannot edit a poll that has ended')

        # Apply edits to kwargs
        if 'question' in body:
            val = body['question']
            if val:
                kwargs['content'] = val

        if 'description' in body:
            val = body['description']
            kwargs['description'] = val if val else None

        if 'image_url' in body:
            val = body['image_url']
            kwargs['image_url'] = val if val else None

        if 'color' in body:
            val = body['color']
            kwargs['color'] = val if val else None

        if 'options' in body:
            new_options = body['options']
            if isinstance(new_options, list) and len(new_options) >= 2:
                existing = kwargs.get('options', [])
                updated = []
                for i, opt_text in enumerate(new_options):
                    if not opt_text:
                        continue
                    if i < len(existing):
                        existing[i]['content'] = opt_text
                        updated.append(existing[i])
                    else:
                        updated.append({'content': opt_text, 'index': i, 'votes': 0})
                if len(updated) >= 2:
                    for idx, opt in enumerate(updated):
                        opt['index'] = idx
                    kwargs['options'] = updated

        metadata['kwargs'] = kwargs
        await self.bot.db.polls.update(poll_id, {'metadata': metadata})

        return web.json_response({'ok': True})

    async def _end_poll(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        poll_id = int(request.match_info['poll_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        cog = self.bot.get_cog('Polls')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='polls cog not loaded')

        polls = await cog.get_guild_polls(guild_id)
        poll = next((p for p in polls if p.id == poll_id), None)
        if poll is None:
            raise web.HTTPNotFound(text='poll not found')

        result = await cog.end_poll(poll)
        if result is None:
            raise web.HTTPBadRequest(text='poll is already ended')

        return web.json_response({'ok': True})

    async def _get_giveaways(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        limit = min(int(request.query.get('limit', '50')), 100)
        offset = int(request.query.get('offset', '0'))

        records = await self.bot.db.giveaways.get_guild_giveaways(guild_id)

        giveaways = []
        for record in records:
            metadata = record.get('metadata') or {}
            _kwargs = metadata.get('kwargs', {})
            giveaways.append({
                'id': record['id'],
                'channel_id': str(record['channel_id']),
                'message_id': str(record['message_id']),
                'author_id': str(record['author_id']),
                'title': _kwargs.get('prize', 'Giveaway'),
                'description': _kwargs.get('description', 'N/A'),
                'winners_count': _kwargs.get('winner_count', 1),
                'entries': len(record.get('entries', [])),
                'ended': datetime.datetime.fromisoformat(_kwargs.get('expires')).astimezone(datetime.UTC) < datetime.datetime.now(datetime.UTC),
                'ends_at': _kwargs.get('expires'),
            })

        total = len(giveaways)
        giveaways = giveaways[offset:offset + limit]
        return web.json_response({'giveaways': giveaways, 'total': total})

    async def _create_giveaway(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        cog = self.bot.get_cog('Giveaways')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='giveaways cog not loaded')
        if self.bot.timers is None:
            raise web.HTTPServiceUnavailable(text='the timers system is not available')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        prize = (body.get('prize') or '').strip()
        if not prize:
            raise web.HTTPBadRequest(text='prize is required')
        if len(prize) > 256:
            raise web.HTTPBadRequest(text='prize must be 256 characters or less')

        try:
            duration = int(body.get('duration_seconds') or 0)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text='invalid duration')
        if duration <= 0:
            raise web.HTTPBadRequest(text='duration must be a positive number of seconds')

        try:
            winners = int(body.get('winners') or 1)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text='invalid winner count')
        if winners < 1 or winners > 100:
            raise web.HTTPBadRequest(text='winner count must be between 1 and 100')

        description = (body.get('description') or '').strip() or None

        channel_id = body.get('channel_id')
        try:
            channel = guild.get_channel(int(channel_id)) if channel_id else None
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text='invalid channel')
        if not isinstance(channel, discord.TextChannel):
            raise web.HTTPBadRequest(text='a valid text channel is required')

        from app.cogs.giveaway import GiveawayEnterButton

        expires = discord.utils.utcnow() + datetime.timedelta(seconds=duration)
        message = await channel.send(embed=discord.Embed(description='*Preparing Giveaway...*'))
        giveaway = await cog.create_giveaway(
            message.channel.id,
            message.id,
            guild_id,
            self.bot.user.id,
            description=description,
            prize=prize,
            winner_count=winners,
            created=discord.utils.utcnow().isoformat(),
            expires=expires.isoformat(),
        )

        await self.bot.timers.create(
            expires,
            'giveaway',
            giveaway_id=giveaway.id,
            created=discord.utils.utcnow(),
            timezone='UTC',
        )

        view = discord.ui.View(timeout=None)
        view.add_item(GiveawayEnterButton(giveaway))
        await message.edit(embed=giveaway.to_embed(), view=view)

        return web.json_response({'ok': True, 'id': giveaway.id})

    async def _end_giveaway(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        giveaway_id = int(request.match_info['giveaway_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        cog = self.bot.get_cog('Giveaways')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='giveaways cog not loaded')

        giveaway = await cog.get_guild_giveaway(guild_id, giveaway_id)
        if giveaway is None:
            raise web.HTTPNotFound(text='giveaway not found')

        # Draws winners now and tidies the message (same path as the timer firing).
        await cog.end_giveaway(giveaway.id)
        return web.json_response({'ok': True})

    async def _delete_giveaway(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        giveaway_id = int(request.match_info['giveaway_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        cog = self.bot.get_cog('Giveaways')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='giveaways cog not loaded')

        giveaway = await cog.get_guild_giveaway(guild_id, giveaway_id)
        if giveaway is None:
            raise web.HTTPNotFound(text='giveaway not found')

        # Cancel without drawing: drop the pending timer, delete the record, and
        # strike through the announcement message so nobody keeps entering.
        if self.bot.timers is not None:
            with suppress(Exception):
                await self.bot.timers.delete('giveaway', giveaway_id=str(giveaway_id))
        await self.bot.db.giveaways.delete_giveaway(giveaway_id)

        with suppress(Exception):
            if giveaway.message is discord.utils.MISSING:
                await giveaway.fetch_message()
            if giveaway.message:
                await giveaway.message.edit(
                    content=f'This giveaway for *{giveaway.prize}* was cancelled.', embed=None, view=None)

        return web.json_response({'ok': True})

    async def _get_tags(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        total = await self.bot.db.tags.count_tags(guild_id)
        # Full directory (every parent tag), used by the dashboard's searchable
        # table, export selection and per-tag preview. Owners are resolved from
        # the member cache only (no per-tag fetch_user — the list can be large).
        all_tags = await self.bot.db.tags.get_guild_tags(guild_id)
        top_creators = await self.bot.db.tags.get_top_tag_creators(guild_id, limit=10)
        total_uses = await self.bot.db.tags.count_tag_command_uses(guild_id)

        tags = []
        for record in sorted(all_tags, key=lambda r: r.get('uses', 0), reverse=True):
            owner_id = record.get('owner_id')
            member = guild.get_member(owner_id) if owner_id else None

            tags.append({
                'id': record['id'],
                'name': record['name'],
                'owner_id': str(owner_id) if owner_id else None,
                'owner_name': member.display_name if member else None,
                'uses': record.get('uses', 0),
                'created_at': record['created_at'].isoformat() if record.get('created_at') else None,
            })

        creators = []
        for record in top_creators:
            user_id = record.get('owner_id')
            member = guild.get_member(user_id) if user_id else None

            creators.append({
                'user_id': str(user_id) if user_id else None,
                'username': member.display_name if member else f'Unknown ({user_id})',
                'count': record.get('count', 0),
            })

        return web.json_response({
            'total': total,
            'total_uses': total_uses,
            'tags': tags,
            'top_creators': creators,
        })

    async def _get_tag_detail(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            tag_id = int(request.match_info['tag_id'])
        except (TypeError, ValueError):
            raise web.HTTPNotFound(text='tag not found')

        record = await self.bot.db.tags.get_tag_record(tag_id, location_id=guild_id)
        if record is None:
            raise web.HTTPNotFound(text='tag not found')

        owner_id = record.get('owner_id')
        member = guild.get_member(owner_id) if owner_id else None
        return web.json_response({
            'id': record['id'],
            'name': record['name'],
            'content': record['content'],
            'owner_id': str(owner_id) if owner_id else None,
            'owner_name': member.display_name if member else None,
            'uses': record.get('uses', 0),
            'created_at': record['created_at'].isoformat() if record.get('created_at') else None,
        })

    async def _delete_tag(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            tag_id = int(request.match_info['tag_id'])
        except (TypeError, ValueError):
            raise web.HTTPNotFound(text='tag not found')

        record = await self.bot.db.tags.get_tag_record(tag_id, location_id=guild_id)
        if record is None:
            raise web.HTTPNotFound(text='tag not found')

        # delete_tag also removes every alias that points to it.
        await self.bot.db.tags.delete_tag(record['id'])
        return web.json_response({'ok': True})

    async def _export_tags(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        records = await self.bot.db.tags.export_tags(guild_id)
        tags = [{'name': r['name'], 'content': r['content']} for r in records]
        return web.json_response({'tags': tags})

    async def _import_tags(self, request: web.Request) -> web.Response:
        """Bulk-create tags from a parsed (name, content) list.

        Access is gated server-side (admin/Manage-Server) by the dashboard, which
        also malware-scans and sanitises the uploaded file. Tag creation goes
        through the parameterised repo layer (no string-built SQL), so the import
        cannot inject SQL. Duplicates and reserved names are skipped, not failed.
        """
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        raw = body.get('tags')
        if not isinstance(raw, list):
            raise web.HTTPBadRequest(text='tags must be a list')

        try:
            owner_id = int(body.get('owner_id')) if body.get('owner_id') else self.bot.user.id
        except (TypeError, ValueError):
            owner_id = self.bot.user.id

        root = self.bot.get_command('tag')
        reserved = set(root.all_commands) if root else set()

        created = 0
        skipped = 0
        failed: list[dict] = []
        seen: set[str] = set()

        for item in raw[:1000]:
            if not isinstance(item, dict):
                continue
            name = (item.get('name') or '').strip()
            content = (item.get('content') or '').strip()
            if not name or not content:
                failed.append({'name': name or '(empty)', 'error': 'name and content are required'})
                continue
            if len(name) > 100:
                failed.append({'name': name[:60], 'error': 'name exceeds 100 characters'})
                continue
            if len(content) > 2000:
                failed.append({'name': name, 'error': 'content exceeds 2000 characters'})
                continue

            lname = name.lower()
            if lname.partition(' ')[0] in reserved:
                failed.append({'name': name, 'error': 'reserved tag name'})
                continue
            if lname in seen:
                skipped += 1
                continue
            seen.add(lname)

            existing = await self.bot.db.tags.get_tag_record(name, location_id=guild_id)
            if existing is not None:
                skipped += 1
                continue

            try:
                await self.bot.db.tags.create_tag(name, content, owner_id, guild_id)
                created += 1
            except Exception:
                failed.append({'name': name, 'error': 'could not be created'})

        return web.json_response({'ok': True, 'created': created, 'skipped': skipped, 'failed': failed})

    async def _get_commands(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        command_config = await self.bot.db.guilds.get_command_config(guild_id)
        plonks = await self.bot.db.guilds.get_plonks(guild_id)

        # A NULL channel_id deny row disables the command for the whole guild;
        # rows with a channel_id only disable it in that specific channel.
        disabled_commands: dict[str, list[str]] = {}
        globally_disabled: set[str] = set()
        for record in command_config:
            name = record['name']
            channel_id = record.get('channel_id')
            whitelist = record.get('whitelist', False)
            if whitelist:
                continue
            if channel_id is None:
                globally_disabled.add(name)
            else:
                disabled_commands.setdefault(name, []).append(str(channel_id))

        all_commands = []
        for cmd in self.bot.walk_commands():
            qualified = cmd.qualified_name
            cog_name = cmd.cog.qualified_name if cmd.cog else 'Uncategorized'
            all_commands.append({
                'name': qualified,
                'category': cog_name,
                'description': cmd.short_doc or '',
                'disabled_in': disabled_commands.get(qualified, []),
                'globally_disabled': qualified in globally_disabled,
            })

        plonk_list = []
        for record in plonks:
            entity_id = record['entity_id']
            member = guild.get_member(entity_id)
            channel = guild.get_channel(entity_id)
            plonk_list.append({
                'entity_id': str(entity_id),
                'type': 'member' if member else ('channel' if channel else 'unknown'),
                'name': member.display_name if member else (channel.name if channel else str(entity_id)),
            })

        return web.json_response({
            'commands': sorted(all_commands, key=lambda c: (c['category'], c['name'])),
            'plonks': plonk_list,
        })

    async def _toggle_command(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        name = body.get('name')
        enabled = body.get('enabled')
        channel_id = body.get('channel_id')

        if name is None or enabled is None:
            raise web.HTTPBadRequest(text='must specify name and enabled')

        if enabled:
            # Re-enable: remove the relevant disable entries for this command.
            if channel_id:
                await self.bot.db.guilds.clear_command_config_channel(guild_id, name, int(channel_id))
            else:
                # Clears both the guild-wide row and every per-channel row.
                await self.bot.db.guilds.clear_command_config(guild_id, name)
        else:
            # Disable: a channel_id targets a single channel; a NULL channel_id
            # disables the command server-wide via one row (not one row per channel).
            await self.bot.db.guilds.set_command_config(
                guild_id, int(channel_id) if channel_id else None, name, whitelist=False)

        # Keep the Config cog's resolved-permissions cache in sync with the write.
        config_cog = self.bot.get_cog('Config')
        if config_cog is not None:
            config_cog.get_commands_configuration.invalidate(guild_id)

        return web.json_response({'ok': True})

    async def _manage_plonk(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        action = body.get('action')
        entity_id = body.get('entity_id')

        if action not in ('add', 'remove'):
            raise web.HTTPBadRequest(text='action must be add or remove')

        if not entity_id:
            raise web.HTTPBadRequest(text='must specify entity_id')

        entity_id = int(entity_id)

        if action == 'add':
            await self.bot.db.guilds.add_plonk(guild_id, entity_id)
        else:
            await self.bot.db.guilds.remove_plonks(guild_id, [entity_id])

        # Drop the Config cog's plonk-status cache for this guild.
        config_cog = self.bot.get_cog('Config')
        if config_cog is not None:
            config_cog.is_plonked.invalidate_containing(f"{guild_id!r}:")

        return web.json_response({'ok': True})

    async def _get_autoresponders(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        limit = min(int(request.query.get('limit', '50')), 100)
        offset = int(request.query.get('offset', '0'))
        search = (request.query.get('search') or '').lower()

        records = await self.bot.db.autoresponders.get_all(guild_id)
        entries = [
            {
                'id': r['id'],
                'trigger': r['trigger'],
                'response': r['response'],
                'match_type': r['match_type'],
                'ignore_case': r.get('ignore_case', True),
                'enabled': r['enabled'],
                'uses': r.get('uses', 0),
                'created_by': str(r['created_by']) if r.get('created_by') else None,
            }
            for r in records
        ]

        if search:
            entries = [e for e in entries if search in e['trigger'].lower() or search in e['response'].lower()]

        total = len(entries)
        entries = entries[offset:offset + limit]
        return web.json_response({'entries': entries, 'total': total})

    async def _create_autoresponder(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        trigger = body.get('trigger', '').strip()
        response = body.get('response', '').strip()
        match_type = body.get('match_type', 'contains')
        ignore_case = body.get('ignore_case', True)
        created_by = body.get('created_by')

        if not trigger or not response:
            raise web.HTTPBadRequest(text='trigger and response are required')

        result = await self.bot.db.autoresponders.create(
            guild_id, trigger, response,
            match_type=match_type, ignore_case=ignore_case, created_by=created_by or 0,
        )
        if result is None:
            raise web.HTTPConflict(text='an autoresponder with that trigger already exists')
        return web.json_response({'ok': True})

    async def _delete_autoresponder(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        trigger = request.match_info['trigger']
        result = await self.bot.db.autoresponders.delete(guild_id, trigger)
        if result is None:
            raise web.HTTPNotFound(text='autoresponder not found')
        return web.json_response({'ok': True})

    async def _patch_autoresponder(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        trigger = request.match_info['trigger']
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        enabled = body.get('enabled')
        if enabled is None:
            raise web.HTTPBadRequest(text='enabled field is required')

        result = await self.bot.db.autoresponders.set_enabled(guild_id, trigger, bool(enabled))
        if result is None:
            raise web.HTTPNotFound(text='autoresponder not found')
        return web.json_response({'ok': True})

    async def _get_comics(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        feeds = []
        for brand_name in ('MARVEL', 'DC', 'MANGA'):
            record = await self.bot.db.comics.get_config(guild_id, brand_name)
            if record:
                feeds.append({
                    'id': record['id'],
                    'brand': record['brand'],
                    'channel_id': str(record['channel_id']),
                    'format': record.get('format', 'Summary'),
                    'day': record.get('day', 1),
                    'ping': str(record['ping']) if record.get('ping') else None,
                    'pin': record.get('pin', False),
                    'next_pull': record['next_pull'].isoformat() if record.get('next_pull') else None,
                })
        return web.json_response({'feeds': feeds})

    async def _create_comic(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        brand = body.get('brand')
        channel_id = body.get('channel_id')
        fmt = body.get('format', 'Summary')
        day = body.get('day', 1)
        ping = body.get('ping')
        pin = body.get('pin', False)

        if not brand or not channel_id:
            raise web.HTTPBadRequest(text='brand and channel_id are required')

        existing = await self.bot.db.comics.get_config(guild_id, brand)
        if existing:
            raise web.HTTPConflict(text=f'already subscribed to {brand}')

        config_dict = {
            'guild_id': guild_id,
            'channel_id': int(channel_id),
            'brand': brand,
            'format': fmt,
            'day': int(day),
            'ping': int(ping) if ping else None,
            'pin': bool(pin),
            'next_pull': None,
        }
        await self.bot.db.comics.create_config(config_dict)
        return web.json_response({'ok': True})

    async def _patch_comic(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        brand = request.match_info['brand']
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        config = await self.bot.get_cog('Comics').get_comic_config(guild_id, brand)  # type: ignore
        if config is None:
            raise web.HTTPNotFound(text='comic feed not found')

        updates: dict[str, object] = {}
        if 'channel_id' in body:
            updates['channel_id'] = int(body['channel_id'])
        if 'format' in body:
            updates['format'] = body['format']
        if 'day' in body:
            updates['day'] = int(body['day'])
            updates['next_pull'] = config.next_scheduled(int(body['day']))
        if 'ping' in body:
            updates['ping'] = int(body['ping']) if body['ping'] else None
        if 'pin' in body:
            updates['pin'] = bool(body['pin'])

        if updates:
            await self.bot.db.comics.update_config(config.id, updates)
        return web.json_response({'ok': True})

    async def _delete_comic(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        brand = request.match_info['brand']
        await self.bot.db.comics.delete_config(guild_id, brand)
        return web.json_response({'ok': True})

    async def _push_comic(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        brand = request.match_info['brand']
        cog = self.bot.get_cog('Comics')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='Comics cog not loaded')
        record = await self.bot.db.comics.get_config(guild_id, brand)
        if record is None:
            raise web.HTTPNotFound(text='comic feed not found')
        self.bot.dispatch('comic_push', guild_id, brand)
        return web.json_response({'ok': True})

    async def _get_temp_channels(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        # Live spawned channels are tracked globally in ``bot.temp_channels``
        # ({channel_id: True}); there is no stored hub->spawned link, so we group
        # the currently-active ones by their category and attach them to the hub
        # that lives in the same category (the spawner always inherits the hub's
        # category). Multiple hubs sharing a category will list the same actives.
        actives_by_category: dict[int | None, list[dict]] = {}
        for raw_id in self.bot.temp_channels.all():
            ch = guild.get_channel(int(raw_id))
            if not isinstance(ch, discord.VoiceChannel):
                continue
            members = [m for m in ch.members if not m.bot]
            actives_by_category.setdefault(ch.category_id, []).append({
                'channel_id': str(ch.id),
                'channel_name': ch.name,
                'user_count': len(members),
            })

        records = await self.bot.db.temp_channels.get_guild_channels(guild_id)
        entries = []
        for r in records:
            ch = guild.get_channel(r['channel_id'])
            category_id = ch.category_id if isinstance(ch, discord.VoiceChannel) else None
            active = sorted(
                actives_by_category.get(category_id, []) if ch else [],
                key=lambda a: a['channel_name'].lower(),
            )
            entries.append({
                'channel_id': str(r['channel_id']),
                'channel_name': ch.name if ch else 'deleted-channel',
                'format': r['format'],
                'active_channels': active,
                'total_users': sum(a['user_count'] for a in active),
            })
        return web.json_response({'entries': entries})

    async def _create_temp_channel(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        channel_id = body.get('channel_id')
        fmt = body.get('format', '⏳ | %name')
        if not channel_id:
            raise web.HTTPBadRequest(text='channel_id is required')

        await self.bot.db.temp_channels.create_channel(guild_id, int(channel_id), fmt)
        return web.json_response({'ok': True})

    async def _patch_temp_channel(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        channel_id = int(request.match_info['channel_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        fmt = body.get('format')
        if not fmt:
            raise web.HTTPBadRequest(text='format is required')

        await self.bot.db.temp_channels.update_channel(guild_id, channel_id, {'format': fmt})
        return web.json_response({'ok': True})

    async def _delete_temp_channel(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        channel_id = int(request.match_info['channel_id'])
        await self.bot.db.temp_channels.delete_channel(guild_id, channel_id)
        return web.json_response({'ok': True})

    async def _get_status_feed(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        cog = self.bot.get_cog('Misc')
        if cog is None:
            return web.json_response({'subscribed': False, 'channel': None})

        sub = await cog.get_subscriber(guild_id)
        if sub is None:
            return web.json_response({'subscribed': False, 'channel': None})

        guild = self.bot.get_guild(guild_id)
        return web.json_response({
            'subscribed': True,
            'channel': self._resolve_channel(guild, sub.channel_id) if guild else None,
        })

    async def _post_status_feed(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        channel_id = body.get('channel_id')
        if not channel_id:
            raise web.HTTPBadRequest(text='channel_id is required')

        cog = self.bot.get_cog('Misc')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='Misc cog not loaded')

        existing = await cog.get_subscriber(guild_id)
        if existing:
            await self.bot.db.incidents.update_channel(guild_id, int(channel_id))
        else:
            await self.bot.db.incidents.create_subscription(guild_id, int(channel_id))
        cog.get_subscriber.invalidate(guild_id)
        cog.get_subscribers.invalidate()
        return web.json_response({'ok': True})

    async def _delete_status_feed(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        cog = self.bot.get_cog('Misc')
        if cog is None:
            raise web.HTTPServiceUnavailable(text='Misc cog not loaded')

        await self.bot.db.incidents.unsubscribe(guild_id)
        cog.get_subscriber.invalidate(guild_id)
        cog.get_subscribers.invalidate()
        return web.json_response({'ok': True})

    async def _get_lockdowns(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        records = await self.bot.db.moderation.get_lockdowns(guild_id)
        entries = []
        for r in records:
            ch = guild.get_channel(r['channel_id'])
            entries.append({
                'channel_id': str(r['channel_id']),
                'channel_name': ch.name if ch else 'deleted-channel',
            })
        return web.json_response({'entries': entries})

    async def _lock_channels(self, request: web.Request) -> web.Response:
        import discord

        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        channel_ids = body.get('channel_ids', [])
        if not channel_ids:
            raise web.HTTPBadRequest(text='channel_ids is required')

        channels = []
        for cid in channel_ids:
            channel = guild.get_channel(int(cid))
            if channel is not None and isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                channels.append(channel)

        if not channels:
            raise web.HTTPBadRequest(text='no valid text or voice channels to lock')

        from app.cogs.moderation.lockdown import lock_channels
        success, failures = await lock_channels(self.bot, guild, channels, reason='Locked via dashboard')
        return web.json_response({'ok': True, 'locked': len(success), 'failures': len(failures)})

    async def _unlock_channels(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        channel_ids = body.get('channel_ids', [])
        if not channel_ids:
            raise web.HTTPBadRequest(text='channel_ids is required')

        ids = [int(c) for c in channel_ids]
        from app.cogs.moderation.lockdown import end_lockdown
        failures = await end_lockdown(self.bot, guild, channel_ids=ids, reason='Unlocked via dashboard')
        # Clear the lockdown bookkeeping so the channels no longer show as locked.
        await self.bot.db.moderation.remove_lockdowns(guild_id, ids)
        return web.json_response({'ok': True, 'failures': len(failures)})

    async def _get_highlights(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        limit = min(int(request.query.get('limit', '50')), 100)
        offset = int(request.query.get('offset', '0'))

        records = await self.bot.db.highlights.get_guild_configs(guild_id)
        entries = []
        for r in records:
            member = guild.get_member(r['user_id'])
            lookup = r.get('lookup') or []
            blocked = r.get('blocked') or []
            entries.append({
                'user_id': str(r['user_id']),
                'username': str(member) if member else 'Unknown',
                'triggers': lookup if isinstance(lookup, list) else list(lookup),
                'blocked_count': len(blocked) if isinstance(blocked, (list, set)) else 0,
            })

        total = len(entries)
        entries = entries[offset:offset + limit]
        return web.json_response({'entries': entries, 'total': total})

    async def _delete_highlight(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])
        config = await self.bot.db.highlights.get_config(guild_id, user_id)
        if config is None:
            raise web.HTTPNotFound(text='highlight config not found')
        await self.bot.db.highlights.delete_config(config['id'])
        return web.json_response({'ok': True})

    async def _get_emoji_stats(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        limit = min(int(request.query.get('limit', '50')), 200)
        offset = int(request.query.get('offset', '0'))
        summary = await self.bot.db.emoji_stats.get_guild_summary(guild_id)
        top = await self.bot.db.emoji_stats.get_top_guild_emojis(guild_id, limit=limit + offset)

        entries = []
        for r in top:
            emoji = self.bot.get_emoji(r['emoji_id'])
            entries.append({
                'emoji_id': str(r['emoji_id']),
                'emoji_name': emoji.name if emoji else 'unknown',
                'emoji_url': str(emoji.url) if emoji else None,
                'total': r['total'],
            })

        total = len(entries)
        entries = entries[offset:offset + limit]
        return web.json_response({
            'total_uses': summary['Count'] if summary else 0,
            'distinct_emojis': summary['Emoji'] if summary else 0,
            'entries': entries,
            'total': total,
        })

