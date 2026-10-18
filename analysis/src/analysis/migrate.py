"""Run Alembic migrations programmatically over an existing async connection."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine


def _upgrade(connection: Connection) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


async def run_migrations(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_upgrade)
