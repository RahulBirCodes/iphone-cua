from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class VMInfo:
    vm_id: str
    vm_ip: str
    agent_port: int


@dataclass
class Turn:
    t: int
    role: str
    screenshot: str | None
    raw_output: str | None
    action: dict | None
    reward: float | None


class TerminationReason(str, Enum):
    FAIL = "Fail"
    DONE = "Done"
    TRUNCATED = "Truncated"


@dataclass
class RolloutResult:
    turns: list[Turn]
    task_id: str
    termination_reason: TerminationReason
    final_reward: float | None
