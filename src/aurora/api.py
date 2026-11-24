"""HTTP API. Verification routes read the condominium data without using a model."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool

from aurora import repository
from aurora.assistant import (
    Assistant,
    AssistantUnavailable,
    ConfirmationNotPending,
    SessionNotFound,
    UnknownApartment,
)
from aurora.config import API_HOST, API_PORT, PROJECT_ROOT
from aurora.database import initialize_database


class CreateSessionBody(BaseModel):
    apartamento: str


class MessageBody(BaseModel):
    texto: str = Field(min_length=1)


class ConfirmationBody(BaseModel):
    id: str
    confirmado: StrictBool


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    initialize_database()
    application.state.assistant = Assistant()
    yield
    await application.state.assistant.close()


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


@app.exception_handler(SessionNotFound)
async def handle_session_not_found(_: Request, __: SessionNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "sessao nao encontrada"})


@app.exception_handler(ConfirmationNotPending)
async def handle_not_pending(_: Request, __: ConfirmationNotPending) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "confirmacao nao pendente"})


@app.exception_handler(AssistantUnavailable)
async def handle_unavailable(_: Request, __: AssistantUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "assistente indisponivel"})


def _assistant(request: Request) -> Assistant:
    return request.app.state.assistant


@app.post("/sessoes", status_code=201)
async def create_session(body: CreateSessionBody, request: Request) -> dict[str, str]:
    try:
        session_id = await _assistant(request).create_session(body.apartamento)
    except UnknownApartment:
        raise HTTPException(status_code=422, detail="apartamento inexistente") from None
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
async def send_message(session_id: str, body: MessageBody, request: Request) -> dict[str, Any]:
    return await _assistant(request).send_message(session_id, body.texto)


@app.post("/sessoes/{session_id}/confirmacoes")
async def answer_confirmation(
    session_id: str, body: ConfirmationBody, request: Request
) -> dict[str, Any]:
    return await _assistant(request).answer_confirmation(session_id, body.id, body.confirmado)


@app.get("/sessoes/{session_id}/eventos")
async def list_events(session_id: str, request: Request) -> list[dict[str, Any]]:
    return await _assistant(request).list_events(session_id)


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
