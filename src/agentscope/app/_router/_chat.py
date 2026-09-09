# -*- coding: utf-8 -*-
"""Chat router — fire-and-forget trigger for chat runs.

The endpoint no longer returns an SSE stream. Instead, it kicks off a
chat run as a background task and returns immediately. Events produced
by the run are published to the message bus and delivered to the
frontend via the long-lived ``GET /sessions/{sid}/stream`` SSE
connection provided by the session router.

Two trigger paths, deliberately asymmetric:

- **New user message(s)** are spawned directly into the
  :class:`ChatRunRegistry`. The registry's single-run-per-session rule
  surfaces as a 409, which is exactly the desired double-submit guard.
- **HITL results** (``UserConfirmResultEvent`` /
  ``ExternalExecutionResultEvent``) are *enqueued* onto the shared
  run-trigger queue and drained by the single
  :class:`WakeupDispatcher`. Routing the resume through the queue keeps
  the dispatcher the sole spawn site, so a resume can never collide with
  the worker's still-finishing parked run (the old 409 race) — the
  dispatcher serialises them.
"""
import asyncio
import mimetypes
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from ..deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_knowledge_parsers,
    get_message_bus,
)
from ._schema import (
    ChatRequest,
    ChatTriggerResponse,
    ListChatAttachmentContentTypesResponse,
    ParseChatAttachmentResponse,
)
from .._manager import ChatRunRegistry
from .._service import (
    ChatService,
    SessionProjection,
    SubagentHitlProjector,
)
from ..message_bus import MessageBus, MessageBusKeys
from .._bus_ops import enqueue_run_trigger
from ...event import UserConfirmResultEvent, ExternalExecutionResultEvent
from ..._logging import logger
from ...message import DataBlock, TextBlock
from ...rag import ParserBase, Section

chat_router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    responses={404: {"description": "Not found"}},
)


def _is_text_extractable_media_type(media_type: str) -> bool:
    """Return whether a parser's output can reasonably contain text."""
    return not media_type.startswith(("image/", "audio/", "video/"))


def _build_attachment_parser_registry(
    parsers: list[ParserBase] | dict[str, ParserBase],
) -> tuple[
    dict[str, ParserBase],
    dict[str, tuple[ParserBase, str]],
]:
    """Build media-type and extension routes for text extraction."""
    media_routes: dict[str, ParserBase] = {}
    parser_list: list[ParserBase]
    if isinstance(parsers, dict):
        for media_type, parser in parsers.items():
            normalized = media_type.lower()
            if _is_text_extractable_media_type(normalized):
                media_routes[normalized] = parser
        parser_list = list(dict.fromkeys(parsers.values()))
    else:
        parser_list = parsers
        for parser in parsers:
            for media_type in parser.supported_media_types:
                normalized = media_type.lower()
                if _is_text_extractable_media_type(normalized):
                    media_routes[normalized] = parser

    extension_routes: dict[str, tuple[ParserBase, str]] = {}
    for parser in parser_list:
        parser_media_types = [
            media_type.lower()
            for media_type in parser.supported_media_types
            if _is_text_extractable_media_type(media_type.lower())
        ]
        if not parser_media_types:
            continue
        for extension in parser.supported_extensions():
            normalized_extension = extension.lower()
            guessed = mimetypes.guess_type(f"file{normalized_extension}")[0]
            resolved_media_type = (
                guessed.lower()
                if guessed and guessed.lower() in parser_media_types
                else parser_media_types[0]
            )
            extension_routes[normalized_extension] = (
                parser,
                resolved_media_type,
            )
    return media_routes, extension_routes


