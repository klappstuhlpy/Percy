from typing import TYPE_CHECKING, Any, Self, TypedDict, NotRequired

from discord import Asset as _Asset, AppInfo
from discord import Permissions
from discord.http import Route
from discord.state import ConnectionState
from discord.types.appinfo import AppInfo as AppInfoPayload
from discord.webhook.async_ import _WebhookState

_State = ConnectionState | _WebhookState


class Asset(_Asset):
    if TYPE_CHECKING:
        name: str

    @classmethod
    def _from_app_asset(cls, state: _State, object_id: int, asset_id: str, name: str) -> Self:
        self = cls(
            state,
            url=f'{cls.BASE}/app-assets/{object_id}/{asset_id}.png?size=4096',
            key=asset_id,
            animated=False
        )
        self.name = name
        return self


class AssetPayload(TypedDict):
    id: str
    name: str
    type: int


class RPCAppInfoPayload(AppInfoPayload, total=False):
    type: NotRequired[Any]
    permissions: NotRequired[int]
    is_monetized: NotRequired[bool]
    category_ids: NotRequired[list[int]]
    approximate_guild_count: NotRequired[int]


class RPCAppInfo(AppInfo):
    """Represents an AppInfo given by the /rpc endpoint."""

    def __init__(self, *, state: ConnectionState, data: RPCAppInfoPayload) -> None:
        super().__init__(state=state, data=data)
        self._permissions: int = data.get('permissions', 0)
        self.type: Any | None = data.get('type')
        self.is_monetized: bool | None = data.get('is_monetized')
        self.category_ids: list[int] | None = data.get('category_ids')
        self.approximate_guild_count: int | None = data.get('approximate_guild_count')

    @property
    def permissions(self) -> Permissions | None:
        """:class:`Permissions`: The application's permissions."""
        if not self._permissions:
            return None
        return Permissions(self._permissions)

    async def get_assets(self) -> list[Asset]:
        """|coro|

        Retrieves the application's assets.

        Returns
        --------
        :class:`dict`: The application's assets.
        """
        assets: list[AssetPayload] = await self._state.http.request(
            Route(
                'GET',
                '/oauth2/applications/{app_id}/assets',
                app_id=self.id
            )
        )
        return [Asset._from_app_asset(self._state, self.id, asset['id'], name=asset['name']) for asset in assets]

    async def edit(self, **kwargs) -> Any:
        raise NotImplementedError('Editing RPCAppInfo is not supported.')
