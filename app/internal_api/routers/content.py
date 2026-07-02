"""Content endpoints: polls, giveaways, tags, commands, autoresponders, comics, etc."""
from __future__ import annotations

import datetime
from contextlib import suppress
from typing import Any

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import resolve_channel

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Content"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreatePollBody(BaseModel):
    question: str
    options: list[str]
    duration_seconds: int
    channel_id: int | str | None = None
    description: str | None = None
    color: str | None = None
    image_url: str | None = None
    thread_question: str | None = None


class PatchPollBody(BaseModel):
    question: str | None = None
    description: str | None = None
    image_url: str | None = None
    color: str | None = None
    options: list[str] | None = None


class CreateGiveawayBody(BaseModel):
    prize: str
    duration_seconds: int
    channel_id: int | str
    winners: int = 1
    description: str | None = None


class ToggleCommandBody(BaseModel):
    name: str
    enabled: bool
    channel_id: int | str | None = None


class ManagePlonkBody(BaseModel):
    action: str
    entity_id: int | str


class CreateAutoresponderBody(BaseModel):
    trigger: str
    response: str
    match_type: str = "contains"
    ignore_case: bool = True
    created_by: int | str | None = None


class PatchAutoresponderBody(BaseModel):
    enabled: bool


class CreateComicBody(BaseModel):
    brand: str
    channel_id: int | str
    format: str = "Summary"
    day: int = 1
    ping: int | str | None = None
    pin: bool = False


class PatchComicBody(BaseModel):
    channel_id: int | str | None = None
    format: str | None = None
    day: int | None = None
    ping: int | str | None = None
    pin: bool | None = None

    model_config = {"extra": "ignore"}


class CreateTempChannelBody(BaseModel):
    channel_id: int | str
    format: str = "⏳ | %name"


class PatchTempChannelBody(BaseModel):
    format: str


class PostStatusFeedBody(BaseModel):
    channel_id: int | str


class LockChannelsBody(BaseModel):
    channel_ids: list[int | str]


class UnlockChannelsBody(BaseModel):
    channel_ids: list[int | str]


class ImportTagsBody(BaseModel):
    tags: list[dict[str, Any]]
    owner_id: int | str | None = None


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


@router.get("/polls")
async def get_polls(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records = await bot.db.polls.get_for_guild(guild.id)

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
    return {'polls': polls, 'total': total}


@router.post("/polls")
async def create_poll(guild: GuildDep, bot: BotDep, body: CreatePollBody) -> dict:
    cog = bot.get_cog('Polls')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='polls cog not loaded')

    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='question is required')

    options = [str(opt).strip() for opt in body.options if str(opt).strip()]
    if len(options) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='at least 2 options are required')
    if len(options) > 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='a poll can have at most 8 options')

    duration = body.duration_seconds
    if duration <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='duration must be a positive number of seconds')

    config = await bot.db.get_guild_config(guild_id=guild.id)

    if body.channel_id:
        try:
            channel = guild.get_channel(int(body.channel_id))
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='invalid channel')
    else:
        channel = config.poll_channel if config else None

    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='a valid text channel is required (set a poll channel or pass channel_id)',
        )

    if bot.timers is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='the timers system is not available')

    expires = discord.utils.utcnow() + datetime.timedelta(seconds=duration)

    poll = await cog.create_poll_from_dashboard(
        guild,
        channel,
        author_id=bot.user.id,
        question=question,
        options=options,
        expires=expires,
        description=body.description.strip() if body.description else None,
        color=body.color.strip() if body.color else None,
        image_url=body.image_url.strip() if body.image_url else None,
        thread_question=body.thread_question.strip() if body.thread_question else None,
    )

    return {'ok': True, 'id': poll.id}