def _resolve_attachment_parser(
    parsers: list[ParserBase] | dict[str, ParserBase],
    filename: str,
    content_type: str | None,
) -> tuple[ParserBase, str] | None:
    """Resolve one uploaded file to a configured text parser."""
    media_routes, extension_routes = _build_attachment_parser_registry(
        parsers,
    )
    claimed_type = (content_type or "").split(";", maxsplit=1)[0].lower()
    if claimed_type in media_routes:
        return media_routes[claimed_type], claimed_type

    guessed_type = mimetypes.guess_type(filename)[0]
    if guessed_type:
        normalized_guess = guessed_type.lower()
        if normalized_guess in media_routes:
            return media_routes[normalized_guess], normalized_guess

    extension = Path(filename).suffix.lower()
    return extension_routes.get(extension)


def _run_parser(
    parser: ParserBase,
    payload: bytes,
    filename: str,
) -> list[Section]:
    """Run an async parser in a worker thread to avoid blocking ASGI."""
    return asyncio.run(parser.parse(payload, filename))


def _format_attachment_text(
    sections: list[Section],
    max_chars: int,
) -> tuple[str, int, int, bool]:
    """Flatten parsed text sections while preserving useful boundaries."""
    parts: list[str] = []
    omitted_media_count = 0
    for section in sections:
        if isinstance(section.content, DataBlock):
            omitted_media_count += 1
            continue
        if not isinstance(section.content, TextBlock):
            continue
        text = section.content.text.strip()
        if not text:
            continue
        if section.metadata:
            metadata = ", ".join(
                f"{key}: {value}"
                for key, value in section.metadata.items()
                if key != "media_type"
            )
            if metadata:
                text = f"[{metadata}]\n{text}"
        parts.append(text)

    flattened = "\n\n".join(parts)
    truncated = len(flattened) > max_chars
    if truncated:
        flattened = (
            flattened[:max_chars].rstrip()
            + "\n\n[Attachment text truncated by the server.]"
        )
    if omitted_media_count:
        flattened += (
            "\n\n["
            f"{omitted_media_count} embedded non-text section(s) omitted."
            "]"
        )
    return flattened, len(parts), omitted_media_count, truncated


@chat_router.get(
    "/attachments/supported_content_types",
    response_model=ListChatAttachmentContentTypesResponse,
    summary="List document types that can be extracted into chat text",
)
async def list_chat_attachment_content_types(
    _: str = Depends(get_current_user_id),
    parsers: list[ParserBase]
    | dict[str, ParserBase] = Depends(get_knowledge_parsers),
) -> ListChatAttachmentContentTypesResponse:
    """Advertise parser-backed document types for the chat picker."""
    media_routes, extension_routes = _build_attachment_parser_registry(
        parsers,
    )
    return ListChatAttachmentContentTypesResponse(
        media_types=sorted(media_routes),
        extensions=sorted(extension_routes),
    )


@chat_router.post(
    "/attachments/parse",
    response_model=ParseChatAttachmentResponse,
    summary="Extract one document attachment into chat-ready text",
)
async def parse_chat_attachment(
    request: Request,
    file: UploadFile = File(description="Document to extract as text."),
    _: str = Depends(get_current_user_id),
    parsers: list[ParserBase]
    | dict[str, ParserBase] = Depends(get_knowledge_parsers),
) -> ParseChatAttachmentResponse:
    """Parse a document without embedding it or creating a knowledge base."""
    filename = file.filename or "uploaded_file"
    max_bytes = request.app.state.chat_attachment_max_bytes
    max_chars = request.app.state.chat_attachment_max_chars
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Attachment exceeds the {max_bytes}-byte size limit.",
        )

    resolved = _resolve_attachment_parser(
        parsers,
        filename,
        file.content_type,
    )
    if resolved is None:
        _, extension_routes = _build_attachment_parser_registry(parsers)
        supported = ", ".join(sorted(extension_routes))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported attachment type for {filename!r}. "
                f"Supported extensions: {supported or 'none'}."
            ),
        )

    payload = await file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Attachment exceeds the {max_bytes}-byte size limit.",
        )

    parser, media_type = resolved
    try:
        sections = await asyncio.to_thread(
            _run_parser,
            parser,
            payload,
            filename,
        )
    except (ImportError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Failed to parse {filename!r}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to parse chat attachment %r", filename)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Failed to parse {filename!r}.",
        ) from exc

    text, section_count, omitted_media_count, truncated = (
        _format_attachment_text(sections, max_chars)
    )
    if not text.strip() or section_count == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No readable text was found in {filename!r}.",
        )
    return ParseChatAttachmentResponse(
        filename=filename,
        media_type=media_type,
        text=text,
        section_count=section_count,
        omitted_media_count=omitted_media_count,
        truncated=truncated,
    )


