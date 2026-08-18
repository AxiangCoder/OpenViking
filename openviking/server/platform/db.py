"""Platform 异步数据库接入（SQLAlchemy 2 async + asyncpg）。

engine/session_factory 为进程级单例，绑定 platform_config；
测试通过 `build_engine/build_session_factory` 自行构造隔离实例。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from openviking.server.platform.config import platform_config


class Base(DeclarativeBase):
    """Platform IAM ORM 基类。"""


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_size=5, max_overflow=10)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


engine = build_engine(platform_config.database_url)
session_factory = build_session_factory(engine)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个 Session。"""
    async with session_factory() as session:
        yield session
