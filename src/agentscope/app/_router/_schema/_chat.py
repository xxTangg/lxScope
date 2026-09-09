# -*- coding: utf-8 -*-
"""The chat endpoint schema."""

from pydantic import BaseModel, Field

from ....message import Msg
from ....event import UserConfirmResultEvent, ExternalExecutionResultEvent


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    agent_id: str = Field(
        description="Agent ID for the chat endpoint.",
    )

    session_id: str = Field(
        description="The session to send the message to.",
    )

    input: (
        Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None
    ) = Field(
        description="The input message(s), or agent event, or None.",
    )


class ChatTriggerResponse(BaseModel):
    """Response body for the fire-and-forget chat trigger.

    Confirms that the chat run was scheduled. Events produced by the
    run arrive separately via the session's SSE stream endpoint.
    """

    status: str = Field(
        default="started",
        description='Always ``"started"`` when the trigger succeeded.',
    )
    session_id: str = Field(
        description="Echo of the session id the run was started for.",
    )


class ListChatAttachmentContentTypesResponse(BaseModel):
    """File types that can be converted into chat text locally."""

    media_types: list[str] = Field(
        description=(
            "Deduplicated, sorted IANA media types accepted by the "
            "configured text-extracting document parsers."
        ),
    )
    extensions: list[str] = Field(
        description=(
            "Deduplicated, sorted filename extensions accepted by the "
            "configured text-extracting document parsers."
        ),
    )


class ParseChatAttachmentResponse(BaseModel):
    """Text extracted from one chat attachment."""

    filename: str = Field(description="Original uploaded filename.")
    media_type: str = Field(
        description="Resolved IANA media type used to select the parser.",
    )
    text: str = Field(
        description="Plain text to include in the user's chat message.",
    )
    section_count: int = Field(
        description="Number of non-empty text sections extracted.",
    )
    omitted_media_count: int = Field(
        description=(
            "Number of embedded non-text sections omitted from the "
            "text-only result."
        ),
    )
    truncated: bool = Field(
        description="Whether the extracted text exceeded the configured cap.",
    )
