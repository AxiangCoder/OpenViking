"""Platform tests 共享夹具（要求本地 PostgreSQL 16，见 README）。

默认连接 Spike 约定实例：
  docker run -d --name ovp-pg16 -e POSTGRES_USER=ov_platform \
    -e POSTGRES_PASSWORD=ov_platform_dev -e POSTGRES_DB=ov_platform \
    -p 55432:5432 postgres:16-alpine

通过 OV_PLATFORM_TEST_ADMIN_URL 覆盖管理连接；测试库固定 ov_platform_test，
会话级重建并执行 alembic upgrade head。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.platform.db import build_engine, build_session_factory
from openviking.server.platform.iam import PostgresIamRepository

TEST_DB_NAME = "ov_platform_test"

# 管理连接（asyncpg 直连，postgres 库）；测试库 DSN 由它派生。
ADMIN_URL = os.environ.get(
    "OV_PLATFORM_TEST_ADMIN_URL",
    "postgresql://ov_platform:ov_platform_dev@127.0.0.1:55432/postgres",
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _alembic_config(database_url: str) -> Config:
    cfg = Config(os.path.join(_REPO_ROOT, "openviking/server/platform/alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest_asyncio.fixture(scope="session")
async def test_database() -> AsyncGenerator[str, None]:
    """重建测试库并执行全部迁移（fresh upgrade）。"""
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
        await admin.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    finally:
        await admin.close()

    database_url = ADMIN_URL.replace("postgresql://", "postgresql+asyncpg://").replace(
        "/postgres", f"/{TEST_DB_NAME}"
    )
    command.upgrade(_alembic_config(database_url), "head")
    yield database_url
    command.downgrade(_alembic_config(database_url), "base")

    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    finally:
        await admin.close()


@pytest_asyncio.fixture
async def session_factory(
    test_database: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = build_engine(test_database)
    factory = build_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """每个测试前清空全部 iam_* 表，保证测试间隔离。"""
    async with session_factory() as s:
        await s.execute(
            text(
                "TRUNCATE TABLE iam_audit_events, iam_user_roles, iam_role_permissions, "
                "iam_sessions, iam_roles, iam_api_credentials, iam_users, iam_accounts, "
                "iam_permissions CASCADE"
            )
        )
        await s.commit()
        yield s
        await s.rollback()


@pytest.fixture
def repo() -> PostgresIamRepository:
    return PostgresIamRepository()
