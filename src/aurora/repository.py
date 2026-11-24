"""Reads and atomic writes of reservations and visitors."""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from aurora.database import connect, write_transaction

CODE_PREFIX = "RSV-"
# Letters only, so a generated code can never contain an apartment number.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 10

Result = dict[str, Any]


def _issue_code(connection: sqlite3.Connection) -> str:
    while True:
        code = CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        issued = connection.execute(
            "INSERT INTO issued_codes (code) VALUES (?) ON CONFLICT DO NOTHING", (code,)
        )
        if issued.rowcount == 1:
            return code


def is_date_taken(area: str, date: str) -> bool:
    connection = connect()
    try:
        row = connection.execute(
            "SELECT 1 FROM reservations WHERE area = ? AND date = ? AND status = 'active'",
            (area, date),
        ).fetchone()
    finally:
        connection.close()
    return row is not None


def list_reservations(apartment: str) -> list[dict[str, str]]:
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT code, area, date FROM reservations "
            "WHERE apartment = ? AND status = 'active' ORDER BY date, area",
            (apartment,),
        ).fetchall()
    finally:
        connection.close()
    return [{"codigo": row["code"], "area": row["area"], "data": row["date"]} for row in rows]


def list_visitors(apartment: str) -> list[dict[str, str]]:
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT name, date FROM visitors WHERE apartment = ? ORDER BY date, name",
            (apartment,),
        ).fetchall()
    finally:
        connection.close()
    return [{"nome": row["name"], "data": row["date"]} for row in rows]


def create_reservation(apartment: str, area: str, date: str, function_call_id: str) -> Result:
    """Inserts the reservation; the partial unique index decides who wins a race."""
    with write_transaction() as transaction:
        existing = transaction.execute(
            "SELECT code, apartment, area, date, status FROM reservations "
            "WHERE function_call_id = ?",
            (function_call_id,),
        ).fetchone()
        if existing is not None:
            if (existing["apartment"], existing["area"], existing["date"]) != (
                apartment,
                area,
                date,
            ):
                return {"status": "chamada_invalida"}
            return {
                "status": "reservada" if existing["status"] == "active" else "cancelada",
                "codigo": existing["code"],
                "area": area,
                "data": date,
            }
        code = _issue_code(transaction)
        inserted = transaction.execute(
            "INSERT INTO reservations (code, apartment, area, date, function_call_id) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (area, date) WHERE status = 'active' DO NOTHING",
            (code, apartment, area, date, function_call_id),
        )
        if inserted.rowcount == 0:
            transaction.execute("DELETE FROM issued_codes WHERE code = ?", (code,))
            return {"status": "ocupada"}
    return {"status": "reservada", "codigo": code, "area": area, "data": date}


def cancel_reservation(apartment: str, area: str, date: str, function_call_id: str) -> Result:
    """Marks the apartment's own active reservation as cancelled and keeps the record."""
    not_found: Result = {"status": "nao_encontrada"}
    with write_transaction() as transaction:
        replayed = transaction.execute(
            "SELECT code, apartment, area, date FROM reservations "
            "WHERE cancel_function_call_id = ?",
            (function_call_id,),
        ).fetchone()
        if replayed is not None:
            if (replayed["apartment"], replayed["area"], replayed["date"]) != (
                apartment,
                area,
                date,
            ):
                return not_found
            return {"status": "cancelada", "codigo": replayed["code"], "area": area, "data": date}
        row = transaction.execute(
            "SELECT code FROM reservations "
            "WHERE apartment = ? AND area = ? AND date = ? AND status = 'active'",
            (apartment, area, date),
        ).fetchone()
        if row is None:
            return not_found
        transaction.execute(
            "UPDATE reservations SET status = 'cancelled', cancel_function_call_id = ? "
            "WHERE code = ?",
            (function_call_id, row["code"]),
        )
    return {"status": "cancelada", "codigo": row["code"], "area": area, "data": date}


def authorize_visitor(apartment: str, name: str, date: str, function_call_id: str) -> Result:
    """Records the visitor once, even when the same call is executed again."""
    with write_transaction() as transaction:
        existing = transaction.execute(
            "SELECT apartment, name, date FROM visitors WHERE function_call_id = ?",
            (function_call_id,),
        ).fetchone()
        if existing is not None:
            if (existing["apartment"], existing["name"], existing["date"]) != (
                apartment,
                name,
                date,
            ):
                return {"status": "chamada_invalida"}
            return {"status": "autorizado", "nome": name, "data": date}
        inserted = transaction.execute(
            "INSERT INTO visitors (apartment, name, date, function_call_id) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (apartment, name, date) DO NOTHING",
            (apartment, name, date, function_call_id),
        )
        status = "autorizado" if inserted.rowcount == 1 else "ja_autorizado"
    return {"status": status, "nome": name, "data": date}
