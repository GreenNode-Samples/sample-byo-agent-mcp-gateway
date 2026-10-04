"""Fake chat model for hermetic agent tests (no LLM endpoint is ever called)."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
Script = Callable[[list[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Answers with `script(messages)`. Streams the text word by word and the tool calls as chunks.

    `seen` records the messages of every model call, newest last.
    """

    script: Any
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        reply = self.script(messages)
        reply = reply.model_copy(update={"usage_metadata": USAGE, "response_metadata": {"model_name": "fake"}})
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        reply = self.script(messages)
        words = reply.content.split(" ") if reply.content else []
        for i, word in enumerate(words):
            yield ChatGenerationChunk(message=AIMessageChunk(content=(" " if i else "") + word))
        calls = [
            {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
            for i, c in enumerate(reply.tool_calls)
        ]
        last = AIMessageChunk(
            content="",
            tool_call_chunks=calls,
            usage_metadata=USAGE,
            response_metadata={"model_name": "fake"},
            chunk_position="last",
        )
        yield ChatGenerationChunk(message=last)


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def calls_then_answer(calls: list[tuple[str, dict]], answer: str = "final answer") -> Script:
    """Call the given tools one after another (one per model call), then answer.

    Progress is counted from the last human message, so the script also works in a multi-turn chat.
    """

    def script(messages: list[BaseMessage]) -> AIMessage:
        done = sum(isinstance(m, ToolMessage) for m in itertools.takewhile(
            lambda m: not isinstance(m, HumanMessage), reversed(messages)))
        if done < len(calls):
            name, args = calls[done]
            return tool_call(name, args, call_id=f"call_{len(messages)}")
        return AIMessage(content=answer)

    return script
