from __future__ import annotations

from typing import Any, Callable, Iterable

import ray

from .schemas import GenerateResult


class InferenceActor:
    def __init__(
        self,
        parse_model_output: Callable[[str], tuple[str | None, str]],
    ):
        self._parse_model_output = parse_model_output

    def format_messages(self, messages: Iterable[dict[str, Any]]) -> str:
        parts = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            parts.append(f"{role}: {content}")
        parts.append("assistant:")
        return "\n".join(parts)

    @staticmethod
    def _extract_reasoning_from_content(content: str) -> tuple[str | None, str]:
        """Extract reasoning from assistant content using Qwen's template logic."""
        if "</think>" not in content:
            return (None, content)

        reasoning_prefix, _, content_suffix = content.partition("</think>")
        reasoning = reasoning_prefix.rstrip("\n")
        if "<think>" in reasoning:
            reasoning = reasoning.split("<think>")[-1]
        reasoning = reasoning.lstrip("\n")
        content = content_suffix.lstrip("\n")
        return (reasoning or None, content)

    def _turns_to_messages(self, turns: list[Any]) -> list[dict[str, Any]]:
        messages = []
        for turn in turns:
            role = turn.role

            if role == "system":
                messages.append({"role": "system", "content": turn.content or ""})

            elif role == "user":
                if turn.screenshot:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": turn.content or ""},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{turn.screenshot}"
                                    },
                                },
                            ],
                        }
                    )
                else:
                    messages.append({"role": "user", "content": turn.content or ""})

            elif role == "assistant":
                content = turn.content or ""
                extracted_reasoning, content = self._extract_reasoning_from_content(
                    content
                )
                reasoning = turn.reasoning or extracted_reasoning

                message: dict[str, Any] = {"role": "assistant", "content": content}
                if reasoning:
                    # Keep reasoning separate so tokenizer chat templates can decide
                    # how/when to serialize <think> blocks.
                    message["reasoning_content"] = reasoning
                messages.append(message)

        return messages

    async def generate(
        self,
        turn_refs: list[ray.ObjectRef],
        sampling: dict[str, Any],
    ) -> GenerateResult:
        raise NotImplementedError
