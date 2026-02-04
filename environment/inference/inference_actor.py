from __future__ import annotations

from typing import Any, Iterable


def default_chat_template(messages: Iterable[dict[str, Any]]) -> str:
    parts = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        parts.append(f"{role}: {content}")
    parts.append("assistant:")
    return "\n".join(parts)


class InferenceActor:
    def format_messages(self, messages: Iterable[dict[str, Any]]) -> str:
        return default_chat_template(messages)

    async def generate(
        self,
        prompt: str | None,
        sampling: dict[str, Any],
        messages: Iterable[dict[str, Any]] | None = None,
    ) -> str:
        raise NotImplementedError
