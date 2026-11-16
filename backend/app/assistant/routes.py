import asyncio
import logging
from typing import Annotated

from ag_ui.core import (
    EventType,
    RunAgentInput,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.assistant.nodes import STREAM_TEXT_EVENT
from app.assistant.schemas import ChatRequest, ChatResponse
from app.assistant.service import AssistantService
from app.auth import Auth, AuthContext
from app.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant")

# Allowlist of AG-UI event types forwarded to the client on the SSE transport when the stream
# is filtered (every non-local/dev stage — see `chat_stream_route`). The adapter also emits
# MESSAGES_SNAPSHOT, RAW, REASONING_*, TOOL_CALL_* and more, which the frontend never consumes
# and which leak internal reconnaissance data (the Bedrock model id, judge reasoning); those are
# dropped by omission. STATE_SNAPSHOT is allowed but sanitized (see `_sanitize_state_snapshot`).
# Module-level (no class here to hang it on) — mirrors `_format_log` in `service.py`.
_FORWARDED_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.RUN_STARTED,
        EventType.RUN_FINISHED,
        EventType.RUN_ERROR,
        EventType.STEP_STARTED,
        EventType.STEP_FINISHED,
        EventType.TEXT_MESSAGE_START,
        EventType.TEXT_MESSAGE_CONTENT,
        EventType.TEXT_MESSAGE_END,
        EventType.STATE_SNAPSHOT,
    }
)


def _sanitize_state_snapshot(event: StateSnapshotEvent) -> StateSnapshotEvent:
    """Strip the STATE_SNAPSHOT down to the only fields the client consumes.

    The adapter ships the whole `AssistantState` in `snapshot`, with `messages` as raw LangChain
    objects that serialise their `response_metadata` (the Bedrock model id) — plus `route`,
    `rephrased_question`, `judge_verdicts`, the retrieved chunks and the token counters. The
    frontend reads only `references` and the id of the last message (to correlate feedback), so
    rebuild the snapshot with just those and drop every internal/reconnaissance field.
    """
    snapshot = event.snapshot or {}
    messages = snapshot.get("messages") or []
    last_id = getattr(messages[-1], "id", None) if messages else None
    return StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT,
        snapshot={
            "references": snapshot.get("references", []),
            "messages": [{"id": last_id}] if last_id is not None else [],
        },
    )


@router.post("/chat", response_model=ChatResponse)
@inject
async def chat_route(
    request: ChatRequest,
    service: FromDishka[AssistantService],
    auth: Annotated[AuthContext, Depends(Auth())],
) -> ChatResponse:
    return await service.chat(request, auth.email)


@router.post("/chat/stream")
@inject
async def chat_stream_route(
    input_data: RunAgentInput,
    request: Request,
    service: FromDishka[AssistantService],
    settings: FromDishka[Settings],
    auth: Annotated[AuthContext, Depends(Auth())],
) -> StreamingResponse:
    """Streaming (SSE) transport over the same compiled graph, in the AG-UI protocol.

    `AssistantService.stream` wraps the shared graph in the AG-UI adapter and yields its events
    (step start/finish per node, text deltas, state snapshot, run start/finish); `EventEncoder`
    serialises them as Server-Sent Events. The trusted `auth.email` (forwarded header, never the
    client payload) is passed to the service so it reaches the run config and the graph's
    finalize node logs it, exactly like the JSON transport.

    Outside `local`/`dev` the raw AG-UI stream is filtered before it reaches the client: it
    otherwise leaks reconnaissance data (the Bedrock model id, internal routing, judge reasoning,
    the retrieved chunks). In filtered mode only `_FORWARDED_EVENT_TYPES` pass and STATE_SNAPSHOT
    is reduced by `_sanitize_state_snapshot`; in `local`/`dev` the full stream is kept for debug.
    """
    encoder = EventEncoder(accept=request.headers.get("accept"))
    full_stream = settings.stage in {"local", "dev"}
    # Typewriter cadence for pre-computed answers (see below). Local to the route: single-use
    # transport knobs, not shared/exported, so they stay out of module scope.
    typewriter_delay_s = 0.05  # ~50 ms between chunks
    typewriter_chunk = 8  # ~8 chars per delta (groups of tokens)

    async def event_generator():
        async for event in service.stream(input_data, auth.email):
            etype = getattr(event, "type", None)
            # Deterministic typewriter for pre-computed answers (chit-chat / out-of-scope):
            # the `stream_answer` node signalled the text via a custom event because it never
            # goes through a chat-model stream (structured output / static text). Expand that
            # signal here into AG-UI text deltas — sharing the message id the node wrote into
            # `messages` so the final MESSAGES_SNAPSHOT reconciles instead of duplicating —
            # and drop the raw CUSTOM event (the client never sees it). Runs in both modes.
            if etype == EventType.CUSTOM and event.name == STREAM_TEXT_EVENT:
                mid = event.value["message_id"]
                text = event.value["message"]
                yield encoder.encode(
                    TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        role="assistant",
                        message_id=mid,
                    )
                )
                for i in range(0, len(text), typewriter_chunk):
                    piece = text[i : i + typewriter_chunk]
                    if not piece:  # TextMessageContent enforces delta min_length=1
                        continue
                    yield encoder.encode(
                        TextMessageContentEvent(
                            type=EventType.TEXT_MESSAGE_CONTENT,
                            message_id=mid,
                            delta=piece,
                        )
                    )
                    await asyncio.sleep(typewriter_delay_s)
                yield encoder.encode(
                    TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=mid)
                )
                continue
            # Filtered mode (non local/dev): default-deny by event type, and ship a sanitized
            # STATE_SNAPSHOT that carries only what the client reads (references + last id).
            if not full_stream:
                if etype not in _FORWARDED_EVENT_TYPES:
                    continue
                if etype == EventType.STATE_SNAPSHOT:
                    event = _sanitize_state_snapshot(event)
            yield encoder.encode(event)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
