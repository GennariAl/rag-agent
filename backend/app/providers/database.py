from collections.abc import AsyncGenerator
from typing import Any

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database_settings import DatabaseSettings


class DatabaseProvider(Provider):
    @provide(scope=Scope.APP)
    def async_engine(self, database_settings: DatabaseSettings) -> AsyncEngine:
        return create_async_engine(
            str(database_settings.database_uri),
            echo=False,
            pool_size=10,
            max_overflow=20,
        )

    @provide(scope=Scope.APP)
    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(engine)

    @provide(scope=Scope.REQUEST)
    async def async_session(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> AsyncGenerator[AsyncSession, Any]:
        async with session_factory() as session, session.begin():
            yield session
