"""SQLite schema, first-run seed and restoration of the condominium state."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from aurora.catalog import load_json
from aurora.config import DOMAIN_DB, SESSIONS_DB, STATE_DIR

BUSY_TIMEOUT_SECONDS = 30
SEEDED_KEY = "seeded"

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS reservations (
      code TEXT PRIMARY KEY,
      apartment TEXT NOT NULL,
      area TEXT NOT NULL,
      date TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
      function_call_id TEXT UNIQUE,
      cancel_function_call_id TEXT UNIQUE
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_reservations_active_area_date
    ON reservations (area, date) WHERE status = 'active'
    """,
    """
    CREATE TABLE IF NOT EXISTS visitors (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      apartment TEXT NOT NULL,
      name TEXT NOT NULL,
      date TEXT NOT NULL,
      function_call_id TEXT UNIQUE,
      UNIQUE (apartment, name, date)
    )
    """,
    # Survives restoration, so a generated code is never issued twice.
    "CREATE TABLE IF NOT EXISTS issued_codes (code TEXT PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DOMAIN_DB, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def write_transaction() -> Iterator[sqlite3.Connection]:
    """Opens a transaction that holds the SQLite write lock from its first statement."""
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _insert_initial_rows(connection: sqlite3.Connection) -> None:
    for reservation in load_json("reservas.json"):
        connection.execute(
            "INSERT INTO reservations (code, apartment, area, date) VALUES (?, ?, ?, ?)",
            (
                reservation["codigo"],
                reservation["apartamento"],
                reservation["area"],
                reservation["data"],
            ),
        )
        connection.execute(
            "INSERT INTO issued_codes (code) VALUES (?) ON CONFLICT DO NOTHING",
            (reservation["codigo"],),
        )
    for visitor in load_json("visitantes.json"):
        connection.execute(
            "INSERT INTO visitors (apartment, name, date) VALUES (?, ?, ?)",
            (visitor["apartamento"], visitor["nome"], visitor["data"]),
        )


def initialize_database() -> None:
    """Creates the schema and seeds dados/ only once in the database lifetime."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    connection = connect()
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    finally:
        connection.close()
    with write_transaction() as transaction:
        for statement in SCHEMA:
            transaction.execute(statement)
        seeded = transaction.execute(
            "SELECT 1 FROM metadata WHERE key = ?", (SEEDED_KEY,)
        ).fetchone()
        if seeded is None:
            _insert_initial_rows(transaction)
            transaction.execute(
                "INSERT INTO metadata (key, value) VALUES (?, '1')", (SEEDED_KEY,)
            )


def restore_initial_state() -> None:
    """Returns reservations and visitors to dados/ and then removes all sessions."""
    initialize_database()
    with write_transaction() as transaction:
        transaction.execute("DELETE FROM reservations")
        transaction.execute("DELETE FROM visitors")
        _insert_initial_rows(transaction)
    for suffix in ("", "-wal", "-shm", "-journal"):
        SESSIONS_DB.with_name(SESSIONS_DB.name + suffix).unlink(missing_ok=True)
