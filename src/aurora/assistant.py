"""Conversation service on top of the ADK Runner and the persisted sessions."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from aurora.agents import APP_NAME, build_app
from aurora.catalog import apartment_numbers, area_fees, area_names
from aurora.config import SESSIONS_DB

logger = logging.getLogger(__name__)

USER_ID = "morador"
CONFIRMATION_CALL = "adk_request_confirmation"
PENDING_REPLY = "Há uma confirmação pendente. Responda a ela antes de fazer um novo pedido."
EMPTY_REPLY = "Não consegui concluir seu pedido. Pode reformular?"
FAILED_ACTION_REPLY = "Não foi possível concluir a ação. Nada foi alterado."


class SessionNotFound(Exception):
    pass


class UnknownApartment(Exception):
    pass


class ConfirmationNotPending(Exception):
    pass


class AssistantUnavailable(Exception):
    pass


@dataclass(frozen=True)
class PendingConfirmation:
    id: str
    action: str
    details: dict[str, Any]
    invocation_id: str
    function_call_id: str

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "acao": self.action, "detalhes": self.details}


def _public_details(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    details = dict(arguments)
    if action == "reservar_area":
        details["taxa"] = area_fees().get(arguments.get("area"))
    return details


def pending_confirmations(events: list[Event]) -> dict[str, PendingConfirmation]:
    """Rebuilds open confirmations exclusively from persisted events."""
    pending: dict[str, PendingConfirmation] = {}
    for event in events:
        for call in event.get_function_calls():
            is_system_request = call.id in (event.long_running_tool_ids or set())
            if call.name != CONFIRMATION_CALL or not call.id or not is_system_request:
                continue
            original = (call.args or {}).get("originalFunctionCall") or {}
            action = original.get("name", "")
            pending[call.id] = PendingConfirmation(
                id=call.id,
                action=action,
                details=_public_details(action, original.get("args") or {}),
                invocation_id=event.invocation_id,
                function_call_id=original.get("id", ""),
            )
        for response in event.get_function_responses():
            if response.name == CONFIRMATION_CALL and response.id:
                pending.pop(response.id, None)
    return pending


def confirmation_reply(result: dict[str, Any]) -> str:
    """Writes the answer in code, from the status of the tool result."""
    status = result.get("status")
    if status == "reservada":
        area = area_names().get(result["area"], result["area"])
        return f"Reserva confirmada: {area} em {result['data']}. Código: {result['codigo']}."
    if status == "ocupada":
        return "Essa data já está ocupada. A reserva não foi feita."
    if status == "autorizado":
        return f"Entrada de {result['nome']} autorizada para {result['data']}."
    if status == "ja_autorizado":
        return f"A entrada de {result['nome']} já estava autorizada para {result['data']}."
    if status == "negada_pelo_morador":
        return "Pedido negado. Nada foi alterado."
    return FAILED_ACTION_REPLY


class Assistant:
    def __init__(self) -> None:
        self.session_service = SqliteSessionService(db_path=str(SESSIONS_DB))
        self.runner = Runner(app=build_app(), session_service=self.session_service)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def close(self) -> None:
        await self.runner.close()

    async def _load(self, session_id: str) -> Session:
        session = await self.session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        if session is None:
            raise SessionNotFound
        return session

    async def _run(
        self, session_id: str, message: types.Content, invocation_id: str | None = None
    ) -> str:
        """Runs one turn and returns its last non-empty final text."""
        resume = {"invocation_id": invocation_id} if invocation_id else {}
        final_text = ""
        try:
            async with aclosing(
                self.runner.run_async(
                    user_id=USER_ID, session_id=session_id, new_message=message, **resume
                )
            ) as stream:
                async for event in stream:
                    if event.author == "user" or not event.is_final_response():
                        continue
                    parts = event.content.parts if event.content else None
                    if not parts:
                        continue
                    text = "".join(part.text for part in parts if part.text and not part.thought)
                    if text:
                        final_text = text
        except Exception as error:  # noqa: BLE001 - details must not reach the client.
            logger.error("Assistant turn failed: %s", type(error).__name__)
            raise AssistantUnavailable from None
        return final_text

    async def create_session(self, apartment: str) -> str:
        if apartment not in apartment_numbers():
            raise UnknownApartment
        session = await self.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, state={"apartamento": apartment}
        )
        return session.id

    async def send_message(self, session_id: str, text: str) -> dict[str, Any]:
        await self._load(session_id)
        async with self._locks[session_id]:
            session = await self._load(session_id)
            pending = pending_confirmations(session.events)
            if pending:
                return {
                    "resposta": PENDING_REPLY,
                    "confirmacoes_pendentes": [item.public() for item in pending.values()],
                }
            message = types.Content(role="user", parts=[types.Part(text=text)])
            reply = await self._run(session_id, message)
            session = await self._load(session_id)
            pending = pending_confirmations(session.events)
            if not reply and not pending:
                reply = EMPTY_REPLY
            return {
                "resposta": reply,
                "confirmacoes_pendentes": [item.public() for item in pending.values()],
            }

    async def answer_confirmation(
        self, session_id: str, confirmation_id: str, approved: bool
    ) -> dict[str, Any]:
        await self._load(session_id)
        async with self._locks[session_id]:
            session = await self._load(session_id)
            confirmation = pending_confirmations(session.events).get(confirmation_id)
            if confirmation is None:
                raise ConfirmationNotPending
            events_before = len(session.events)
            answer = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=confirmation.id,
                            name=CONFIRMATION_CALL,
                            response={"confirmed": approved},
                        )
                    )
                ],
            )
            await self._run(session_id, answer, confirmation.invocation_id)
            session = await self._load(session_id)
            result = self._action_result(
                session.events[events_before:], confirmation.function_call_id
            )
            if result is None:
                logger.error("Confirmation answer produced no tool result")
                raise AssistantUnavailable
            return {
                "resposta": confirmation_reply(result),
                "confirmacoes_pendentes": [
                    item.public() for item in pending_confirmations(session.events).values()
                ],
            }

    @staticmethod
    def _action_result(events: list[Event], function_call_id: str) -> dict[str, Any] | None:
        for event in reversed(events):
            if event.author == "user":
                continue
            for response in event.get_function_responses():
                if response.id == function_call_id and isinstance(response.response, dict):
                    return response.response
        return None

    async def list_events(self, session_id: str) -> list[dict[str, Any]]:
        session = await self._load(session_id)
        return [
            event.model_dump(mode="json", by_alias=True, exclude_none=True)
            for event in session.events
        ]
