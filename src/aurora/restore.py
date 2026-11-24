"""Command that returns the condominium to the state in dados/."""

from __future__ import annotations

import socket
import sys

from aurora.config import API_PORT
from aurora.database import restore_initial_state

PROBE_TIMEOUT_SECONDS = 1


def is_api_responding() -> bool:
    try:
        with socket.create_connection(("localhost", API_PORT), PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def main() -> int:
    if is_api_responding():
        print(
            f"Restauração recusada: a API está respondendo na porta {API_PORT}. "
            "Pare a API com Ctrl+C e rode o comando de novo. Nada foi alterado.",
            file=sys.stderr,
        )
        return 1
    restore_initial_state()
    print("Reservas e visitantes restaurados a partir de dados/. Sessões apagadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
