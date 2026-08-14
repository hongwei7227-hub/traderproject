"""Talking to providers.

Three wire protocols cover every provider worth supporting. Vendors that are
not OpenAI or Anthropic almost always expose one of their shapes rather than
invent a third, so the useful axis is the protocol, not the brand — a vendor
can offer its metered endpoint in one shape and its subscription endpoint in
another, and treating those as one thing is how a client ends up posting an
OpenAI body to an Anthropic path.

Requests are built here and sent by a caller that owns the HTTP client, so that
retry, circuit breaking and timeouts stay in one place instead of being
reimplemented per protocol.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from kairos.adapters.llm.credentials import Credential
from kairos.core.catalog.descriptors import Capability, ModelDescriptor, Wire


class Speaker(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of conversation, before it is shaped for a provider."""

    speaker: Speaker
    text: str
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as offered to the model."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Turn:
    """What to ask a provider for."""

    messages: Sequence[Message]
    tools: Sequence[ToolSpec] = ()
    max_output_tokens: int | None = None
    temperature: float | None = None
    stream: bool = True

    def system_text(self) -> str:
        """System messages, merged.

        Anthropic takes a single top-level system field rather than a message,
        so several have to become one. Merging here keeps that from being a
        surprise the Anthropic encoder has to handle alone.
        """
        return "\n\n".join(m.text for m in self.messages if m.speaker is Speaker.SYSTEM)

    def conversation(self) -> Iterator[Message]:
        return (m for m in self.messages if m.speaker is not Speaker.SYSTEM)


@dataclass(frozen=True, slots=True)
class Request:
    """A ready-to-send HTTP request, protocol already applied."""

    url: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]
    timeout_seconds: float = 600.0


class Encoder(Protocol):
    """Shapes a turn for one wire protocol."""

    def encode(
        self, turn: Turn, model: ModelDescriptor, credential: Credential
    ) -> Request: ...


def _base(credential: Credential, fallback: str) -> str:
    return (credential.base_url or fallback).rstrip("/")


def _tools_supported(model: ModelDescriptor, turn: Turn) -> bool:
    """Whether to offer tools at all.

    Sending a tool list to a model that cannot use them is rejected by some
    providers and silently ignored by others. Checking the declared capability
    turns both into the same predictable behaviour.
    """
    return bool(turn.tools) and model.supports(Capability.TOOL_CALLING)


class OpenAIChat:
    """The `/chat/completions` shape.

    The de facto default: local runtimes and most gateways implement it, which
    is why a self-hosted model and a hosted one differ here by a base URL and
    nothing else.
    """

    default_base_url = "https://api.openai.com/v1"

    def encode(
        self, turn: Turn, model: ModelDescriptor, credential: Credential
    ) -> Request:
        body: dict[str, Any] = {
            "model": model.remote_id,
            "messages": [self._message(m) for m in turn.messages],
            "stream": turn.stream,
            **model.params,
        }
        if turn.max_output_tokens is not None:
            body["max_tokens"] = turn.max_output_tokens
        if turn.temperature is not None:
            body["temperature"] = turn.temperature
        if _tools_supported(model, turn):
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": dict(t.parameters),
                    },
                }
                for t in turn.tools
            ]
        if turn.stream:
            # Without this, usage is omitted from streamed responses entirely
            # and a streamed turn contributes nothing to the tenant's meter.
            body["stream_options"] = {"include_usage": True}

        return Request(
            url=f"{_base(credential, self.default_base_url)}/chat/completions",
            headers={
                "Authorization": f"Bearer {credential.secret}",
                "Content-Type": "application/json",
            },
            body=body,
        )

    @staticmethod
    def _message(message: Message) -> dict[str, Any]:
        shaped: dict[str, Any] = {
            "role": str(message.speaker),
            "content": message.text,
        }
        if message.speaker is Speaker.TOOL and message.tool_call_id:
            shaped["tool_call_id"] = message.tool_call_id
        if message.name:
            shaped["name"] = message.name
        return shaped


class OpenAIResponses:
    """The `/responses` shape.

    Newer, and the only route to some models. Differs from chat completions in
    more than its path — the system prompt is a separate field, and reasoning
    configuration lives at the top level.
    """

    default_base_url = "https://api.openai.com/v1"

    def encode(
        self, turn: Turn, model: ModelDescriptor, credential: Credential
    ) -> Request:
        body: dict[str, Any] = {
            "model": model.remote_id,
            "input": [
                {"role": str(m.speaker), "content": m.text} for m in turn.conversation()
            ],
            "stream": turn.stream,
            **model.params,
        }
        if system := turn.system_text():
            body["instructions"] = system
        if turn.max_output_tokens is not None:
            body["max_output_tokens"] = turn.max_output_tokens
        if _tools_supported(model, turn):
            body["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": dict(t.parameters),
                }
                for t in turn.tools
            ]

        return Request(
            url=f"{_base(credential, self.default_base_url)}/responses",
            headers={
                "Authorization": f"Bearer {credential.secret}",
                "Content-Type": "application/json",
            },
            body=body,
        )