@router.patch("/polls/{poll_id}")
async def patch_poll(guild: GuildDep, bot: BotDep, poll_id: int, body: PatchPollBody) -> dict:
    record = await bot.db.polls.get(poll_id, guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='poll not found')

    metadata = record.get('metadata') or {}
    kwargs = metadata.get('kwargs', {})

    if not kwargs.get('running', False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='cannot edit a poll that has ended')

    # Apply edits to kwargs (an empty question is ignored rather than clearing the field)
    if body.question:
        kwargs['content'] = body.question

    if body.description is not None:
        kwargs['description'] = body.description if body.description else None

    if body.image_url is not None:
        kwargs['image_url'] = body.image_url if body.image_url else None

    if body.color is not None:
        kwargs['color'] = body.color if body.color else None

    if body.options is not None:
        new_options = body.options
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
    await bot.db.polls.update(poll_id, {'metadata': metadata})

    return {'ok': True}


@router.post("/polls/{poll_id}/end")
async def end_poll(guild: GuildDep, bot: BotDep, poll_id: int) -> dict:
    cog = bot.get_cog('Polls')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='polls cog not loaded')

    polls = await cog.get_guild_polls(guild.id)
    poll = next((p for p in polls if p.id == poll_id), None)
    if poll is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='poll not found')

    result = await cog.end_poll(poll)
    if result is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='poll is already ended')

    return {'ok': True}


# ---------------------------------------------------------------------------
# Giveaways
# ---------------------------------------------------------------------------


@router.get("/giveaways")
async def get_giveaways(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records = await bot.db.giveaways.get_guild_giveaways(guild.id)

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
            'ended': (
                datetime.datetime.fromisoformat(_kwargs.get('expires')).astimezone(datetime.UTC)
                < datetime.datetime.now(datetime.UTC)
            ),
            'ends_at': _kwargs.get('expires'),
        })

    total = len(giveaways)
    giveaways = giveaways[offset:offset + limit]
    return {'giveaways': giveaways, 'total': total}


@router.post("/giveaways")
async def create_giveaway(guild: GuildDep, bot: BotDep, body: CreateGiveawayBody) -> dict:
    cog = bot.get_cog('Giveaways')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='giveaways cog not loaded')
    if bot.timers is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='the timers system is not available')

    prize = body.prize.strip()
    if not prize:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='prize is required')
    if len(prize) > 256:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='prize must be 256 characters or less')

    duration = body.duration_seconds
    if duration <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='duration must be a positive number of seconds')

    winners = body.winners
    if winners < 1 or winners > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='winner count must be between 1 and 100')

    description = body.description.strip() if body.description else None

    try:
        channel = guild.get_channel(int(body.channel_id)) if body.channel_id else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='invalid channel')
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='a valid text channel is required')

    from app.cogs.giveaway import GiveawayEnterButton

    expires = discord.utils.utcnow() + datetime.timedelta(seconds=duration)
    message = await channel.send(embed=discord.Embed(description='*Preparing Giveaway...*'))
    giveaway = await cog.create_giveaway(
        message.channel.id,
        message.id,
        guild.id,
        bot.user.id,
        description=description,
        prize=prize,
        winner_count=winners,
        created=discord.utils.utcnow().isoformat(),
        expires=expires.isoformat(),
    )

    await bot.timers.create(
        expires,
        'giveaway',
        giveaway_id=giveaway.id,
        created=discord.utils.utcnow(),
        timezone='UTC',
    )

    view = discord.ui.View(timeout=None)
    view.add_item(GiveawayEnterButton(giveaway))
    await message.edit(embed=giveaway.to_embed(), view=view)

    return {'ok': True, 'id': giveaway.id}


@router.post("/giveaways/{giveaway_id}/end")
async def end_giveaway(guild: GuildDep, bot: BotDep, giveaway_id: int) -> dict:
    cog = bot.get_cog('Giveaways')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='giveaways cog not loaded')

    giveaway = await cog.get_guild_giveaway(guild.id, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='giveaway not found')

    # Draws winners now and tidies the message (same path as the timer firing).
    await cog.end_giveaway(giveaway.id)
    return {'ok': True}


