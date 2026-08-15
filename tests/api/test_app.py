"""The assembled application, driven over HTTP.

These exercise the whole stack in one piece: a request arrives, the middleware
establishes a scope, the route resolves its dependencies, the engine runs a
turn, and frames come back down the same connection. Everything that could be
faked separately is faked separately elsewhere; the point here is that the
seams line up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from kairos.api.http.identity import StaticVerifier, VerifiedClaims
from kairos.core.catalog import (
    Capability,
    Catalog,
    Endpoint,
    ModelDescriptor,
    ModelId,
    ProviderDescriptor,
    ProviderId,
    TokenBudget,
    Wire,
)
from kairos.core.reasoning.engine import ModelReply, ReasoningEngine
from kairos.core.reasoning.turn import ToolOutcome, ToolRequest
from kairos.core.tools import ToolRegistry
from kairos.runtime.app import create_app, dependency_overrides_for
from kairos.runtime.settings import AuthSettings, Deployment, Settings

CATALOG = Catalog(
    providers=[
        ProviderDescriptor(
            id=ProviderId("vendor"),
            display_name="Vendor",
            endpoint=Endpoint(wire=Wire.OPENAI_CHAT, credential_env="KEY"),
        )
    ],
    models=[
        ModelDescriptor(
            id=ModelId("primary"),
            remote_id="primary",
            provider=ProviderId("vendor"),
            budget=TokenBudget(context=100_000, max_output=4_000),
            capabilities=Capability.baseline(),
        )
    ],
)


@dataclass
class FakeThread:
    id: UUID
    title: str | None = "A thread"
    workspace_id: UUID = field(default_factory=uuid4)
    updated_at: Any = None

    def __post_init__(self) -> None:
        from datetime import UTC, datetime

        self.updated_at = self.updated_at or datetime.now(UTC)


class FakeThreadRepo:
    def __init__(self, threads: dict[UUID, FakeThread] | None = None) -> None:
        self.threads = threads or {}
        self.removed: list[UUID] = []

    async def get(self, thread_id: UUID) -> FakeThread | None:
        return self.threads.get(thread_id)

    async def list_own(self, *, limit: int = 50, offset: int = 0) -> list[FakeThread]:
        return list(self.threads.values())[offset : offset + limit]

    async def remove(self, thread_id: UUID) -> None:
        self.removed.append(thread_id)


class FakeTools:
    async def run(self, request: ToolRequest) -> ToolOutcome:
        return ToolOutcome(call_id=request.call_id, ok=True, summary="ok")


class FakeTranscripts:
    def __init__(self) -> None:
        self.stored: dict[UUID, Any] = {}

    async def for_thread(self, thread_id: UUID) -> Any:
        return self.stored.get(thread_id)


@dataclass
class FakeRepositories:
    threads: FakeThreadRepo
    tools: FakeTools = field(default_factory=FakeTools)
    transcripts: FakeTranscripts = field(default_factory=FakeTranscripts)


class ScriptedModel:
    def __init__(self, *replies: ModelReply) -> None:
        self._replies = list(replies)

    async def call(self, messages, tools):  # type: ignore[no-untyped-def]
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


def solo_settings() -> Settings:
    return Settings(deployment=Deployment.SOLO, auth=AuthSettings())


def hosted_settings() -> Settings:
    return Settings(
        deployment=Deployment.HOSTED,
        auth=AuthSettings(
            jwks_url="https://issuer.example/jwks", issuer="https://issuer.example"
        ),
    )


def build(
    *,
    settings: Settings | None = None,
    threads: dict[UUID, FakeThread] | None = None,
    replies: tuple[ModelReply, ...] = (ModelReply(text="42"),),
    verifier: Any = None,
) -> tuple[TestClient, FakeRepositories]:
    app = create_app(
        settings=settings or solo_settings(), catalog=CATALOG, verifier=verifier
    )
    repositories = FakeRepositories(threads=FakeThreadRepo(threads))
    engine = ReasoningEngine(ScriptedModel(*replies), ToolRegistry())
    dependency_overrides_for(app, engine=engine, repositories=repositories)
    return TestClient(app), repositories


class TestHealth:
    def test_health_is_public(self) -> None:
        client, _ = build()
        assert client.get("/health").status_code == 200

    def test_health_reports_what_it_checked(self) -> None:
        """A constant tells a load balancer only that a route can be served."""
        client, _ = build()
        body = client.get("/health").json()
        assert body["models"] == 1
        assert body["pipeline_stages"] > 0


class TestDocsExposure:
    def test_solo_mode_serves_docs(self) -> None:
        client, _ = build()
        assert client.get("/openapi.json").status_code == 200

    def test_hosted_mode_does_not(self) -> None:
        """Useful while developing, an unnecessary map of the surface in production."""
        client, _ = build(
            settings=hosted_settings(),
            verifier=StaticVerifier({"t": VerifiedClaims(subject="u", tenant="acme")}),
        )
        assert client.get("/openapi.json").status_code == 404


class TestAuthentication:
    def test_solo_mode_needs_no_token(self) -> None:
        client, _ = build()
        assert client.get("/api/v1/threads").status_code == 200

    def test_hosted_mode_refuses_an_anonymous_request(self) -> None:
        client, _ = build(
            settings=hosted_settings(),
            verifier=StaticVerifier({"good": VerifiedClaims(subject="u", tenant="acme")}),
        )
        assert client.get("/api/v1/threads").status_code == 401

    def test_hosted_mode_accepts_a_valid_token(self) -> None:
        client, _ = build(
            settings=hosted_settings(),
            verifier=StaticVerifier({"good": VerifiedClaims(subject="u", tenant="acme")}),
        )
        response = client.get(
            "/api/v1/threads", headers={"Authorization": "Bearer good"}
        )
        assert response.status_code == 200

    def test_a_forged_token_is_refused(self) -> None:
        client, _ = build(
            settings=hosted_settings(),
            verifier=StaticVerifier({"good": VerifiedClaims(subject="u", tenant="acme")}),
        )
        response = client.get(
            "/api/v1/threads", headers={"Authorization": "Bearer forged"}
        )
        assert response.status_code == 401


class TestThreadRoutes:
    def test_a_missing_thread_is_not_found(self) -> None:
        client, _ = build()
        assert client.get(f"/api/v1/threads/{uuid4()}").status_code == 404

    def test_an_existing_thread_is_returned(self) -> None:
        thread_id = uuid4()
        client, _ = build(threads={thread_id: FakeThread(id=thread_id)})
        response = client.get(f"/api/v1/threads/{thread_id}")
        assert response.status_code == 200
        assert response.json()["id"] == str(thread_id)

    def test_deleting_is_scoped_not_fetch_then_delete(self) -> None:
        """The predicate cannot be lost between two statements if there is one."""
        thread_id = uuid4()
        client, repositories = build()
        assert client.delete(f"/api/v1/threads/{thread_id}").status_code == 204
        assert repositories.threads.removed == [thread_id]


class TestStreaming:
    def test_a_message_streams_an_answer(self) -> None:
        thread_id = uuid4()
        client, _ = build(threads={thread_id: FakeThread(id=thread_id)})

        response = client.post(
            f"/api/v1/threads/{thread_id}/messages", json={"prompt": "what is it"}
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert "event: metadata" in body
        assert "event: text" in body
        assert "event: done" in body

    def test_the_stream_opens_with_metadata(self) -> None:
        """A client that drops mid-turn resumes by run id.

        It cannot learn that id from a stream it is no longer receiving, so it
        must arrive first.
        """
        thread_id = uuid4()
        client, _ = build(threads={thread_id: FakeThread(id=thread_id)})
        body = client.post(
            f"/api/v1/threads/{thread_id}/messages", json={"prompt": "hi"}
        ).text
        assert body.lstrip().startswith("id: 0")

    def test_the_reconnect_url_is_advertised(self) -> None:
        thread_id = uuid4()
        client, _ = build(threads={thread_id: FakeThread(id=thread_id)})
        response = client.post(
            f"/api/v1/threads/{thread_id}/messages", json={"prompt": "hi"}
        )
        assert "run_id=" in response.headers["content-location"]

    def test_buffering_is_disabled(self) -> None:
        thread_id = uuid4()
        client, _ = build(threads={thread_id: FakeThread(id=thread_id)})
        response = client.post(
            f"/api/v1/threads/{thread_id}/messages", json={"prompt": "hi"}
        )
        assert response.headers["x-accel-buffering"] == "no"

    def test_a_refusal_arrives_as_a_status_not_an_event(self) -> None:
        """Checks that can refuse must run before the body starts.

        After that the status line is already sent, and a refusal can only be
        an error event inside a 200 — indistinguishable from a turn that
        genuinely failed.
        """
        client, _ = build()
        response = client.post(
            f"/api/v1/threads/{uuid4()}/messages", json={"prompt": "hi"}
        )
        assert response.status_code == 404

    def test_a_tool_using_turn_streams_its_calls(self) -> None:
        thread_id = uuid4()
        registry = ToolRegistry()
        from kairos.core.tools import Exposure, ToolDefinition

        registry.register(
            ToolDefinition(name="search", description="s", exposure=Exposure.DIRECT)
        )

        app = create_app(settings=solo_settings(), catalog=CATALOG)
        repositories = FakeRepositories(
            threads=FakeThreadRepo({thread_id: FakeThread(id=thread_id)})
        )
        engine = ReasoningEngine(
            ScriptedModel(
                ModelReply(
                    tool_requests=(
                        ToolRequest(call_id="c1", name="search", arguments={}),
                    )
                ),
                ModelReply(text="found"),
            ),
            registry,
        )
        dependency_overrides_for(app, engine=engine, repositories=repositories)

        body = TestClient(app).post(
            f"/api/v1/threads/{thread_id}/messages", json={"prompt": "find it"}
        ).text

        assert "event: tool_call" in body
        assert "event: tool_result" in body


class TestValidation:
    def test_an_empty_prompt_is_refused(self) -> None:
        thread_id = uuid4()
        client, _ = build(threads={thread_id: FakeThread(id=thread_id)})
        response = client.post(
            f"/api/v1/threads/{thread_id}/messages", json={"prompt": ""}
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("iterations", [0, 51])
    def test_an_out_of_range_budget_is_refused(self, iterations: int) -> None:
        thread_id = uuid4()
        client, _ = build(threads={thread_id: FakeThread(id=thread_id)})
        response = client.post(
            f"/api/v1/threads/{thread_id}/messages",
            json={"prompt": "hi", "max_iterations": iterations},
        )
        assert response.status_code == 422
