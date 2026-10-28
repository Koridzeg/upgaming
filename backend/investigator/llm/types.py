"""Provider-independent message, tool and response types (the OpenAI chat format)."""

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the model produced invalid JSON
    raw_arguments: str = ""
    error: str | None = None  # why `arguments` is None; sent back to the model to fix

    def to_openai(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.raw_arguments},
        }


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)  # assistant messages
    tool_call_id: str | None = None  # tool messages: which call this result answers

    def to_openai(self) -> dict:
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [call.to_openai() for call in self.tool_calls]
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id
        return message


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: Usage
    provider: str
    model: str
    latency_ms: float

    def as_message(self) -> Message:
        """The response as the assistant message to append to the conversation."""
        return Message("assistant", self.content, self.tool_calls)
