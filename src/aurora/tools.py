"""ADK tools. Every critical rule is enforced here, whatever the model decides.

No tool receives an apartment: it always comes from the session state.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date as calendar_date
from typing import Any, Literal

from google.adk.tools import FunctionTool, ToolContext

from aurora import repository
from aurora.catalog import apartment_numbers, area_fees

AreaId = Literal["salao-de-festas", "churrasqueira", "quadra"]
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

Result = dict[str, Any]


def _session_apartment(tool_context: ToolContext) -> str | None:
    apartment = tool_context.state.get("apartamento")
    if isinstance(apartment, str) and apartment in apartment_numbers():
        return apartment
    return None


def _is_valid_area(area: Any) -> bool:
    return isinstance(area, str) and area in area_fees()


def _is_valid_date(value: Any) -> bool:
    if not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None:
        return False
    try:
        calendar_date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_confirmed(tool_context: ToolContext) -> bool:
    confirmation = tool_context.tool_confirmation
    return confirmation is not None and confirmation.confirmed is True


def _area_date_refusal(area: Any, data: Any) -> Result | None:
    if not _is_valid_area(area):
        return {"status": "area_invalida", "areas_validas": sorted(area_fees())}
    if not _is_valid_date(data):
        return {"status": "data_invalida", "formato": "AAAA-MM-DD"}
    return None


def reservation_requires_confirmation(area: str, data: str, tool_context: ToolContext) -> bool:
    """Depends only on the immutable fee, because ADK evaluates it again on resume."""
    return area_fees().get(area, 0) > 0


async def consultar_disponibilidade(
    area: AreaId, data: str, tool_context: ToolContext
) -> Result:
    """Checks whether a common area is free on a date.

    Args:
        area: Exact area id.
        data: Date in YYYY-MM-DD format.

    Returns:
        A status of livre or ocupada, without any information about who reserved it.
    """
    if _session_apartment(tool_context) is None:
        return {"status": "apartamento_ausente"}
    refusal = _area_date_refusal(area, data)
    if refusal is not None:
        return refusal
    taken = await asyncio.to_thread(repository.is_date_taken, area, data)
    return {"status": "ocupada" if taken else "livre"}


async def listar_minhas_reservas(tool_context: ToolContext) -> Result:
    """Lists the active reservations of the resident's own apartment."""
    apartment = _session_apartment(tool_context)
    if apartment is None:
        return {"status": "apartamento_ausente"}
    reservations = await asyncio.to_thread(repository.list_reservations, apartment)
    return {"status": "ok", "reservas": reservations}


async def reservar_area(area: AreaId, data: str, tool_context: ToolContext) -> Result:
    """Reserves a common area for the resident's own apartment.

    Args:
        area: Exact area id.
        data: Date in YYYY-MM-DD format.

    Returns:
        A status of reservada with the new code, or ocupada when the date is taken.
    """
    apartment = _session_apartment(tool_context)
    if apartment is None:
        return {"status": "apartamento_ausente"}
    refusal = _area_date_refusal(area, data)
    if refusal is not None:
        return refusal
    if area_fees()[area] > 0 and not _is_confirmed(tool_context):
        return {"status": "confirmacao_necessaria"}
    function_call_id = tool_context.function_call_id
    if not function_call_id:
        return {"status": "chamada_invalida"}
    return await asyncio.to_thread(
        repository.create_reservation, apartment, area, data, function_call_id
    )


async def cancelar_minha_reserva(area: AreaId, data: str, tool_context: ToolContext) -> Result:
    """Cancels an active reservation of the resident's own apartment.

    Args:
        area: Exact area id.
        data: Date in YYYY-MM-DD format.

    Returns:
        A status of cancelada, or nao_encontrada when the apartment has no such reservation.
    """
    apartment = _session_apartment(tool_context)
    if apartment is None:
        return {"status": "apartamento_ausente"}
    refusal = _area_date_refusal(area, data)
    if refusal is not None:
        return refusal
    function_call_id = tool_context.function_call_id
    if not function_call_id:
        return {"status": "chamada_invalida"}
    return await asyncio.to_thread(
        repository.cancel_reservation, apartment, area, data, function_call_id
    )


async def listar_meus_visitantes(tool_context: ToolContext) -> Result:
    """Lists the visitors authorized by the resident's own apartment."""
    apartment = _session_apartment(tool_context)
    if apartment is None:
        return {"status": "apartamento_ausente"}
    visitors = await asyncio.to_thread(repository.list_visitors, apartment)
    return {"status": "ok", "visitantes": visitors}


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> Result:
    """Authorizes a visitor to enter the building for the resident's own apartment.

    Args:
        nome: Visitor full name.
        data: Date of the visit in YYYY-MM-DD format.

    Returns:
        A status of autorizado with the visitor name and date.
    """
    apartment = _session_apartment(tool_context)
    if apartment is None:
        return {"status": "apartamento_ausente"}
    name = nome.strip() if isinstance(nome, str) else ""
    if not name:
        return {"status": "nome_invalido"}
    if not _is_valid_date(data):
        return {"status": "data_invalida", "formato": "AAAA-MM-DD"}
    if not _is_confirmed(tool_context):
        return {"status": "confirmacao_necessaria"}
    function_call_id = tool_context.function_call_id
    if not function_call_id:
        return {"status": "chamada_invalida"}
    return await asyncio.to_thread(
        repository.authorize_visitor, apartment, name, data, function_call_id
    )


RESERVATION_TOOLS = [
    FunctionTool(consultar_disponibilidade),
    FunctionTool(listar_minhas_reservas),
    FunctionTool(reservar_area, require_confirmation=reservation_requires_confirmation),
    FunctionTool(cancelar_minha_reserva),
]
VISITOR_TOOLS = [
    FunctionTool(listar_meus_visitantes),
    FunctionTool(autorizar_visitante, require_confirmation=True),
]
