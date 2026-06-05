"""
DuckDB database connection and helper utilities.
"""

import logging
import os
import pathlib
from typing import Any

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# Resolve the schema file relative to this module
_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


class Database:
    """Thin wrapper around a DuckDB connection."""

    def __init__(self, db_path: str):
        """
        Connect to (or create) a DuckDB database file.

        Args:
            db_path: Path to the .db file. Use ':memory:' for an in-memory DB.
        """
        self.db_path = db_path
        logger.info("Connecting to DuckDB at '%s'", db_path)
        self._conn = duckdb.connect(db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def initialize_schema(self, schema_path: str | None = None) -> None:
        """
        Read and execute the DDL in schema.sql.

        Args:
            schema_path: Optional override path. Defaults to the bundled schema.sql.
        """
        path = schema_path or str(_SCHEMA_PATH)
        logger.info("Initializing schema from '%s'", path)
        with open(path, "r", encoding="utf-8") as fh:
            sql = fh.read()

        # Execute statement by statement so errors are easier to pinpoint
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            logger.debug("Executing DDL: %s...", stmt[:60])
            self._conn.execute(stmt)

        logger.info("Schema initialized successfully")

    # ------------------------------------------------------------------
    # DML helpers
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: list | tuple | None = None) -> duckdb.DuckDBPyRelation:
        """
        Execute a single SQL statement.

        Args:
            sql: SQL statement
            params: Optional positional parameters (use ? placeholders)

        Returns:
            DuckDB relation object
        """
        if params is not None:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def executemany(self, sql: str, rows: list[list | tuple]) -> None:
        """
        Execute a parameterized statement for each row in *rows*.

        Args:
            sql: SQL statement with ? placeholders
            rows: List of parameter lists/tuples
        """
        if not rows:
            logger.debug("executemany called with 0 rows — skipping")
            return
        self._conn.executemany(sql, rows)

    def query(self, sql: str, params: list | tuple | None = None) -> pd.DataFrame:
        """
        Execute a SELECT and return the result as a DataFrame.

        Args:
            sql: SELECT statement
            params: Optional positional parameters

        Returns:
            pandas DataFrame (empty if no rows)
        """
        if params is not None:
            rel = self._conn.execute(sql, params)
        else:
            rel = self._conn.execute(sql)
        return rel.df()

    def query_one(self, sql: str, params: list | tuple | None = None) -> dict | None:
        """
        Execute a SELECT and return the first row as a dict.

        Args:
            sql: SELECT statement
            params: Optional positional parameters

        Returns:
            First row as a dict, or None if no rows
        """
        df = self.query(sql, params)
        if df.empty:
            return None
        return df.iloc[0].to_dict()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the DuckDB connection."""
        logger.info("Closing database connection")
        self._conn.close()

    # Context manager support
    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
