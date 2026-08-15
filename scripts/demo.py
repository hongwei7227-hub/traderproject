"""Run the service against a scripted model, with no external dependencies.

Proves the assembled system works end to end without needing a database, a
provider key, or a network. Everything real is real except the model itself,
which replays a fixed script — because the point is to exercise the wiring, and
a live model would make the demo depend on the weather.

    python scripts/demo.py            # start the server on :8000
    python scripts/demo.py --probe    # start it, drive it, print the stream
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kairos.core.catalog import (  # noqa: E402
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
from kairos.core.reasoning.engine import ModelReply, ReasoningEngine  # noqa: E402
from kairos.core.reasoning.turn import ToolOutcome, ToolRequest  # noqa: E402
from kairos.core.tools import Exposure, ToolDefinition, ToolRegistry  # noqa: E402
from kairos.runtime.app import create_app, dependency_overrides_for  # noqa: E402
from kairos.runtime.settings import AuthSettings, Deployment, Settings  # noqa: E402

THREAD_ID = UUID("11111111-1111-1111-1111-111111111111")

CATALOG = Catalog(
    providers=[
        ProviderDescriptor(
            id=ProviderId("demo"),
            display_name="Demo",
            endpoint=Endpoint(wire=Wire.OPENAI_CHAT, credential_env="DEMO_KEY"),
        )
    ],
    models=[
        ModelDescriptor(
            id=ModelId("demo-large"),
            remote_id="demo-large",
            provider=ProviderId("demo"),
            budget=TokenBudget(context=200_000, max_output=8_000),
            capabilities=Capability.baseline() | Capability.VISION,
        ),
        ModelDescriptor(
            id=ModelId("demo-small"),
            remote_id="demo-small",
            provider=ProviderId("demo"),
            budget=TokenBudget(context=32_000, max_output=2_000),
            capabilities=Capability.baseline(),
        ),
    ],
)


class Thread:
    """The one thread this demo serves."""

    def __init__(self) -> None:
        self.id = THREAD_ID
        self.title = "Demo thread"
        self.workspace_id = uuid4()
        self.updated_at = datetime.now(UTC)


class Threads:
    def __init__(self) -> None:
        self._thread = Thread()

    async def get(self, thread_id: UUID) -> Thread | None:
        return self._thread if thread_id == THREAD_ID else None

    async def list_own(self, *, limit: int = 50, offset: int = 0) -> list[Thread]:
        return [self._thread]

    async def remove(self, thread_id: UUID) -> None:
        pass


class Tools:
    """A tool that answers instantly, so the demo needs nothing outside itself."""

    async def run(self, request: ToolRequest) -> ToolOutcome:
        await asyncio.sleep(0.15)  # visible in the stream, so ordering is observable
        return ToolOutcome(
            call_id=request.call_id,
            ok=True,
            summary=f"{request.name} returned 3 results",
        )


class Transcripts:
    async def for_thread(self, thread_id: UUID):  # type: ignore[no-untyped-def]
        return None


class Repositories:
    def __init__(self) -> None:
        self.threads = Threads()
        self.tools = Tools()
        self.transcripts = Transcripts()


class ScriptedModel:
    """Two rounds: ask for a tool, then answer using its result."""

    def __init__(self) -> None:
        self._round = 0

    async def call(self, messages, tools):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.2)
        self._round += 1
        if self._round == 1:
            return ModelReply(
                reasoning="The question needs current data; searching first.",
                tool_requests=(
                    ToolRequest(
                        call_id="call-1",
                        name="search",
                        arguments={"query": "quarterly revenue"},
                    ),
                ),
                input_tokens=420,
                output_tokens=35,
                model_id="demo-large",
            )
        return ModelReply(
            text="Revenue grew 12% quarter over quarter, driven by services.",
            input_tokens=510,
            output_tokens=88,
            model_id="demo-large",
        )


def build_app():  # type: ignore[no-untyped-def]
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search",
            description="Search filings and news",
            exposure=Exposure.DIRECT,
        )
    )

    app = create_app(
        settings=Settings(deployment=Deployment.SOLO, auth=AuthSettings()),
        catalog=CATALOG,
    )
    dependency_overrides_for(
        app,
        engine=ReasoningEngine(ScriptedModel(), registry),
        repositories=Repositories(),
    )
    return app


def probe() -> int:
    """Drive the app in-process and print what a client would receive."""
    from fastapi.testclient import TestClient

    client = TestClient(build_app())

    print("── health ─────────────────────────────────────────────")
    print(client.get("/health").json())

    print("\n── threads ────────────────────────────────────────────")
    print(client.get("/api/v1/threads").json())

    print("\n── streamed answer ────────────────────────────────────")
    with client.stream(
        "POST",
        f"/api/v1/threads/{THREAD_ID}/messages",
        json={"prompt": "How did revenue do last quarter?"},
    ) as response:
        print(f"status      {response.status_code}")
        print(f"content-type {response.headers['content-type']}")
        print(f"reconnect   {response.headers['content-location']}\n")
        for line in response.iter_lines():
            if line:
                print(f"  {line}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="drive it and exit")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()

    if arguments.probe:
        return probe()

    import uvicorn

    print(f"http://127.0.0.1:{arguments.port}/docs")
    print(f"thread id: {THREAD_ID}")
    uvicorn.run(build_app(), host="127.0.0.1", port=arguments.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
