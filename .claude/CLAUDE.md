# Project Refactoring Brief: Percy Discord Bot

You are tasked with refactoring the Percy Discord bot codebase. The current architecture suffers from tight coupling, mixing data access, business logic, and presentation (Discord UI) within single, bloated files (specifically `mod.py`, `_poker.py`, `leveling.py`, `models.py`, and `polls.py`). 

Your goal is to modularize the codebase using the architectural guidelines and file structures detailed below. Please execute these changes phase by phase, ensuring all imports and dependencies are updated to prevent breaking the bot.

## Phase 1: Implement a Data Access Layer (Repository Pattern)
**Goal:** Remove raw SQL (`asyncpg`) queries from Discord command callbacks and UI views.
**Target Structure:**
Create a new directory: `app/database/repositories/`
Create the following files:
* `app/database/repositories/base.py`: Contains a generic repository class with access to the connection pool.
* `app/database/repositories/guilds.py`: Handles `guild_config` and `guild_gatekeeper` queries.
* `app/database/repositories/users.py`: Handles `user_settings` and `economy` queries.
* `app/database/repositories/polls.py`: Handles `polls` and `poll_entry` queries.
**Instructions:** Extract SQL queries from the cogs. Instantiate these repositories within the `Database` class in `app/database/base.py` so cogs can access them via `await self.bot.db.guilds.get_config(guild_id)` instead of writing raw SQL.

## Phase 2: Decouple UI from Business Logic (MVVM-Lite)
**Goal:** Extract `discord.ui.View`, `discord.ui.Modal`, and complex `EmbedBuilder` logic from command files.
**Target Structure:**
Create UI-specific modules next to their respective cogs:
* `app/cogs/games/ui/poker_ui.py`: Move `TexasHoldem` views and Modals here.
* `app/cogs/polls_ui.py` (or `app/cogs/polls/ui.py`): Move `PollReasonModal`, `EditModal`, and all poll-related buttons here.
* `app/cogs/leveling_ui.py`: Move `AddLevelRoleModal`, `RemoveLevelRolesSelect`, and `InteractiveLevelRolesView` here.
**Instructions:**
The cog files should only handle receiving the command, fetching data via the database layer, and passing that data to these UI classes. Pass the `Bot` or `Context` instance to the UI classes explicitly rather than relying on global state.

## Phase 3: Deconstruct the "God" Cog (`mod.py`)
**Goal:** Split the massive `app/cogs/mod.py` file into focused, single-responsibility cogs.
**Target Structure:**
Delete `app/cogs/mod.py` and replace it with a new directory: `app/cogs/moderation/`
Create the following files:
* `app/cogs/moderation/__init__.py`: Setup function to load all moderation cogs.
* `app/cogs/moderation/core.py`: Handles standard commands (`kick`, `ban`, `mute`, `purge`).
* `app/cogs/moderation/gatekeeper.py`: Contains the `Gatekeeper` logic, captcha generation, and queue management.
* `app/cogs/moderation/antispam.py`: Contains the `SpamChecker` logic, raid protection, and mention-spam listeners.
* `app/cogs/moderation/lockdowns.py`: Handles the `lockdown` command group and permission overrides.
**Instructions:**
Ensure the `SpamChecker` and `Gatekeeper` states remain accessible if they need to cross-communicate, potentially by registering them on the `Bot` instance or creating a shared service class.

## Phase 4: Isolate Game Engines
**Goal:** Remove all `discord.*` imports from the core game rules and state machines.
**Target Structure:**
Create a new directory: `app/games/engine/`
Create the following files:
* `app/games/engine/poker.py`: Move `Hand`, `Ranker`, `Pot`, `Player`, and the core `TexasHoldem` state machine here.
* `app/games/engine/blackjack.py`: Move card drawing, hand evaluation, and win-condition logic here.
**Instructions:**
The game engines must return pure data (e.g., ints, strings, enums). The cogs in `app/cogs/games/` will import these engines, feed them user inputs, and map the engine's output back to Discord embeds and views.

## Phase 5: Modularize Core Models
**Goal:** Split `app/core/models.py` to make the bot's foundation easier to navigate.
**Target Structure:**
Inside `app/core/`, create the following files:
* `app/core/context.py`: Move `Context` and `HybridContext` here.
* `app/core/command.py`: Move `Command`, `GroupCommand`, `HybridCommand`, and the decorators (`@command`, `@group`) here.
* `app/core/embeds.py`: Move `EmbedBuilder` here.
* `app/core/permissions.py`: Move `PermissionSpec` and `PermissionTemplate` here.
**Instructions:**
Update `app/core/__init__.py` to expose these newly separated classes so external imports across the bot do not break.

Please acknowledge these instructions and let me know which Phase you would like to begin executing first.