@router.delete("/giveaways/{giveaway_id}")
async def delete_giveaway(guild: GuildDep, bot: BotDep, giveaway_id: int) -> dict:
    cog = bot.get_cog('Giveaways')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='giveaways cog not loaded')

    giveaway = await cog.get_guild_giveaway(guild.id, giveaway_id)
    if giveaway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='giveaway not found')

    # Cancel without drawing: drop the pending timer, delete the record, and
    # strike through the announcement message so nobody keeps entering.
    if bot.timers is not None:
        with suppress(Exception):
            await bot.timers.delete('giveaway', giveaway_id=str(giveaway_id))
    await bot.db.giveaways.delete_giveaway(giveaway_id)

    with suppress(Exception):
        if giveaway.message is discord.utils.MISSING:
            await giveaway.fetch_message()
        if giveaway.message:
            await giveaway.message.edit(
                content=f'This giveaway for *{giveaway.prize}* was cancelled.', embed=None, view=None)

    return {'ok': True}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@router.get("/tags")
async def get_tags(guild: GuildDep, bot: BotDep) -> dict:
    total = await bot.db.tags.count_tags(guild.id)
    # Full directory (every parent tag), used by the dashboard's searchable
    # table, export selection and per-tag preview. Owners are resolved from
    # the member cache only (no per-tag fetch_user -- the list can be large).
    all_tags = await bot.db.tags.get_guild_tags(guild.id)
    top_creators = await bot.db.tags.get_top_tag_creators(guild.id, limit=10)
    total_uses = await bot.db.tags.count_tag_command_uses(guild.id)

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

    return {
        'total': total,
        'total_uses': total_uses,
        'tags': tags,
        'top_creators': creators,
    }


@router.get("/tags/export")
async def export_tags(guild: GuildDep, bot: BotDep) -> dict:
    records = await bot.db.tags.export_tags(guild.id)
    tags = [{'name': r['name'], 'content': r['content']} for r in records]
    return {'tags': tags}


@router.post("/tags/import")
async def import_tags(guild: GuildDep, bot: BotDep, body: ImportTagsBody) -> dict:
    """Bulk-create tags from a parsed (name, content) list.

    Access is gated server-side (admin/Manage-Server) by the dashboard, which
    also malware-scans and sanitises the uploaded file. Tag creation goes
    through the parameterised repo layer (no string-built SQL), so the import
    cannot inject SQL. Duplicates and reserved names are skipped, not failed.
    """
    raw = body.tags
    if not isinstance(raw, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='tags must be a list')

    try:
        owner_id = int(body.owner_id) if body.owner_id else bot.user.id
    except (TypeError, ValueError):
        owner_id = bot.user.id

    root = bot.get_command('tag')
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

        existing = await bot.db.tags.get_tag_record(name, location_id=guild.id)
        if existing is not None:
            skipped += 1
            continue

        try:
            await bot.db.tags.create_tag(name, content, owner_id, guild.id)
            created += 1
        except Exception:
            failed.append({'name': name, 'error': 'could not be created'})

    return {'ok': True, 'created': created, 'skipped': skipped, 'failed': failed}


@router.get("/tags/{tag_id}")
async def get_tag_detail(guild: GuildDep, bot: BotDep, tag_id: int) -> dict:
    record = await bot.db.tags.get_tag_record(tag_id, location_id=guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='tag not found')

    owner_id = record.get('owner_id')
    member = guild.get_member(owner_id) if owner_id else None
    return {
        'id': record['id'],
        'name': record['name'],
        'content': record['content'],
        'owner_id': str(owner_id) if owner_id else None,
        'owner_name': member.display_name if member else None,
        'uses': record.get('uses', 0),
        'created_at': record['created_at'].isoformat() if record.get('created_at') else None,
    }


@router.delete("/tags/{tag_id}")
async def delete_tag(guild: GuildDep, bot: BotDep, tag_id: int) -> dict:
    record = await bot.db.tags.get_tag_record(tag_id, location_id=guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='tag not found')

    # delete_tag also removes every alias that points to it.
    await bot.db.tags.delete_tag(record['id'])
    return {'ok': True}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@router.get("/commands")
async def get_commands(guild: GuildDep, bot: BotDep) -> dict:
    command_config = await bot.db.guilds.get_command_config(guild.id)
    plonks = await bot.db.guilds.get_plonks(guild.id)

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
    for cmd in bot.walk_commands():
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

    return {
        'commands': sorted(all_commands, key=lambda c: (c['category'], c['name'])),
        'plonks': plonk_list,
    }


