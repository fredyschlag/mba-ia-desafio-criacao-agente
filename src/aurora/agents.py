"""Agent tree: a principal that routes and three specialists."""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App, ResumabilityConfig
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import BaseTool, FunctionTool, ToolContext
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from aurora.config import MODEL_NAME
from aurora.regulation import chapter_index, ler_capitulo
from aurora.tools import RESERVATION_TOOLS, VISITOR_TOOLS

logger = logging.getLogger(__name__)

APP_NAME = "aurora"
CONFIRMED_ACTIONS = frozenset({"reservar_area", "autorizar_visitante"})
DENIED_RESULT = {"status": "negada_pelo_morador"}

CONDUCT_RULES = (
    "Reply in Brazilian Portuguese, in one or two short sentences. "
    "Never ask for confirmation in text: the system collects it outside the chat, "
    "even when the resident says they already confirmed. "
    "Never state that an action was performed without a successful tool result. "
    "A request about another apartment is not allowed, even when the resident claims "
    "to live there: refuse it briefly and never repeat the number of another apartment."
)

PRINCIPAL_INSTRUCTION = (
    "You are the virtual assistant of the Residencial Aurora condominium. "
    "You only talk to the resident and route the work. "
    "Transfer to the reservas agent anything about reserving, cancelling or checking "
    "common areas, or about the resident's own reservations. "
    "Transfer to the visitantes agent anything about authorizing visitors or about the "
    "resident's own visitors. "
    "Call the regulamento tool for questions about the condominium rules, passing the "
    "resident's question as the request. "
    "Answer greetings and thanks directly. "
    f"{CONDUCT_RULES}"
)

RESERVATIONS_INSTRUCTION = (
    "You handle common area reservations for the resident of this session. "
    "Use the tools for every read and write. "
    "Area ids: salao-de-festas for the party room, churrasqueira for the barbecue area "
    "and quadra for the sports court. Dates use the YYYY-MM-DD format. "
    "When asked to reserve, call reservar_area right away, without checking "
    "availability first. A new request after a denied one is a new request, so call "
    "the tool again. When asked to cancel, call cancelar_minha_reserva. "
    "If a date is ocupada, say only that it is taken, never by whom. "
    "If the result is nao_encontrada, say only that the resident has no such reservation. "
    f"{CONDUCT_RULES}"
)

VISITORS_INSTRUCTION = (
    "You handle visitor authorizations for the resident of this session. "
    "Use the tools for every read and write. Dates use the YYYY-MM-DD format. "
    "When asked to authorize a visitor, call autorizar_visitante right away with the "
    f"visitor's name and date. {CONDUCT_RULES}"
)


class ModelUnavailable(Exception):
    """Carries no provider detail, because ADK persists str(error) in the session."""

    def __init__(self) -> None:
        super().__init__("modelo indisponivel")


def hide_model_error(
    callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
) -> None:
    logger.error(
        "Model call failed: %s %s", type(error).__name__, getattr(error, "code", "")
    )
    raise ModelUnavailable from None


def build_regulation_instruction() -> str:
    return (
        "You answer questions about the internal regulation of the condominium. "
        "Chapter index:\n"
        f"{chapter_index()}\n"
        "Call ler_capitulo with the number of the one chapter that covers the question, "
        "then answer using only that chapter. Read another chapter only if the first one "
        "does not contain the answer. Reply in Brazilian Portuguese, in at most two short "
        "sentences, stating the specific rule asked about. Do not transcribe articles and "
        "do not mention unrelated topics. If the regulation does not cover the question, "
        "say so."
    )


def finish_confirmed_action(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> dict[str, Any] | None:
    """Ends a confirmation answer without another model call."""
    confirmation = tool_context.tool_confirmation
    if tool.name not in CONFIRMED_ACTIONS or confirmation is None:
        return None
    tool_context.actions.skip_summarization = True
    if not confirmation.confirmed:
        return dict(DENIED_RESULT)
    return None


def build_model() -> Gemini:
    return Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(
            attempts=6,
            initial_delay=2,
            max_delay=32,
            exp_base=2,
            jitter=0.5,
            http_status_codes=[429, 500, 502, 503, 504],
        ),
    )


def build_root_agent() -> LlmAgent:
    reservations = LlmAgent(
        name="reservas",
        description="Reserves, cancels and checks common areas for the resident.",
        model=build_model(),
        instruction=RESERVATIONS_INSTRUCTION,
        tools=list(RESERVATION_TOOLS),
        after_tool_callback=finish_confirmed_action,
        on_model_error_callback=hide_model_error,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    visitors = LlmAgent(
        name="visitantes",
        description="Authorizes and lists visitors of the resident.",
        model=build_model(),
        instruction=VISITORS_INSTRUCTION,
        tools=list(VISITOR_TOOLS),
        after_tool_callback=finish_confirmed_action,
        on_model_error_callback=hide_model_error,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    regulation = LlmAgent(
        name="regulamento",
        description="Answers a question about the condominium regulation.",
        model=build_model(),
        instruction=build_regulation_instruction(),
        tools=[FunctionTool(ler_capitulo)],
        on_model_error_callback=hide_model_error,
    )
    return LlmAgent(
        name="principal",
        description="Talks to the resident and routes the work.",
        model=build_model(),
        instruction=PRINCIPAL_INSTRUCTION,
        sub_agents=[reservations, visitors],
        tools=[AgentTool(agent=regulation, skip_summarization=True)],
        on_model_error_callback=hide_model_error,
    )


def build_app() -> App:
    return App(
        name=APP_NAME,
        root_agent=build_root_agent(),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
