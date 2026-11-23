"""Filesystem locations and network settings."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "dados"
STATE_DIR = PROJECT_ROOT / ".aurora"
DOMAIN_DB = STATE_DIR / "condominio.sqlite"
SESSIONS_DB = STATE_DIR / "sessoes.sqlite"

API_HOST = "127.0.0.1"
API_PORT = 8000
