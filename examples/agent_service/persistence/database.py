# -*- coding: utf-8 -*-
"""Async PostgreSQL bootstrap for the lxScope application layer."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


class ApplicationDatabase:
    """Own the application PostgreSQL engine and numbered SQL migrations.

    SQLAlchemy and asyncpg are imported lazily so Redis-only local runs keep
    the same import behavior when the optional application database is not
    configured.
    """

    def __init__(
        self,
        url: str,
        *,
        migration_dir: Path | None = None,
    ) -> None:
        if not url.strip():
            raise ValueError("An application database URL is required.")
        self.url = url.strip()
        self.migration_dir = migration_dir or (
            Path(__file__).resolve().parents[1] / "migrations"
        )
        self._engine: Any | None = None

    @property
    def engine(self) -> Any:
        """Return the initialized async SQLAlchemy engine."""

        if self._engine is None:
            raise RuntimeError(
                "ApplicationDatabase is not initialized; call initialize() first.",
            )
        return self._engine

    async def initialize(self) -> None:
        """Create the engine and apply pending application migrations."""

        from sqlalchemy.ext.asyncio import create_async_engine

        if self._engine is None:
            self._engine = create_async_engine(
                self.url,
                pool_pre_ping=True,
            )

        await self._ensure_migration_table()
        await self._apply_migrations()

    async def _autocommit_connection(self) -> Any:
        """Return a connection whose SQL may manage its own transaction."""

        connection = await self.engine.connect()
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        return connection

    async def _ensure_migration_table(self) -> None:
        connection = await self._autocommit_connection()
        try:
            await connection.exec_driver_sql(
                "CREATE SCHEMA IF NOT EXISTS longxin_app",
            )
            await connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS longxin_app.schema_migrations (
                    version VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
            )
        finally:
            await connection.close()

    async def _applied_versions(self) -> set[str]:
        connection = await self._autocommit_connection()
        try:
            result = await connection.exec_driver_sql(
                "SELECT version FROM longxin_app.schema_migrations",
            )
            return {str(row[0]) for row in result}
        finally:
            await connection.close()

    def _migration_files(self) -> list[Path]:
        """Return forward migrations, excluding rollback scripts."""

        return sorted(
            path
            for path in self.migration_dir.glob("*.sql")
            if not path.name.endswith(".down.sql")
        )

    @staticmethod
    def _split_sql_statements(sql: str) -> list[str]:
        """Split PostgreSQL SQL without breaking quoted or DO-block semicolons."""

        statements: list[str] = []
        buffer: list[str] = []
        quote: str | None = None
        dollar_quote: str | None = None
        index = 0
        while index < len(sql):
            if dollar_quote is not None:
                if sql.startswith(dollar_quote, index):
                    buffer.append(dollar_quote)
                    index += len(dollar_quote)
                    dollar_quote = None
                else:
                    buffer.append(sql[index])
                    index += 1
                continue

            if quote is not None:
                character = sql[index]
                buffer.append(character)
                if character == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        buffer.append(sql[index + 1])
                        index += 2
                        continue
                    quote = None
                elif character == "\\" and quote == "'" and index + 1 < len(sql):
                    buffer.append(sql[index + 1])
                    index += 2
                    continue
                index += 1
                continue

            if sql.startswith("--", index):
                end = sql.find("\n", index)
                end = len(sql) if end == -1 else end
                buffer.append(sql[index:end])
                index = end
                continue

            if sql.startswith("/*", index):
                end = sql.find("*/", index + 2)
                end = len(sql) if end == -1 else end + 2
                buffer.append(sql[index:end])
                index = end
                continue

            if sql[index] in {"'", '"'}:
                quote = sql[index]
                buffer.append(sql[index])
                index += 1
                continue

            if sql[index] == "$":
                match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
                if match:
                    dollar_quote = match.group(0)
                    buffer.append(dollar_quote)
                    index += len(dollar_quote)
                    continue

            if sql[index] == ";":
                statement = "".join(buffer).strip()
                if statement:
                    statements.append(statement)
                buffer = []
                index += 1
                continue

            buffer.append(sql[index])
            index += 1

        statement = "".join(buffer).strip()
        if statement:
            statements.append(statement)
        return statements

    async def _apply_migrations(self) -> None:
        from sqlalchemy import text

        applied = await self._applied_versions()
        for migration in self._migration_files():
            version = migration.name
            if version in applied:
                continue

            sql = migration.read_text(encoding="utf-8")
            connection = await self._autocommit_connection()
            try:
                for statement in self._split_sql_statements(sql):
                    await connection.exec_driver_sql(statement)
                await connection.execute(
                    text(
                        """
                        INSERT INTO longxin_app.schema_migrations (version)
                        VALUES (:version)
                        ON CONFLICT (version) DO NOTHING
                        """,
                    ),
                    {"version": version},
                )
            finally:
                await connection.close()

    async def close(self) -> None:
        """Dispose the owned engine."""

        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None


__all__ = ["ApplicationDatabase"]
