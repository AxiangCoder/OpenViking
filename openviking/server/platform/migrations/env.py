"""Platform Alembic async 迁移环境。

- 目标元数据：openviking.server.platform.db.Base（9 张 iam_* 表注册于 models）。
- URL 优先级：config 中已设置的 sqlalchemy.url（测试注入）> PlatformConfig。
"""

import asyncio
import sys
import types
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# openviking 包根导入依赖 ragfs_python（Rust binding，maturin develop 安装）。
# 迁移只需要 platform 子模块，在无 binding 的开发环境下降级为包根 stub；
# 真实包可导入时走正常路径。
try:
    import openviking  # noqa: F401

    _OPENVIKING_STUBBED = False
except ImportError:
    _openviking_stub = types.ModuleType("openviking")
    _openviking_stub.pyagfs = types.ModuleType("openviking.pyagfs")
    sys.modules["openviking"] = _openviking_stub
    sys.modules["openviking.pyagfs"] = _openviking_stub.pyagfs
    _OPENVIKING_STUBBED = True

import openviking.server.platform.models  # noqa: F401  (register ORM tables)
from openviking.server.platform.config import platform_config
from openviking.server.platform.db import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", platform_config.database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def _run_async_migrations_in_new_loop() -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run_async_migrations())
    finally:
        loop.close()


def run_migrations_online() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # CLI：独立进程，直接 asyncio.run
        asyncio.run(run_async_migrations())
    else:
        # 被测试等已在运行的事件循环内嵌调用（pytest-asyncio）：
        # 在新线程中创建独立 loop 执行，避免 asyncio.run 冲突。
        import threading

        outcome: list[BaseException | None] = [None]

        def _target() -> None:
            try:
                _run_async_migrations_in_new_loop()
            except BaseException as exc:  # noqa: BLE001
                outcome[0] = exc

        thread = threading.Thread(target=_target, name="alembic-migrations")
        thread.start()
        thread.join()
        if outcome[0] is not None:
            raise outcome[0]


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
