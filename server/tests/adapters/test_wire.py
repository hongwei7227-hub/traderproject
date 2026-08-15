"""Protocol shaping. Pure functions, so worth pinning down without a network."""

from __future__ import annotations

import pytest

from kairos.adapters.llm.credentials import Credential, Payer
from kairos.adapters.llm.wire import (
    Message,
    Speaker,
    ToolSpec,
    Turn,
    Usage,
    encoder_for,
    is_retryable,
    read_usage,
)
from kairos.core.catalog import (
    Capability,
    ModelDescriptor,
    ModelId,
    ProviderId,
    TokenBudget,
    Wire,
)

SEARCH = ToolSpec(
    name="search",
    description="Search filings",
    parameters={"type": "object", "properties": {"q": {"type": "string"}}},
)


def model(*, caps: Capability = Capability.baseline(), remote: str = "m-1") -> ModelDescriptor:
    return ModelDescriptor(
        id=ModelId("model"),
        remote_id=remote,
        provider=ProviderId("vendor"),
        budget=TokenBudget(context=200_000, max_output=8_000),
        capabilities=caps,
    )


def credential(base_url: str | None = None) -> Credential:
    return Credential(
        secret="k-123", payer=Payer.TENANT, provider=ProviderId("vendor"), base_url=base_url
    )


def turn(**kwargs) -> Turn:  # type: ignore[no-untyped-def]
    kwargs.setdefault(
        "messages",
        [
            Message(speaker=Speaker.SYSTEM, text="Be brief."),
            Message(speaker=Speaker.USER, text="Hello"),
        ],
    )
    return Turn(**kwargs)


class TestOpenAIChat:
    def test_path_and_authorization(self) -> None:
        request = encoder_for(Wire.OPENAI_CHAT).encode(turn(), model(), credential())
        assert request.url.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer k-123"

    def test_the_system_message_stays_in_the_list(self) -> None:
        # This protocol carries it as a message; only Anthropic hoists it out.
        request = encoder_for(Wire.OPENAI_CHAT).encode(turn(), model(), credential())
        assert request.body["messages"][0]["role"] == "system"

    def test_streaming_asks_for_usage(self) -> None:
        """Without it, a streamed turn contributes nothing to the meter."""
        request = encoder_for(Wire.OPENAI_CHAT).encode(
            turn(stream=True), model(), credential()
        )
        assert request.body["stream_options"] == {"include_usage": True}

    def test_a_tenant_base_url_replaces_the_default(self) -> None:
        request = encoder_for(Wire.OPENAI_CHAT).encode(
            turn(), model(), credential("https://private.example/v1/")
        )
        assert request.url == "https://private.example/v1/chat/completions"


class TestAnthropicMessages:
    def test_it_authenticates_with_its_own_header(self) -> None:
        """Sending Authorization instead simply fails."""
        request = encoder_for(Wire.ANTHROPIC_MESSAGES).encode(
            turn(), model(), credential()
        )
        assert request.headers["x-api-key"] == "k-123"
        assert "Authorization" not in request.headers

    def test_the_system_prompt_is_hoisted_out_of_the_messages(self) -> None:
        request = encoder_for(Wire.ANTHROPIC_MESSAGES).encode(
            turn(), model(), credential()
        )
        assert request.body["system"] == "Be brief."
        assert all(m["role"] != "system" for m in request.body["messages"])

    def test_several_system_messages_merge_into_one(self) -> None:
        # The protocol takes a single field, so more than one has to become one.
        request = encoder_for(Wire.ANTHROPIC_MESSAGES).encode(
            turn(
                messages=[
                    Message(speaker=Speaker.SYSTEM, text="First."),
                    Message(speaker=Speaker.SYSTEM, text="Second."),
                    Message(speaker=Speaker.USER, text="Hi"),
                ]
            ),
            model(),
            credential(),
        )
        assert request.body["system"] == "First.\n\nSecond."

    def test_max_tokens_falls_back_to_the_model_ceiling(self) -> None:
        """Required here, so omitting it would have the request rejected."""
        request = encoder_for(Wire.ANTHROPIC_MESSAGES).encode(
            turn(), model(), credential()
        )
        assert request.body["max_tokens"] == 8_000


class TestGeminiGenerate:
    def test_the_assistant_is_called_model(self) -> None:
        # Using the wrong word is accepted and then ignored.
        request = encoder_for(Wire.GEMINI_GENERATE).encode(
            turn(
                messages=[
                    Message(speaker=Speaker.USER, text="Hi"),
                    Message(speaker=Speaker.ASSISTANT, text="Hello"),
                ]
            ),
            model(),
            credential(),
        )
        assert [c["role"] for c in request.body["contents"]] == ["user", "model"]

    def test_streaming_changes_the_verb(self) -> None:
        streaming = encoder_for(Wire.GEMINI_GENERATE).encode(
            turn(stream=True), model(), credential()
        )
        assert streaming.url.endswith(":streamGenerateContent")


class TestToolOffering:
    @pytest.mark.parametrize(
        "wire", [Wire.OPENAI_CHAT, Wire.ANTHROPIC_MESSAGES, Wire.GEMINI_GENERATE]
    )
    def test_tools_are_offered_when_the_model_supports_them(self, wire: Wire) -> None:
        request = encoder_for(wire).encode(turn(tools=[SEARCH]), model(), credential())
        assert "tools" in request.body

    @pytest.mark.parametrize(
        "wire", [Wire.OPENAI_CHAT, Wire.ANTHROPIC_MESSAGES, Wire.GEMINI_GENERATE]
    )
    def test_tools_are_withheld_from_a_model_that_cannot_use_them(
        self, wire: Wire
    ) -> None:
        """Some providers reject the list, others ignore it. Neither is useful."""
        incapable = ModelDescriptor(
            id=ModelId("plain"),
            remote_id="plain",
            provider=ProviderId("vendor"),
            budget=TokenBudget(context=8_000, max_output=1_000),
            capabilities=Capability.TEXT | Capability.STREAMING,
            selectable=False,
        )
        request = encoder_for(wire).encode(turn(tools=[SEARCH]), incapable, credential())
        assert "tools" not in request.body


class TestUsageReading:
    def test_openai_naming(self) -> None:
        assert read_usage(
            Wire.OPENAI_CHAT, {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        ) == Usage(10, 5)

    def test_responses_naming(self) -> None:
        assert read_usage(
            Wire.OPENAI_RESPONSES, {"usage": {"input_tokens": 10, "output_tokens": 5}}
        ) == Usage(10, 5)

    def test_anthropic_naming(self) -> None:
        assert read_usage(
            Wire.ANTHROPIC_MESSAGES, {"usage": {"input_tokens": 7, "output_tokens": 3}}
        ) == Usage(7, 3)

    def test_gemini_naming(self) -> None:
        assert read_usage(
            Wire.GEMINI_GENERATE,
            {"usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}},
        ) == Usage(4, 2)

    def test_a_response_without_usage_reports_nothing(self) -> None:
        # A turn whose usage was not understood is a turn nobody is billed for,
        # so this returning zero rather than raising is the deliberate choice.
        assert read_usage(Wire.OPENAI_CHAT, {}) == Usage(0, 0)


class TestRetryClassification:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 408])
    def test_transient_failures_are_retryable(self, status: int) -> None:
        assert is_retryable(status)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_refusals_about_the_request_are_not(self, status: int) -> None:
        # Retrying sends the same rejected body again; only a different model
        # can help.
        assert not is_retryable(status)

    def test_no_status_at_all_is_retryable(self) -> None:
        """A reset or a timeout is the cheapest kind of failure to recover from."""
        assert is_retryable(None)
