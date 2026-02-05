from dataclasses import dataclass
from enum import Enum


@dataclass
class RewardPolicy:
    parse_penalty: float = 0.0
    success_reward: float = 1.0
    failure_penalty: float = 0.0


@dataclass
class Turn:
    t: int
    role: str
    screenshot: str | None
    reasoning: str | None
    content: str
    action: dict | None
    reward: float | None


class TerminationReason(str, Enum):
    FAIL = "Fail"
    DONE = "Done"
    TRUNCATED = "Truncated"


class EnvRuntimeError(RuntimeError):
    pass


@dataclass
class RolloutResult:
    turns: list[Turn]
    task_id: str
    termination_reason: TerminationReason
    final_reward: float | None