@router.post("/commands/toggle")
async def toggle_command(guild: GuildDep, bot: BotDep, body: ToggleCommandBody) -> dict:
    if body.enabled:
        # Re-enable: remove the relevant disable entries for this command.
        if body.channel_id:
            await bot.db.guilds.clear_command_config_channel(guild.id, body.name, int(body.channel_id))
        else:
            # Clears both the guild-wide row and every per-channel row.
            await bot.db.guilds.clear_command_config(guild.id, body.name)
    else:
        # Disable: a channel_id targets a single channel; a NULL channel_id
        # disables the command server-wide via one row (not one row per channel).
        await bot.db.guilds.set_command_config(
            guild.id, int(body.channel_id) if body.channel_id else None, body.name, whitelist=False)

    # Keep the Config cog's resolved-permissions cache in sync with the write.
    config_cog = bot.get_cog('Config')
    if config_cog is not None:
        config_cog.get_commands_configuration.invalidate(guild.id)

    return {'ok': True}


@router.post("/plonks")
async def manage_plonk(guild: GuildDep, bot: BotDep, body: ManagePlonkBody) -> dict:
    if body.action not in ('add', 'remove'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='action must be add or remove')

    entity_id = int(body.entity_id)

    if body.action == 'add':
        await bot.db.guilds.add_plonk(guild.id, entity_id)
    else:
        await bot.db.guilds.remove_plonks(guild.id, [entity_id])

    # Drop the Config cog's plonk-status cache for this guild.
    config_cog = bot.get_cog('Config')
    if config_cog is not None:
        config_cog.is_plonked.invalidate_containing(f"{guild.id!r}:")

    return {'ok': True}


# ---------------------------------------------------------------------------
# Autoresponders
# ---------------------------------------------------------------------------


@router.get("/autoresponders")
async def get_autoresponders(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default=''),
) -> dict:
    records = await bot.db.autoresponders.get_all(guild.id)
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

    search_lower = search.lower()
    if search_lower:
        entries = [e for e in entries if search_lower in e['trigger'].lower() or search_lower in e['response'].lower()]

    total = len(entries)
    entries = entries[offset:offset + limit]
    return {'entries': entries, 'total': total}


@router.post("/autoresponders")
async def create_autoresponder(guild: GuildDep, bot: BotDep, body: CreateAutoresponderBody) -> dict:
    trigger = body.trigger.strip()
    response = body.response.strip()

    if not trigger or not response:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='trigger and response are required')

    result = await bot.db.autoresponders.create(
        guild.id, trigger, response,
        match_type=body.match_type, ignore_case=body.ignore_case, created_by=int(body.created_by) if body.created_by else 0,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='an autoresponder with that trigger already exists')
    return {'ok': True}


@router.delete("/autoresponders/{trigger:path}")
async def delete_autoresponder(guild: GuildDep, bot: BotDep, trigger: str) -> dict:
    result = await bot.db.autoresponders.delete(guild.id, trigger)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='autoresponder not found')
    return {'ok': True}


@router.patch("/autoresponders/{trigger:path}")
async def patch_autoresponder(guild: GuildDep, bot: BotDep, trigger: str, body: PatchAutoresponderBody) -> dict:
    result = await bot.db.autoresponders.set_enabled(guild.id, trigger, body.enabled)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='autoresponder not found')
    return {'ok': True}


# ---------------------------------------------------------------------------
# Comics
# ---------------------------------------------------------------------------


@router.get("/comics")
async def get_comics(guild: GuildDep, bot: BotDep) -> dict:
    feeds = []
    for brand_name in ('MARVEL', 'DC', 'MANGA'):
        record = await bot.db.comics.get_config(guild.id, brand_name)
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
    return {'feeds': feeds}


@router.post("/comics")
async def create_comic(guild: GuildDep, bot: BotDep, body: CreateComicBody) -> dict:
    if not body.brand or not body.channel_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='brand and channel_id are required')

    existing = await bot.db.comics.get_config(guild.id, body.brand)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'already subscribed to {body.brand}')

    config_dict = {
        'guild_id': guild.id,
        'channel_id': int(body.channel_id),
        'brand': body.brand,
        'format': body.format,
        'day': body.day,
        'ping': int(body.ping) if body.ping else None,
        'pin': body.pin,
        'next_pull': None,
    }
    await bot.db.comics.create_config(config_dict)
    return {'ok': True}


