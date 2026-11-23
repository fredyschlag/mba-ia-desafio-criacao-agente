"""HTTP API. Verification routes read the condominium data without using a model."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from aurora import repository
from aurora.config import API_HOST, API_PORT
from aurora.database import initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


@app.get("/apartamentos/{numero}/reservas")
def list_apartment_reservations(numero: str) -> list[dict[str, str]]:
    return repository.list_reservations(numero)


@app.get("/apartamentos/{numero}/visitantes")
def list_apartment_visitors(numero: str) -> list[dict[str, str]]:
    return repository.list_visitors(numero)


def main() -> None:
    uvicorn.run("aurora.api:app", host=API_HOST, port=API_PORT, workers=1)


if __name__ == "__main__":
    main()
