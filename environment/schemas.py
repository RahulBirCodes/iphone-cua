from dataclasses import dataclass
from enum import Enum


@dataclass
class ParsedOutput:
    reasoning: str
    content: str
    action: dict | None


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
    raw_output: str | None
    parsed_output: "ParsedOutput | None"
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
