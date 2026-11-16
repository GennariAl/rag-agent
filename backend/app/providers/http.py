from collections.abc import AsyncGenerator

from dishka import Provider, Scope, provide
from httpx import AsyncClient


class HttpProvider(Provider):
    scope = Scope.APP

    @provide
    async def http(self) -> AsyncGenerator[AsyncClient]:
        async with AsyncClient() as client:
            yield client
