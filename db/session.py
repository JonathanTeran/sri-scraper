"""Engine y session factory lazy-loaded para evitar errores de import."""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@lru_cache
def _build_engine() -> AsyncEngine:
    from config.settings import get_settings
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


@lru_cache
def _build_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        _build_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


def get_engine() -> AsyncEngine:
    return _build_engine()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _build_session_factory()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _build_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
