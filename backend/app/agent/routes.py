from typing import Annotated

from ag_ui.core import EventType, RunAgentInput, StateSnapshotEvent
from ag_ui.encoder import EventEncoder
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agent.agent import AgentService
from app.assistant.schemas import ChatRequest, ChatResponse
from app.auth import Auth, AuthContext

router = APIRouter(tags=["agent"])

_DROP_EVENT_TYPES = frozenset({EventType.MESSAGES_SNAPSHOT, EventType.RAW})


def _sanitize_agent_snapshot(event: StateSnapshotEvent) -> StateSnapshotEvent:
    # AgentState only has `messages` (full LangChain objects); the frontend expects
    # `{references: [], messages: [{id}]}`. Rebuild to match the contract so that
    # snapshot.references.map(...) on the client side doesn't throw a TypeError.
    snapshot = event.snapshot or {}
    messages = snapshot.get("messages") or []
    last_id = getattr(messages[-1], "id", None) if messages else None
    return StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT,
        snapshot={
            "references": [],
            "messages": [{"id": last_id}] if last_id is not None else [],
        },
    )


@router.post("/agent/chat", response_model=ChatResponse)
@inject
async def agent_chat_route(
    request: ChatRequest,
    service: FromDishka[AgentService],
    auth: Annotated[AuthContext, Depends(Auth())],
) -> ChatResponse:
    return await service.chat(request, auth.email)


@router.post("/agent/chat/stream")
@inject
async def agent_chat_stream_route(
    input_data: RunAgentInput,
    request: Request,
    service: FromDishka[AgentService],
    auth: Annotated[AuthContext, Depends(Auth())],
) -> StreamingResponse:
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_generator():
        async for event in service.stream(input_data, auth.email):
            etype = getattr(event, "type", None)
            if etype in _DROP_EVENT_TYPES:
                continue
            if etype == EventType.STATE_SNAPSHOT:
                event = _sanitize_agent_snapshot(event)
            yield encoder.encode(event)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