@router.patch("/comics/{brand}")
async def patch_comic(guild: GuildDep, bot: BotDep, brand: str, body: PatchComicBody) -> dict:
    cog = bot.get_cog('Comics')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Comics cog not loaded')

    config = await cog.get_comic_config(guild.id, brand)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='comic feed not found')

    updates: dict[str, object] = {}
    if body.channel_id is not None:
        updates['channel_id'] = int(body.channel_id)
    if body.format is not None:
        updates['format'] = body.format
    if body.day is not None:
        updates['day'] = body.day
        updates['next_pull'] = config.next_scheduled(body.day)
    if body.ping is not None:
        updates['ping'] = int(body.ping) if body.ping else None
    if body.pin is not None:
        updates['pin'] = body.pin

    if updates:
        await bot.db.comics.update_config(config.id, updates)
    return {'ok': True}


@router.delete("/comics/{brand}")
async def delete_comic(guild: GuildDep, bot: BotDep, brand: str) -> dict:
    await bot.db.comics.delete_config(guild.id, brand)
    return {'ok': True}


@router.post("/comics/{brand}/push")
async def push_comic(guild: GuildDep, bot: BotDep, brand: str) -> dict:
    cog = bot.get_cog('Comics')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Comics cog not loaded')
    record = await bot.db.comics.get_config(guild.id, brand)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='comic feed not found')
    bot.dispatch('comic_push', guild.id, brand)
    return {'ok': True}


# ---------------------------------------------------------------------------
# Temp Channels
# ---------------------------------------------------------------------------


@router.get("/temp-channels")
async def get_temp_channels(guild: GuildDep, bot: BotDep) -> dict:
    # Live spawned channels are tracked globally in ``bot.temp_channels``
    # ({channel_id: True}); there is no stored hub->spawned link, so we group
    # the currently-active ones by their category and attach them to the hub
    # that lives in the same category (the spawner always inherits the hub's
    # category). Multiple hubs sharing a category will list the same actives.
    actives_by_category: dict[int | None, list[dict]] = {}
    for raw_id in bot.temp_channels.all():
        ch = guild.get_channel(int(raw_id))
        if not isinstance(ch, discord.VoiceChannel):
            continue
        members = [m for m in ch.members if not m.bot]
        actives_by_category.setdefault(ch.category_id, []).append({
            'channel_id': str(ch.id),
            'channel_name': ch.name,
            'user_count': len(members),
        })

    records = await bot.db.temp_channels.get_guild_channels(guild.id)
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
    return {'entries': entries}


@router.post("/temp-channels")
async def create_temp_channel(guild: GuildDep, bot: BotDep, body: CreateTempChannelBody) -> dict:
    if not body.channel_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='channel_id is required')

    await bot.db.temp_channels.create_channel(guild.id, int(body.channel_id), body.format)
    return {'ok': True}


@router.patch("/temp-channels/{channel_id}")
async def patch_temp_channel(guild: GuildDep, bot: BotDep, channel_id: int, body: PatchTempChannelBody) -> dict:
    if not body.format:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='format is required')

    await bot.db.temp_channels.update_channel(guild.id, channel_id, {'format': body.format})
    return {'ok': True}


@router.delete("/temp-channels/{channel_id}")
async def delete_temp_channel(guild: GuildDep, bot: BotDep, channel_id: int) -> dict:
    await bot.db.temp_channels.delete_channel(guild.id, channel_id)
    return {'ok': True}


# ---------------------------------------------------------------------------
# Status Feed
# ---------------------------------------------------------------------------


@router.get("/status-feed")
async def get_status_feed(guild: GuildDep, bot: BotDep) -> dict:
    cog = bot.get_cog('Misc')
    if cog is None:
        return {'subscribed': False, 'channel': None}

    sub = await cog.get_subscriber(guild.id)
    if sub is None:
        return {'subscribed': False, 'channel': None}

    return {
        'subscribed': True,
        'channel': resolve_channel(guild, sub.channel_id),
    }