@chat_router.post(
    "/",
    response_model=ChatTriggerResponse,
    summary="Trigger a chat run (fire-and-forget)",
)
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
    chat_run_registry: ChatRunRegistry = Depends(get_chat_run_registry),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ChatTriggerResponse:
    """Trigger a chat run for the specified session.

    Events produced during the run are published to the message bus and
    delivered to any active ``GET /sessions/{session_id}/stream`` SSE
    subscriber. The caller does **not** receive events from this
    endpoint's response body.

    Accepts the same ``input`` payloads as before:

    - ``Msg`` / ``list[Msg]``: new user message(s) — spawned directly.
    - ``UserConfirmResultEvent`` / ``ExternalExecutionResultEvent``:
      resume a paused tool call (human-in-the-loop) — routed to the
      owning session and enqueued for the dispatcher.
    - ``None``: continue from current state — spawned directly.

    Args:
        request (`ChatRequest`):
            JSON body with ``agent_id``, ``session_id``, and ``input``.
        user_id (`str`):
            Injected user id.
        chat_service (`ChatService`):
            Injected application-wide chat service.
        chat_run_registry (`ChatRunRegistry`):
            Injected per-process chat-run registry.
        message_bus (`MessageBus`):
            Injected message bus, used to resolve subagent-confirm
            routing and to enqueue resume triggers.

    Returns:
        `ChatTriggerResponse`:
            Confirms the run was scheduled (for a resume, that it was
            enqueued).

    Raises:
        `HTTPException`:
            409 if a chat run for this session is already in flight in
            this process (the registry enforces single-run-per-session).
            Only direct-spawn paths (new messages / ``None``) can raise
            this; the enqueued resume path never does.
    """
    # ------------------------------------------------------------------
    # HITL resume — route to the owning session, then enqueue.
    #
    # A confirmation / external-result POSTed to a *leader* session may
    # actually belong to a team *member*: the leader is the single front
    # door clients talk to. Resolve the owning worker HERE, then enqueue
    # a ``resume`` trigger for that session. The single WakeupDispatcher
    # drains it — spawning under the *worker* session id, serialised
    # behind any still-finishing parked run, so there is no registry
    # collision (no 409) and the leader's run slot is never occupied by
    # the worker's resume.
    # ------------------------------------------------------------------
    if isinstance(
        request.input,
        (UserConfirmResultEvent, ExternalExecutionResultEvent),
    ):
        run_session_id = request.session_id
        run_agent_id = request.agent_id
        target = await SubagentHitlProjector.resolve(
            SessionProjection(message_bus),
            request.session_id,
            request.input.reply_id,
        )
        if target is not None:
            run_session_id = target["worker_session_id"]
            run_agent_id = target["worker_agent_id"]

        await enqueue_run_trigger(
            message_bus,
            user_id=user_id,
            session_id=run_session_id,
            agent_id=run_agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=request.input,
        )
        return ChatTriggerResponse(status="started", session_id=run_session_id)

    # ------------------------------------------------------------------
    # New user message(s) / None — spawn directly. The registry's
    # single-run-per-session rule is the desired double-submit guard.
    # ------------------------------------------------------------------
    try:
        chat_run_registry.spawn(
            chat_service.run(
                user_id=user_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                input_msg=request.input,
            ),
            session_id=request.session_id,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    return ChatTriggerResponse(
        status="started",
        session_id=request.session_id,
    )
