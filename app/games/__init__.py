"""Pure, framework-agnostic game logic.

Modules under this package contain the rules and state machines for the bot's
games and must not import :mod:`discord` (or any UI/IO concern). The cogs in
``app.cogs.games`` import these engines, feed them user input, and map their
output back onto Discord embeds and views.
"""
