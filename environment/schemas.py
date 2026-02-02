from dataclasses import dataclass


@dataclass(frozen=True)
class VMInfo:
    vm_id: str
    vm_ip: str
    agent_port: int


@dataclass
class Turn:
    role: str
    screenshot: str | None
    raw_output: str | None
    action: dict | None
    reward: float | None


@dataclass
class RolloutResult:
    turns: list[Turn]
    task_id: str
