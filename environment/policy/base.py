from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from environment.schemas import Turn


@dataclass
class PolicyOutput:
    """Normalized single completion produced by a policy backend.

    The environment executes exactly one action per inference step, so the
    abstraction exposes one normalized completion rather than a provider-native
    response envelope.
    """

    content: str
    reasoning: str | None = None
    finish_reason: str | None = None
    token_ids: list[int] | None = None
    token_logprobs: list[float] | None = None
    backend_name: str | None = None
    raw_response: Any | None = None


class PolicyBackend(Protocol):
    def generate(
        self,
        turns: Sequence[Turn],
        sampling: Mapping[str, Any],
    ) -> PolicyOutput:
        """Generate one completion from the current rollout transcript."""
        ...
