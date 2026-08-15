"""Conversation endpoints.

The routes are thin on purpose. Everything that decides anything — which model,
whether the tenant may proceed, when to stop — happens below this layer, and
these functions only translate between HTTP and those decisions.

Two things the reference implementation got wrong are fixed structurally rather
than by remembering: ownership is enforced by the repository rather than by
each handler calling a guard, and the response body cannot begin before the
checks that would otherwise have to become error events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from kairos.api.stream.bridge import RecordingStream, StreamBridge, run_and_stream
from kairos.api.stream.sse import SSE_HEADERS, ResumePoint, encode, replay
from kairos.core.reasoning.engine import ReasoningEngine
from kairos.core.reasoning.turn import Budget
from kairos.core.streaming.events import EventStream, Transcript
from kairos.core.tenancy.context import current_scope

router = APIRouter(prefix="/threads", tags=["threads"])


class MessageRequest(BaseModel):
    """A question, and how the caller wants it answered."""

    prompt: Annotated[str, Field(min_length=1, max_length=100_000)]
    model: str | None = None
    max_iterations: Annotated[int, Field(ge=1, le=50)] = 12


class ThreadSummary(BaseModel):
    id: UUID
    title: str | None
    workspace_id: UUID
    updated_at: str


class ThreadPage(BaseModel):
    threads: list[ThreadSummary]
    limit: int
    offset: int


def get_engine() -> ReasoningEngine:
    """Supplied by the composition root at wiring time."""
    raise NotImplementedError("engine dependency is not wired")


def get_repositories():  # type: ignore[no-untyped-def]
    raise NotImplementedError("repository dependency is not wired")


@router.get("", response_model=ThreadPage)
async def list_threads(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> ThreadPage:
    """Threads belonging to the caller.

    No ownership check here: the repository reads the ambient scope, so a
    handler that forgot one would return nothing rather than everything.
    """
    threads = await repositories.threads.list_own(limit=limit, offset=offset)
    return ThreadPage(
        threads=[
            ThreadSummary(
                id=t.id,
                title=t.title,
                workspace_id=t.workspace_id,
                updated_at=t.updated_at.isoformat(),
            )
            for t in threads
        ],
        limit=limit,
        offset=offset,
    )


@router.get("/{thread_id}", response_model=ThreadSummary)
async def get_thread(
    thread_id: UUID,
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> ThreadSummary:
    thread = await repositories.threads.get(thread_id)
    if thread is None:
        # Deliberately the same response whether it does not exist or belongs
        # to someone else. Distinguishing them tells a prober which ids are
        # real.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    return ThreadSummary(
        id=thread.id,
        title=thread.title,
        workspace_id=thread.workspace_id,
        updated_at=thread.updated_at.isoformat(),
    )


@router.post("/{thread_id}/messages")
async def send_message(
    thread_id: UUID,
    request: MessageRequest,
    engine: ReasoningEngine = Depends(get_engine),
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> StreamingResponse:
    """Ask a question and stream the answer.

    Every check that can refuse the request happens before the response body
    starts. Once streaming begins the status line is already sent, so a
    refusal raised later can only become an error event inside a 200 — which a
    client cannot distinguish from a turn that genuinely failed.
    """
    thread = await repositories.threads.get(thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

    scope = current_scope()
    run_id = uuid4()

    stream = RecordingStream(run_id)
    bridge = StreamBridge(stream)
    stream.attach(bridge)
    stream.open(thread_id=str(thread_id), model=request.model or "")

    async def produce() -> AsyncIterator[str]:
        # Producing and consuming overlap: the turn runs as its own task and
        # frames leave as they are emitted. Awaiting the turn first and
        # rendering afterwards would show nothing for the length of the turn
        # and then everything, which is what the protocol exists to avoid.
        turn = engine.run(
            request.prompt,
            tenant=scope.tenant_id,
            tools=repositories.tools,
            stream=stream,
            budget=Budget(max_iterations=request.max_iterations),
        )
        async for event in run_and_stream(bridge, turn):
            yield encode(event)

    return StreamingResponse(
        produce(),
        media_type="text/event-stream",
        headers={
            **SSE_HEADERS,
            # Where to reconnect. A client that drops mid-turn cannot learn
            # this from a stream it is no longer receiving.
            "Content-Location": f"/api/v1/threads/{thread_id}/messages/stream?run_id={run_id}",
        },
    )


@router.get("/{thread_id}/messages/replay")
async def replay_thread(
    thread_id: UUID,
    last_event_id: Annotated[int | None, Query(ge=0)] = None,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> StreamingResponse:
    """Re-send a finished turn.

    The cursor is read from the query first and the header second, because a
    client reading an authenticated stream must use fetch — and fetch does not
    send the header the standard specifies.
    """
    thread = await repositories.threads.get(thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

    transcript = await repositories.transcripts.for_thread(thread_id)
    if transcript is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No transcript for this thread")

    resume = ResumePoint.from_request(
        query_value=str(last_event_id) if last_event_id is not None else None,
        header_value=last_event_id_header,
    )

    # Rendered eagerly so that a transcript violating the ordering contract
    # fails as a 500 here rather than as a malformed stream the client would
    # render as a broken conversation.
    body = replay(transcript, resume)

    async def send() -> AsyncIterator[str]:
        yield body

    return StreamingResponse(
        send(),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Replay-Source": "transcript"},
    )


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: UUID,
    repositories=Depends(get_repositories),  # type: ignore[no-untyped-def]
) -> Response:
    """Delete a thread.

    Expressed as a scoped delete rather than fetch-then-delete, so the
    ownership predicate cannot be lost between the two statements.

    Returns a bare response rather than annotating `-> None`: the framework
    infers a response model from the return annotation, and a model on a 204
    is a contradiction it refuses at import.
    """
    await repositories.threads.remove(thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def build_transcript(stream: EventStream) -> Transcript:
    """Freeze a live stream for storage."""
    transcript = Transcript.of(stream)
    transcript.validate()
    return transcript