class AnthropicMessages:
    """The `/v1/messages` shape.

    Several vendors expose their subscription or coding endpoints in this
    shape regardless of whose model is behind it, so this encoder sees more
    than one brand.
    """

    default_base_url = "https://api.anthropic.com"
    api_version = "2023-06-01"

    def encode(
        self, turn: Turn, model: ModelDescriptor, credential: Credential
    ) -> Request:
        body: dict[str, Any] = {
            "model": model.remote_id,
            "messages": [
                {"role": str(m.speaker), "content": m.text} for m in turn.conversation()
            ],
            "stream": turn.stream,
            # Required rather than optional here, so a caller that omits it
            # gets the model's own ceiling instead of a rejected request.
            "max_tokens": turn.max_output_tokens or model.budget.max_output,
            **model.params,
        }
        if system := turn.system_text():
            body["system"] = system
        if turn.temperature is not None:
            body["temperature"] = turn.temperature
        if _tools_supported(model, turn):
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": dict(t.parameters),
                }
                for t in turn.tools
            ]

        return Request(
            url=f"{_base(credential, self.default_base_url)}/v1/messages",
            headers={
                # Not a bearer token: this protocol authenticates with its own
                # header, and sending Authorization instead simply fails.
                "x-api-key": credential.secret,
                "anthropic-version": self.api_version,
                "Content-Type": "application/json",
            },
            body=body,
        )


class GeminiGenerate:
    """The `generateContent` shape."""

    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def encode(
        self, turn: Turn, model: ModelDescriptor, credential: Credential
    ) -> Request:
        body: dict[str, Any] = {
            "contents": [
                {
                    # This protocol names the assistant "model", and using the
                    # wrong word is accepted and then ignored.
                    "role": "model" if m.speaker is Speaker.ASSISTANT else "user",
                    "parts": [{"text": m.text}],
                }
                for m in turn.conversation()
            ],
            **model.params,
        }
        if system := turn.system_text():
            body["systemInstruction"] = {"parts": [{"text": system}]}

        generation: dict[str, Any] = {}
        if turn.max_output_tokens is not None:
            generation["maxOutputTokens"] = turn.max_output_tokens
        if turn.temperature is not None:
            generation["temperature"] = turn.temperature
        if generation:
            body["generationConfig"] = generation

        if _tools_supported(model, turn):
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": dict(t.parameters),
                        }
                        for t in turn.tools
                    ]
                }
            ]

        verb = "streamGenerateContent" if turn.stream else "generateContent"
        return Request(
            url=(
                f"{_base(credential, self.default_base_url)}"
                f"/models/{model.remote_id}:{verb}"
            ),
            headers={
                "x-goog-api-key": credential.secret,
                "Content-Type": "application/json",
            },
            body=body,
        )


ENCODERS: Mapping[Wire, Encoder] = {
    Wire.OPENAI_CHAT: OpenAIChat(),
    Wire.OPENAI_RESPONSES: OpenAIResponses(),
    Wire.ANTHROPIC_MESSAGES: AnthropicMessages(),
    Wire.GEMINI_GENERATE: GeminiGenerate(),
}


def encoder_for(wire: Wire) -> Encoder:
    try:
        return ENCODERS[wire]
    except KeyError:  # pragma: no cover — the enum is closed
        raise ValueError(f"no encoder for wire protocol {wire!r}") from None


@dataclass(frozen=True, slots=True)
class Usage:
    """What a call consumed, normalised across protocols."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


def read_usage(wire: Wire, payload: Mapping[str, Any]) -> Usage:
    """Pull token counts out of a response.

    Every protocol reports these under a different name, and a turn whose usage
    was not understood is a turn the tenant is not billed for — so the mapping
    lives in one place rather than being re-guessed at each call site.
    """
    match wire:
        case Wire.OPENAI_CHAT | Wire.OPENAI_RESPONSES:
            usage = payload.get("usage") or {}
            return Usage(
                input_tokens=int(
                    usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                ),
                output_tokens=int(
                    usage.get("completion_tokens") or usage.get("output_tokens") or 0
                ),
            )
        case Wire.ANTHROPIC_MESSAGES:
            usage = payload.get("usage") or {}
            return Usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
            )
        case Wire.GEMINI_GENERATE:
            usage = payload.get("usageMetadata") or {}
            return Usage(
                input_tokens=int(usage.get("promptTokenCount") or 0),
                output_tokens=int(usage.get("candidatesTokenCount") or 0),
            )
    return Usage()


RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Refusals about the request itself. Retrying one sends the same rejected body
# again; falling back to another model is the only move that can help.
FATAL_STATUS: frozenset[int] = frozenset({400, 401, 403, 404, 405, 413, 422})


def is_retryable(status: int | None) -> bool:
    """Whether a failure is worth trying again.

    An unknown status — a connection reset, a timeout with no response — counts
    as retryable. Those are the failures most likely to be transient, and
    treating them as fatal would give up on the cheapest thing to recover from.
    """
    if status is None:
        return True
    if status in FATAL_STATUS:
        return False
    return status in RETRYABLE_STATUS or status >= 500


@dataclass(slots=True)
class Attempt:
    """The record of one call against one provider."""

    model_id: str
    provider_id: str
    status: int | None = None
    error: str = ""
    usage: Usage = field(default_factory=Usage)

    @property
    def succeeded(self) -> bool:
        return self.status is not None and 200 <= self.status < 300