@router.post("/status-feed")
async def post_status_feed(guild: GuildDep, bot: BotDep, body: PostStatusFeedBody) -> dict:
    if not body.channel_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='channel_id is required')

    cog = bot.get_cog('Misc')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Misc cog not loaded')

    existing = await cog.get_subscriber(guild.id)
    if existing:
        await bot.db.incidents.update_channel(guild.id, int(body.channel_id))
    else:
        await bot.db.incidents.create_subscription(guild.id, int(body.channel_id))
    cog.get_subscriber.invalidate(guild.id)
    cog.get_subscribers.invalidate()
    return {'ok': True}


@router.delete("/status-feed")
async def delete_status_feed(guild: GuildDep, bot: BotDep) -> dict:
    cog = bot.get_cog('Misc')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Misc cog not loaded')

    await bot.db.incidents.unsubscribe(guild.id)
    cog.get_subscriber.invalidate(guild.id)
    cog.get_subscribers.invalidate()
    return {'ok': True}


# ---------------------------------------------------------------------------
# Lockdowns
# ---------------------------------------------------------------------------


@router.get("/lockdowns")
async def get_lockdowns(guild: GuildDep, bot: BotDep) -> dict:
    records = await bot.db.moderation.get_lockdowns(guild.id)
    entries = []
    for r in records:
        ch = guild.get_channel(r['channel_id'])
        entries.append({
            'channel_id': str(r['channel_id']),
            'channel_name': ch.name if ch else 'deleted-channel',
        })
    return {'entries': entries}


@router.post("/lockdowns/lock")
async def lock_channels(guild: GuildDep, bot: BotDep, body: LockChannelsBody) -> dict:
    if not body.channel_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='channel_ids is required')

    channels = []
    for cid in body.channel_ids:
        channel = guild.get_channel(int(cid))
        if channel is not None and isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            channels.append(channel)

    if not channels:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no valid text or voice channels to lock')

    from app.cogs.moderation.lockdown import lock_channels as _lock_channels
    success, failures = await _lock_channels(bot, guild, channels, reason='Locked via dashboard')
    return {'ok': True, 'locked': len(success), 'failures': len(failures)}


@router.post("/lockdowns/unlock")
async def unlock_channels(guild: GuildDep, bot: BotDep, body: UnlockChannelsBody) -> dict:
    if not body.channel_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='channel_ids is required')

    ids = [int(c) for c in body.channel_ids]
    from app.cogs.moderation.lockdown import end_lockdown
    failures = await end_lockdown(bot, guild, channel_ids=ids, reason='Unlocked via dashboard')
    # Clear the lockdown bookkeeping so the channels no longer show as locked.
    await bot.db.moderation.remove_lockdowns(guild.id, ids)
    return {'ok': True, 'failures': len(failures)}


# ---------------------------------------------------------------------------
# Highlights
# ---------------------------------------------------------------------------


@router.get("/highlights")
async def get_highlights(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records = await bot.db.highlights.get_guild_configs(guild.id)
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
    return {'entries': entries, 'total': total}


@router.delete("/highlights/{user_id}")
async def delete_highlight(guild: GuildDep, bot: BotDep, user_id: int) -> dict:
    config = await bot.db.highlights.get_config(guild.id, user_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='highlight config not found')
    await bot.db.highlights.delete_config(config['id'])
    return {'ok': True}


# ---------------------------------------------------------------------------
# Emoji Stats
# ---------------------------------------------------------------------------


@router.get("/emoji-stats")
async def get_emoji_stats(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    summary = await bot.db.emoji_stats.get_guild_summary(guild.id)
    top = await bot.db.emoji_stats.get_top_guild_emojis(guild.id, limit=limit + offset)

    entries = []
    for r in top:
        emoji = bot.get_emoji(r['emoji_id'])
        entries.append({
            'emoji_id': str(r['emoji_id']),
            'emoji_name': emoji.name if emoji else 'unknown',
            'emoji_url': str(emoji.url) if emoji else None,
            'total': r['total'],
        })

    total = len(entries)
    entries = entries[offset:offset + limit]
    return {
        'total_uses': summary['Count'] if summary else 0,
        'distinct_emojis': summary['Emoji'] if summary else 0,
        'entries': entries,
        'total': total,
    }
